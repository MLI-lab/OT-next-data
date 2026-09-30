"""Run packaged InferredBugs tasks' own verifier (tests/test.sh) in their runtime image on Helma.

Per task and variant the container gets what a task container has: /setup_files, /tests, an empty
/app, and a writable root (apptainer --fakeroot --overlay). Inside it runs setup_repository.sh, puts
the variant's target file in /app and runs test.sh.
  buggy  the task's buggy file: the verifier must answer 0 because the warning remains
  gold   the historical fixed file alone: 1 where it compiles without the fix's other files
  full   the complete historical fix: /app is the fixing commit's tree.
         Must be 1: it is the proof that the task has a solution
  agent  what the instruction offers the agent: build_setup.sh, install_analyzer.sh and analyze.sh
         in /app with the buggy file; the printed warnings must include the task's

Cluster specifics (proxy, Maven settings helper, Central mirror, apt under single-uid fakeroot) are
applied here, not in the packaged task.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
from types import SimpleNamespace
import urllib.parse

repo = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('inferredbugs_patcher', repo / 'data/inferredbugs/patch_tasktrove_inferredbugs_v3.py')
patcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = patcher
spec.loader.exec_module(patcher)
LOCK = threading.Lock()
DONE = {}


def run_variant(a, task_id, row, meta, files, before, after, variant):
    out = Path(a.output) / task_id / variant
    if (out / 'summary.json').exists():
        return json.loads((out / 'summary.json').read_text())
    if (task_id, variant) in DONE:
        return DONE[task_id, variant]
    work = Path(a.scratch) / task_id / variant
    shutil.rmtree(work, ignore_errors=True)
    for name in ('app', 'workspace', 'cache', 'logs', 'tmp', 'overlay', 'fixture'):
        (work / name).mkdir(parents=True)
    for name, data in files.items():
        if name.startswith(('tests/', 'setup_files/', 'solution/')):
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    mirror = os.environ.get('INFERREDBUGS_CENTRAL_MIRROR')
    for group in ('tests', 'setup_files'):
        if row['language'] == 'java':
            subprocess.run([sys.executable, a.proxy_helper, str(work / group / 'audit-settings.xml')], check=True)
        for script in (work / group).glob('build*.sh'):
            if mirror:
                script.write_text(re.sub(r'https?://repo1?\.maven(?:\.apache)?\.org/maven2/?', mirror.rstrip('/') + '/', script.read_text()))
    shutil.copy2(a.proxy_helper, work / 'fixture/configure_maven_proxy.py')
    (work / 'fixture/target').write_bytes(after if variant == 'gold' else before)
    target = row['target_file']
    options = '-Xmx3g -Duser.home=/tmp/home -Dhttp.nonProxyHosts=localhost|127.0.0.1'
    env = ['HOME=/tmp/home', 'TMPDIR=/tmp', 'INFERREDBUGS_MAVEN_SETTINGS_HELPER=/fixture/configure_maven_proxy.py']
    for protocol in ('http', 'https'):
        value = os.environ.get(protocol + '_proxy', '')
        endpoint = urllib.parse.urlsplit(value)
        if endpoint.hostname:
            options += ' -D%s.proxyHost=%s -D%s.proxyPort=%s' % (protocol, endpoint.hostname, protocol, endpoint.port or 80)
            env += ['%s_proxy=%s' % (protocol, value), '%s_PROXY=%s' % (protocol.upper(), value)]
    if row['language'] == 'java':
        env.append('JAVA_TOOL_OPTIONS=' + options)
    env += ['%s=%s' % (k, v) for k, v in os.environ.items() if k in ('INFERREDBUGS_WHOLE_PROJECT', 'INFERREDBUGS_ANALYSIS_JOBS', 'INFERREDBUGS_ANALYZE_OPTIONS', 'CLR_OPENSSL_VERSION_OVERRIDE')]
    caches = {'INFERREDBUGS_DOWNLOAD_CACHE': '/warm/downloads', 'INFERREDBUGS_REPOSITORY_CACHE': '/warm/repositories'}
    shell = ('mkdir -p /tmp/home /etc/apt/apt.conf.d && echo \'APT::Sandbox::User "root";\' > /etc/apt/apt.conf.d/99inferredbugs-check; '
             'set -e; bash /setup_files/setup_repository.sh > /logs/setup_repository.log 2>&1; '
             'cp /fixture/target ' + patcher.shlex.quote('/app/' + target) + '; '
             'set +e; timeout %d bash /tests/test.sh > /logs/test.log 2>&1; echo $? > /logs/test.exit' % a.timeout)
    if variant == 'full':
        # the task's own oracle: solution/solve.sh replaces /app by the fixing commit's tree
        shell = shell.replace('cp /fixture/target ' + patcher.shlex.quote('/app/' + target) + '; ', 'bash /solution/solve.sh > /logs/solve.log 2>&1; ')
    if variant == 'agent':
        shell = shell.split('set +e;')[0] + ('set +e; { bash /setup_files/build_setup.sh /app && bash /setup_files/install_analyzer.sh && bash /setup_files/install_analyzer.sh '
                                             '&& timeout %d bash /setup_files/analyze.sh; } > /logs/test.log 2>&1; echo $? > /logs/test.exit' % a.timeout)
    binds = ['%s/%s:/%s' % (work, n, n) for n in ('app', 'workspace', 'logs', 'tmp')]
    binds.append('%s:/cache' % (a.shared_cache or work / 'cache'))
    for variable, mount in caches.items():
        if os.environ.get(variable):
            binds.append('%s:%s:ro' % (os.environ[variable], mount))
            env.append('%s=%s' % (variable, mount))
    binds += ['%s/tests:/tests:ro' % work, '%s/setup_files:/setup_files:ro' % work, '%s/solution:/solution:ro' % work, '%s/fixture:/fixture:ro' % work, '/etc/resolv.conf:/etc/resolv.conf:ro']
    image = Path(a.images) / ((os.environ.get('CHECK_IMAGE') or meta['runtime_key']) + '.sif')   # CHECK_IMAGE: try another runtime image
    command = ['apptainer', 'exec', '--fakeroot', '--containall', '--no-home', '--overlay', str(work / 'overlay'), '--pwd', '/app',
               '--bind', ','.join(binds), str(image), 'env', *env, 'bash', '-c', shell]
    start = time.monotonic()
    with (work / 'logs/container.log').open('wb') as log:
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
    logs = work / 'logs'
    read = lambda p: p.read_text(errors='replace').strip() if p.exists() else None
    result = json.loads(read(logs / 'verifier/result.json') or read(work / 'tmp/inferredbugs-analysis/result.json') or '{}')
    summary = {'task_id': task_id, 'variant': variant, 'analyzer': patcher.task_warnings(task_id)['analyzer'], 'container_exit': code,
               'seconds': round(time.monotonic() - start), 'test_exit': read(logs / 'test.exit'), 'reward': read(logs / 'verifier/reward.txt'),
               'status': result.get('status'), 'steps': result.get('steps'), 'compiles': result.get('compiles'),
               'original_warning_remains': result.get('original_warning_remains'), 'new_warnings': result.get('new_warnings'),
               'changed_files': result.get('changed_files'),
               'warnings': [[w['file'], w['hash']] for w in result.get('warnings', [])] if os.environ.get('INFERREDBUGS_WHOLE_PROJECT') == '1' else None,
               'warning_hashes': sorted(w['hash'] for w in result.get('warnings', [])) if 'warnings' in result else None}
    if variant == 'agent':
        summary['output'] = (read(logs / 'test.log') or '')[-3000:]
    shutil.rmtree(logs / 'verifier/infer', ignore_errors=True)
    expected = {'buggy': ('original_warning_remains',), 'gold': ('passed', 'build_failed'), 'full': ('passed',), 'agent': ('analyzed',)}[variant]
    if a.summary_file:
        # A full run: one line per verification, logs only where the outcome needs a look.
        if summary['status'] not in expected:
            (Path(a.output) / 'logs').mkdir(exist_ok=True)
            with tarfile.open(Path(a.output) / 'logs' / ('%s-%s.tar.gz' % (task_id, variant)), 'w:gz') as tar:
                tar.add(logs, arcname='logs')
        with LOCK, open(a.summary_file, 'a') as f:
            f.write(json.dumps(summary) + '\n')
    else:
        out.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out / 'logs.tar.gz', 'w:gz') as tar:
            tar.add(logs, arcname='logs')
        (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    subprocess.run(['chmod', '-R', 'u+rwX', str(work)], check=False)
    shutil.rmtree(work, ignore_errors=True)
    with LOCK:
        print(task_id, variant, summary['analyzer'], 'reward', summary['reward'], summary['status'], '%ds' % summary['seconds'], flush=True)
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('images', 'scratch', 'output', 'ids-file', 'proxy-helper'):
        ap.add_argument('--' + name, required=True)
    ap.add_argument('--inputs', help="the audit's inputs.sqlite (Helma); or --inferredbugs-root")
    ap.add_argument('--inferredbugs-root', help='a checkout of microsoft/InferredBugs at the pinned commit (warmup.py --source DIR)')
    ap.add_argument('--variants', default='buggy,gold')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--timeout', type=int, default=7200, help='seconds for one test.sh')
    ap.add_argument('--step-timeout', type=int, default=2400, help='the packaged verifier\'s per step budget')
    ap.add_argument('--summary-file', help='append one JSON line per verification here instead of a directory per task')
    ap.add_argument('--shared-cache', help='one /cache (Maven, NuGet) for every task instead of a fresh one each (see warmup.py)')
    ap.add_argument('--shard', type=int, default=0)
    ap.add_argument('--shards', type=int, default=1)
    a = ap.parse_args()
    ids = [x for x in Path(a.ids_file).read_text().split() if x][a.shard::a.shards]
    if a.summary_file and Path(a.summary_file).exists():
        for line in Path(a.summary_file).read_text().splitlines():
            row = json.loads(line)
            DONE[row['task_id'], row['variant']] = row
    rows = {r['task_id']: r for r in patcher.embedded_recipes()}
    db = sqlite3.connect('file:%s?immutable=1' % a.inputs, uri=True) if a.inputs else None
    jobs = []
    for task_id in ids:
        row = rows[task_id]
        if db:
            metadata, before, after = db.execute('SELECT metadata,before,after FROM tasks WHERE id=?', (task_id,)).fetchone()
            meta = json.loads(metadata)
        else:
            record = Path(a.inferredbugs_root) / 'inferredbugs' / row['language'] / row['project'] / str(row['bug_id'])
            before, after = (record / 'file_before.txt').read_bytes(), (record / 'file_after.txt').read_bytes()
            meta = {'runtime_key': patcher.resolve_image(row['image'], row['language']).removeprefix('inferredbugs-').replace(':', '-')}
        record = Path(a.scratch) / 'records' / task_id
        record.mkdir(parents=True, exist_ok=True)
        (record / 'file_before.txt').write_bytes(before)
        task = patcher.Task(task_id, row['language'], row['project'], str(row['bug_id']), record, row['commit'], row['target_file'],
                            row['file_after_sha256'], row['repository'], file_before_sha256=row.get('file_before_sha256', ''), parent=row.get('parent', ''))
        files = patcher.package({}, task, patcher.row_proposal(row), patcher.resolve_image(row['image'], row['language']), row, {},
                                SimpleNamespace(vendor_dir=None, vendor_url=patcher.DEFAULT_VENDOR_URL, verify_timeout=a.step_timeout))
        jobs += [(a, task_id, row, meta, files, before, after, v) for v in a.variants.split(',')]
    if db:
        db.close()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        results = list(pool.map(lambda j: run_variant(*j), jobs))
    print(json.dumps(dict(collections.Counter((r['variant'], r['reward'], r['status']) for r in results).most_common()), default=str, indent=1)
          if False else dict(collections.Counter((r['variant'], r['reward'], r['status']) for r in results)))


if __name__ == '__main__':
    main()
