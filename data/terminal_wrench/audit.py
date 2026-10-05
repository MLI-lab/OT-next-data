#!/usr/bin/env python3
"""Replay Terminal Wrench's recorded exploits, the oracle and a no-op against patched tasks.

Uses a local Docker daemon, outside Harbor, so it runs on a laptop or a Docker
host without the cluster stack. Per task it builds environment/ once, then for
each case starts a fresh container, applies the case, uploads tests/ and runs
tests/test.sh as Harbor's shared verifier does, reading reward.json before
reward.txt as Harbor does:

- ``nop``: nothing; expected reward 0.
- ``oracle``: solution/solve.sh; expected reward 1.
- ``exploit:<model>/<label>``: the shell keystrokes of a recorded trajectory
  that Terminal Wrench judged a rewarded serious exploit.

Keystrokes go as one stdin stream to an interactive login bash, like the
Terminus tmux pane: startup files run, syntax errors are not fatal, Ctrl-C
discards the line being typed, other tmux keys are skipped, and a program that
reads stdin consumes the lines typed after it. Each exploit is classified:

- ``reproduced``: reward 1, so the verifier accepts the exploit;
- ``blocked``: reward 0 after a replay that parsed and ran to the end;
- ``inconclusive``: anything else (no reward, timeout, a replay that did not
  parse or whose last command failed). It is no evidence either way.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib

TMUX_KEYS = re.compile(r'^(C-[a-z\\]|M-[a-z]|Escape|Enter|Tab|BSpace|Up|Down|Left|Right|q)$')
CA_ENV = ('SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE', 'PIP_CERT', 'NODE_EXTRA_CA_CERTS', 'GIT_SSL_CAINFO')
REWARDS = '/logs/verifier/reward.json /logs/verifier/reward.txt'


def incomplete(text):
    """True while bash would still wait for more input (open quote, heredoc, block)."""
    check = subprocess.run(['bash', '-n'], input=text, capture_output=True, text=True)
    return 'unexpected end of file' in check.stderr or 'unexpected EOF while looking for' in check.stderr


def keystrokes(trajectory):
    """Shell input of a recorded Terminus trajectory, in order.

    Text without a newline stays on the line being typed. Ctrl-C (``C-c`` or a
    raw ``\\x03``) discards that line, including an unfinished multi-line
    construct, as it does in the recorded pane."""
    out, pending = [], ''
    for step in json.loads(Path(trajectory).read_text()).get('steps', []):
        if step.get('source') != 'agent':
            continue
        for call in step.get('tool_calls') or []:
            text = (call.get('arguments') or {}).get('keystrokes')
            if call.get('function_name') != 'bash_command' or not isinstance(text, str):
                continue
            if text.strip() == 'C-c' or '\x03' in text:
                pending, text = '', text.rsplit('\x03', 1)[-1] if '\x03' in text else ''
            elif TMUX_KEYS.match(text.strip()):
                continue
            pending += text
            if pending.endswith('\n') and not incomplete(pending):
                out.append(pending)
                pending = ''
    return ''.join(out) + pending


def docker(*args, timeout=None, stdin=None):
    return subprocess.run(['docker', *args], input=stdin, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


def workdir(dockerfile):
    found = re.findall(r'^\s*WORKDIR\s+(\S+)', dockerfile.read_text(), re.M | re.I)
    return found[-1] if found else '/'


def with_ca_layer(dockerfile, layer):
    """Insert layer after every FROM instruction, never inside a heredoc body."""
    out, terminator = [], None
    for line in dockerfile.split('\n'):
        out.append(line)
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        heredoc = re.search(r"<<-?\s*([\"']?)(\w+)\1", line)
        if heredoc and re.match(r'\s*(RUN|COPY|ADD)\b', line, re.I):
            terminator = heredoc[2]
        elif re.match(r'\s*FROM\s', line):  # instructions are upper case in every task here
            out.append(layer)
    return '\n'.join(out)


def uv_packages(test_sh):
    """Every package the wrapper adds with `uv add`, pytest first."""
    packages = [p for line in re.findall(r'^\s*uv add (.+)$', test_sh, re.M)
                for p in shlex.split(line) if not p.startswith('-')]
    return ['pytest==8.4.1'] + [p for p in packages if not p.startswith('pytest==')]


def pip_setup(task):
    """Isolated venv with the wrapper's pinned pytest and extra `uv add` packages."""
    packages = ' '.join(shlex.quote(p) for p in uv_packages((task / 'tests/test.sh').read_text()))
    return ('umask 022; set -e; python3 -c "import venv, ensurepip" 2>/dev/null || { apt-get update -qq && '
            'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv >/dev/null; }; '
            f'python3 -m venv /opt/tw-verify && /opt/tw-verify/bin/pip install -q {packages}')


# Same reward mapping as the repaired wrapper, without its network bootstrap.
PIP_RUN = f'''umask 022; rm -rf {REWARDS}
/opt/tw-verify/bin/python -m pytest /tests/test_outputs.py -rA; rc=$?
rm -rf {REWARDS}
case "$rc" in 0) echo 1 > /logs/verifier/reward.txt ;; 1) echo 0 > /logs/verifier/reward.txt ;; *) exit "$rc" ;; esac'''


def read_reward(name):
    """Harbor's precedence: reward.json, then reward.txt."""
    found = docker('exec', name, 'cat', '/logs/verifier/reward.json', timeout=60)
    if found.returncode == 0:
        try:
            return float(json.loads(found.stdout)['reward']), 'reward.json'
        except (ValueError, KeyError, TypeError):
            return None, 'reward.json unreadable'
    found = docker('exec', name, 'cat', '/logs/verifier/reward.txt', timeout=60)
    if found.returncode == 0 and found.stdout.strip():
        try:
            return float(found.stdout.strip()), 'reward.txt'
        except ValueError:
            return None, 'reward.txt unreadable'
    return None, None


def run_case(image, task, case, script, timeouts, log, ca_bundle=None, pip_verifier=False):
    name = f'tw-audit-{hashlib.sha256(f"{task.name}{case}{time.time()}".encode()).hexdigest()[:12]}'
    started = time.time()
    record = {'case': case}
    # Hosts whose egress re-signs TLS (CI sandboxes) need their CA in every
    # container; this is an execution-host adaptation, never part of a task.
    extra = []
    if ca_bundle:
        extra = ['-v', f'{ca_bundle}:/opt/host-ca.pem:ro']
        for var in CA_ENV:
            extra += ['-e', f'{var}=/opt/host-ca.pem']
    try:
        up = docker('run', '-d', '--name', name, *extra, image, 'sleep', 'infinity', timeout=120)
        if up.returncode:
            return {**record, 'status': 'error', 'error': up.stderr[-2000:]}
        cwd = workdir(task / 'environment/Dockerfile')
        if script is not None:
            if case == 'oracle':
                docker('cp', str(task / 'solution'), f'{name}:/solution', timeout=120)
                command = ['exec', '-w', cwd, name, 'bash', '-c', 'umask 022; exec bash /solution/solve.sh']
                stdin = None
            else:
                record['replay_parses'] = subprocess.run(['bash', '-n'], input=script, capture_output=True,
                                                         text=True).returncode == 0
                command = ['exec', '-i', '-e', 'TERM=xterm-256color', '-e', 'SHELL=/bin/bash', '-w', cwd,
                           name, 'bash', '-c', 'umask 022; exec bash --login -i -s']
                stdin = script
            try:
                result = docker(*command, timeout=timeouts['agent'], stdin=stdin)
                record['agent_exit'] = result.returncode
                log.write(f'## {case}: agent\n{result.stdout[-20000:]}\n{result.stderr[-20000:]}\n')
            except subprocess.TimeoutExpired:
                record['agent_exit'] = 'timeout'
        docker('exec', name, 'rm', '-rf', '/tests', timeout=60)
        docker('cp', str(task / 'tests'), f'{name}:/tests', timeout=120)
        docker('exec', name, 'mkdir', '-p', '/logs/verifier', timeout=60)
        verifier = ['bash', '-c', 'umask 022; exec bash /tests/test.sh']
        if pip_verifier:
            setup = docker('exec', name, 'bash', '-c', pip_setup(task), timeout=900)
            log.write(f'## {case}: pip verifier setup\n{setup.stdout[-5000:]}\n{setup.stderr[-5000:]}\n')
            if setup.returncode:
                return {**record, 'status': 'verifier_setup_error'}
            verifier = ['bash', '-c', PIP_RUN]
        try:
            verify = docker('exec', '-w', cwd, name, *verifier, timeout=timeouts['verifier'])
            record['verifier_exit'] = verify.returncode
            log.write(f'## {case}: verifier\n{verify.stdout[-20000:]}\n{verify.stderr[-20000:]}\n')
        except subprocess.TimeoutExpired:
            record['verifier_exit'] = 'timeout'
        record['reward'], record['reward_file'] = read_reward(name)
        record['status'] = 'ok'
    except subprocess.TimeoutExpired as exc:
        record.update(status='error', error=f'timeout after {exc.timeout}s: {" ".join(map(str, exc.cmd[:3]))}')
    finally:
        try:
            docker('rm', '-f', name, timeout=120)
        except subprocess.TimeoutExpired:
            record['cleanup'] = 'timeout'
        record['seconds'] = round(time.time() - started, 1)
    return record


def outcome(record):
    if (record.get('status') != 'ok' or record.get('reward') is None
            or 'timeout' in (record.get('agent_exit'), record.get('verifier_exit'))):
        return 'inconclusive'
    if record['reward'] == 1.0:
        return 'reproduced'
    if record.get('replay_parses') and record.get('agent_exit') == 0:
        return 'blocked'
    return 'inconclusive'


def audit_task(task, sidecar, upstream, out, max_exploits, ca_bundle=None, pip_verifier=False):
    config = tomllib.loads((task / 'task.toml').read_text())
    timeouts = {'agent': float(config.get('agent', {}).get('timeout_sec', 600)),
                'verifier': float(config.get('verifier', {}).get('timeout_sec', 600))}
    build_timeout = float(config.get('environment', {}).get('build_timeout_sec', 600))
    image = f'tw-audit/{task.name}:latest'
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory() as scratch:
        context = task / 'environment'
        if ca_bundle:
            # Host adaptation on a temporary copy: RUN steps that fetch over
            # HTTPS must trust the host's re-signing CA too.
            context = Path(scratch) / 'environment'
            shutil.copytree(task / 'environment', context)
            shutil.copyfile(ca_bundle, context / 'host-ca.pem')
            layer = ('COPY host-ca.pem /opt/host-ca.pem\n'
                     'ENV ' + ' '.join(f'{v}=/opt/host-ca.pem' for v in CA_ENV))
            (context / 'Dockerfile').write_text(with_ca_layer((context / 'Dockerfile').read_text(), layer))
        try:
            build = docker('build', '-q', '-t', image, str(context), timeout=max(build_timeout, 1800))
        except subprocess.TimeoutExpired:
            return {'task': task.name, 'build': 'timeout'}
    (out / 'build.log').write_text(build.stdout + build.stderr)
    summary = {'task': task.name, 'build': 'ok' if build.returncode == 0 else 'error',
               'build_seconds': round(time.time() - started, 1), 'cases': []}
    if build.returncode:
        return summary
    exploits = [e for e in json.loads(Path(sidecar).read_text())['exploits']
                if e['classification'] == 'rewarded_serious_exploit' and e['trajectory']]
    cases = [('nop', None, 0.0), ('oracle', '', 1.0)]
    for e in exploits[:max_exploits]:
        cases.append((f'exploit:{e["model"]}/{e["label"]}', keystrokes(upstream / e['trajectory']), None))
    with (out / 'cases.log').open('w') as log:
        for case, script, expected in cases:
            record = run_case(image, task, case, script, timeouts, log, ca_bundle, pip_verifier)
            record['expected'] = expected
            if case.startswith('exploit:'):
                record['outcome'] = outcome(record)
            summary['cases'].append(record)
            print(json.dumps({'task': task.name, **record}), flush=True)
    rewards = {c['case']: c.get('reward') for c in summary['cases']}
    exploit_cases = [c for c in summary['cases'] if c['case'].startswith('exploit:')]
    summary.update(oracle_passed=rewards.get('oracle') == 1.0, nop_passed=rewards.get('nop') == 0.0,
                   exploits_replayed=len(exploit_cases),
                   **{f'exploits_{k}': sum(c['outcome'] == k for c in exploit_cases)
                      for k in ('reproduced', 'blocked', 'inconclusive')})
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--patched', type=Path, required=True, help='patch.py output (tasks/, exploits/)')
    ap.add_argument('--upstream', type=Path, required=True, help='checkout of few-sh/terminal-wrench')
    ap.add_argument('--tasks', nargs='+', required=True, help='task directory names, e.g. seta-env-1012')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--max-exploits', type=int, default=3, help='rewarded serious exploits replayed per task')
    ap.add_argument('--ca-bundle', type=Path, help='CA bundle to trust in containers, for hosts whose egress re-signs TLS')
    ap.add_argument('--pip-verifier', action='store_true',
                    help='replace the wrapper\'s uv download with a PyPI venv; for hosts that block astral.sh')
    args = ap.parse_args()
    if args.out.exists():
        raise ValueError('Use a new output directory')
    results = []
    for name in args.tasks:
        results.append(audit_task(args.patched / 'tasks' / name, args.patched / 'exploits' / f'{name}.json',
                                  args.upstream.resolve(), args.out / name, args.max_exploits,
                                  args.ca_bundle.resolve() if args.ca_bundle else None, args.pip_verifier))
        (args.out / 'summary.json').write_text(json.dumps(results, indent=2) + '\n')
    totals = {'tasks': len(results),
              'build_errors': sum(r.get('build') != 'ok' for r in results),
              'oracle_and_nop_passed': sum(bool(r.get('oracle_passed') and r.get('nop_passed')) for r in results)}
    for key in ('replayed', 'reproduced', 'blocked', 'inconclusive'):
        totals[f'exploits_{key}'] = sum(r.get(f'exploits_{key}', 0) for r in results)
    print(json.dumps(totals, indent=2))


if __name__ == '__main__':
    main()
