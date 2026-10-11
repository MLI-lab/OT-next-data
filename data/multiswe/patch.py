"""Bake a compatible, pinned grader into the pinned Multi-SWE task images."""
from __future__ import annotations

import argparse
import copy
import io
import hashlib
import json
import re
import subprocess
import shlex
from pathlib import Path, PurePosixPath
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


REVISION = '80de95c62ac792c99dcfa8e26569bcd7d036bdc3'
MARKER = '# Multi-SWE pinned grader runtime v4'

# Bullseye packages moved off the live mirrors after its LTS end in August 2026.
# Keep signature checking; only relax archive Release-file expiration checks.
BULLSEYE_ARCHIVE = (
    '. /etc/os-release; if [ "$ID" = debian ] && [ "${VERSION_CODENAME:-}" = bullseye ]; then '
    'for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do '
    '[ -f "$source_file" ] || continue; '
    "sed -i -E 's@https?://deb\\.debian\\.org/debian@https://archive.debian.org/debian@g; "
    "s@https?://security\\.debian\\.org/debian-security@https://archive.debian.org/debian-security@g' \"$source_file\"; "
    'done; printf \'%s\\n\' \'Acquire::Check-Valid-Until "false";\' > /etc/apt/apt.conf.d/99-multiswe-archive; fi'
)

PYTHON_INSTALL = (
    'set -eu; python_build_dir=$(mktemp -d /tmp/multiswe-python.XXXXXX); '
    'trap \'rm -rf "$python_build_dir"\' EXIT; '
    "curl --fail --location --retry 3 'https://github.com/astral-sh/python-build-standalone/releases/download/20250818/cpython-3.11.13%2B20250818-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz' "
    '-o "$python_build_dir/python.tar.gz"; '
    'printf \'%s  %s\\n\' 22232b7e892726fbf898e449cdae9ccfabf080319655575a8c5e54a39b553c96 "$python_build_dir/python.tar.gz" | sha256sum --check -; '
    'mkdir -p /opt/multiswe-python; tar -xzf "$python_build_dir/python.tar.gz" --strip-components=1 -C /opt/multiswe-python'
)


def docker_setup():
    # Embed all setup and pins in the exported Dockerfile. No COPY dependency:
    # the Apptainer deferred-build path executes RUN before replaying COPY.
    requirements = Path(__file__).with_name('grader-requirements.txt').read_text().splitlines()
    pins = ' '.join(shlex.quote(line) for line in requirements if line.strip())
    commands = [
        BULLSEYE_ARCHIVE,
        # Complete tmux package installation in single-UID Apptainer builds.
        # Privileged utmp accounting is unnecessary for task terminals.
        'if ! dpkg-statoverride --list /usr/lib/x86_64-linux-gnu/utempter/utempter; then dpkg-statoverride --add root root 0755 /usr/lib/x86_64-linux-gnu/utempter/utempter; fi',
        'apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tmux && tmux -V && test -z "$(dpkg --audit)"',
        PYTHON_INSTALL,
        '/opt/multiswe-python/bin/python3 -m pip install --no-cache-dir --disable-pip-version-check ' + pins,
        "/opt/multiswe-python/bin/python3 -c 'from multi_swe_bench.harness.dataset import Dataset; from multi_swe_bench.harness.instance import Instance'",
    ]
    return '\n' + MARKER + '\nUSER root\n' + ''.join('RUN ' + command + '\n' for command in commands)


def patched_image_prepare(recipe):
    """Repair only the baseline install command for known Multi-SWE task families."""
    script = recipe['files']['prepare.sh']
    if recipe['repo'] == 'material-ui':
        # The pinned Multi-SWE material-ui verifiers run unit tests only.
        script = 'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1\n' + script
    if recipe['repo'] == 'async':
        # Some pinned commits have package manifests newer than their lockfiles.
        script = re.sub(r'(?<!\S)npm ci\b', 'npm install --no-audit --no-fund', script)
    if recipe['repo'] == 'redux' and re.search(r'(?m)^\s*yarn install\b', script):
        # Skip only the legacy root prepublish hook for dependency installation.
        script = re.sub(r'\s*\|\|\s*true\b', '', script)
        disable = 'const fs=require("fs");const p=JSON.parse(fs.readFileSync("package.json"));delete p.scripts.prepublish;fs.writeFileSync("package.json",JSON.stringify(p));'
        install = ('( backup=$(mktemp); cp package.json "$backup"; '
                   'trap \'cp "$backup" package.json; rm -f "$backup"\' EXIT; '
                   'node -e ' + shlex.quote(disable) + '; yarn install )')
        script = re.sub(r'(?m)^yarn install\s*$', lambda _: install, script)
    return script


def patch_files(files):
    """Keep task/reference semantics; make grader infrastructure failures explicit."""
    files = dict(files)
    docker = files['environment/Dockerfile'].decode()
    if MARKER in docker:
        return files
    test = files['tests/test.sh'].decode()
    install = [line for line in test.splitlines() if line.startswith('python3 -m pip install --target /tmp/multiswe-grader ')]
    if len(install) != 1 or 'python3 /tests/grade.py ' not in test:
        raise ValueError('Unexpected Multi-SWE verifier layout; refusing partial repair')
    grade = files['tests/grade.py'].decode()
    error_line = '        print(f"Multi-SWE grader error: {type(exc).__name__}: {exc}", file=sys.stderr)'
    if grade.count(error_line) != 1:
        raise ValueError('Unexpected grader error handling')
    grade = grade.replace(error_line, error_line + '\n        reward_path.unlink(missing_ok=True)\n        raise')
    import inspect
    grade = grade.replace('def main():', inspect.getsource(expected_missing_source) + '\n\ndef main():')
    grade = grade.replace('        reward = float(report.valid',
                          '        if report.fix_patch_result.all_count == 0 and not expected_missing_source(row, output):\n'
                          '            raise RuntimeError("Verifier produced no parsed test results; see test_output.txt")\n'
                          '        reward = float(report.valid')
    grade = grade.replace('        print(report.error_msg or "valid report")',
                          '        missing = {field: sorted(set(getattr(dataset, field)) - set(getattr(report, field)))\n'
                          '                   for field in ("p2p_tests", "f2p_tests", "s2p_tests", "n2p_tests")}\n'
                          '        print(report.error_msg or ("Missing required test results: " + json.dumps(missing) if not reward else "valid report"))')
    test = test.replace('set -uo pipefail', 'set -euo pipefail')
    test = test.replace(install[0] + '\n', '')
    test = test.replace('export PYTHONPATH=/tmp/multiswe-grader:${PYTHONPATH:-}\n', '')
    test = test.replace('mkdir -p /logs/verifier\n', 'mkdir -p /logs/verifier\nrm -f /logs/verifier/reward.json\n')
    test = test.replace('python3 /tests/grade.py ', '/opt/multiswe-python/bin/python3 -I /tests/grade.py ')
    files.update({
        'environment/Dockerfile': (docker.rstrip() + '\n' + docker_setup()).encode(),
        'tests/test.sh': test.encode(), 'tests/grade.py': grade.encode(),
    })
    return files


def export_shared_recipes(rows):
    """Use the pinned upstream builder, including its per-PR recipe selection."""
    from importlib.metadata import version
    from dataclasses import fields
    from multi_swe_bench.harness.pull_request import PullRequest
    from multi_swe_bench.harness.image import Config, Image
    from multi_swe_bench.harness.instance import Instance
    if version('multi-swe-bench') != '1.1.2':
        raise ValueError('Shared-image conversion requires multi-swe-bench==1.1.2')
    keys = {f.name for f in fields(PullRequest)}
    result = {}
    for task_id, row in rows.items():
        try:
            pr = PullRequest.from_dict({k: v for k, v in row.items() if k in keys})
            image = Instance.create(pr, Config(need_clone=True, global_env=None, clear_env=False)).dependency()
            base = image.dependency()
            if not isinstance(base, Image):
                raise ValueError('No upstream shared Image recipe')
            files = image.files()
            names = [f.name for f in files]
            if len(names) != len(set(names)) or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', n) for n in names):
                raise ValueError('Unsupported recipe filenames')
            # Deliberately support only the exact COPY + prepare pattern audited
            # in this source. Extra RUN/ENV/USER/etc. need explicit review.
            actual = [l.strip() for l in image.dockerfile().splitlines() if l.strip()]
            expected = ['FROM ' + base.image_full_name()] + ['COPY ' + n + ' /home/' for n in names] + ['RUN bash /home/prepare.sh']
            if actual != expected:
                raise ValueError('Unsupported upstream Dockerfile instructions')
            if not {'prepare.sh', 'test.patch', 'fix-run.sh'}.issubset(names):
                raise ValueError('Incomplete upstream recipe')
            result[task_id] = {
                'original_image': image.image_full_name(), 'base': base.image_full_name(),
                'base_sha': pr.base.sha, 'repo': pr.repo,
                # Never export the reference answer into agent-visible setup.
                'files': {f.name: f.content for f in files if f.name != 'fix.patch'},
            }
        except Exception as exc:
            result[task_id] = {'error': str(exc)}
    return result


ELECTRON_PROXY = '''# Electron's downloader requires explicit proxy opt-in.
electron_https_proxy=${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}
electron_http_proxy=${http_proxy:-${HTTP_PROXY:-$electron_https_proxy}}
if [ -n "$electron_https_proxy" ] || [ -n "${GLOBAL_AGENT_HTTPS_PROXY:-}${GLOBAL_AGENT_HTTP_PROXY:-}" ]; then
    export ELECTRON_GET_USE_PROXY=${ELECTRON_GET_USE_PROXY:-1}
    export GLOBAL_AGENT_HTTPS_PROXY=${GLOBAL_AGENT_HTTPS_PROXY:-$electron_https_proxy}
    export GLOBAL_AGENT_HTTP_PROXY=${GLOBAL_AGENT_HTTP_PROXY:-$electron_http_proxy}
    export GLOBAL_AGENT_NO_PROXY=${GLOBAL_AGENT_NO_PROXY:-${no_proxy:-${NO_PROXY:-}}}
fi
unset electron_https_proxy electron_http_proxy
'''


PREINSTALLED_REPOS = {
    'insomnia', 'github-readme-stats', 'async', 'trpc', 'redux', 'material-ui',
    'dayjs', 'react-router', 'checkstyle', 'fastjson2', 'junit5', 'logstash', 'mockito', 'spotbugs',
}


def preinstalled_docker(recipe):
    # Keep the already-built cache keys for repositories whose baseline
    # installer has not changed. The current shared helper adds setup paths for
    # Fastjson2 and JUnit 5; embedding those branches in every image would
    # invalidate unrelated image caches.
    helper_name = ('preinstall.py' if recipe['repo'] in ('fastjson2', 'junit5')
                   else 'preinstall_cache_v8.py')
    helper = Path(__file__).with_name(helper_name).read_text()
    proxy = Path(__file__).with_name('gradle_proxy.py').read_text().split("\nif __name__ == '__main__':", 1)[0]
    proxy = proxy.replace('def main():', 'def configure_gradle_proxy():')
    program = proxy + '\n' + helper + '\npreinstall(json.loads(sys.argv[1]), configure_gradle_proxy)\n'
    # Only baseline preparation enters the image, never the solution or test patch.
    spec = {key: recipe[key] for key in ('repo', 'base_sha')}
    spec['prepare'] = patched_image_prepare(recipe)
    spec['electron_proxy'] = ELECTRON_PROXY if recipe['repo'] == 'insomnia' else ''
    return ('\n# Install baseline dependencies once, for this exact source commit.\n'
            'ENV GRADLE_USER_HOME=/opt/multiswe-gradle\n'
            + ('ENV MAVEN_USER_HOME=/opt/multiswe-maven-user\n' if recipe['repo'] == 'fastjson2' else '')
            + 'RUN /opt/multiswe-python/bin/python3 -c ' + shlex.quote('exec(' + repr(program) + ')')
            + ' ' + shlex.quote(json.dumps(spec, sort_keys=True)) + '\n')


def share_image(files, recipe, locks):
    """Move the audited upstream PR layer to idempotent task initialization."""
    if 'error' in recipe:
        raise ValueError(recipe['error'])
    pinned = locks.get(recipe['base'])
    if not pinned or not re.fullmatch(r'[a-z0-9_./-]+@sha256:[a-f0-9]{64}', pinned):
        raise ValueError('Missing digest-pinned shared base: ' + recipe['base'])
    docker = files['environment/Dockerfile'].decode()
    old = 'FROM ' + recipe['original_image']
    if docker.splitlines()[0] != old:
        raise ValueError('Upstream recipe does not match task image')
    if any(n.startswith('setup_files/') for n in files):
        raise ValueError('Existing task setup requires review')
    if not re.fullmatch(r'[a-f0-9]{40}', recipe['base_sha']):
        raise ValueError('Invalid repository commit')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', recipe['repo']):
        raise ValueError('Invalid repository name')
    recipe = repair_recipe(recipe)
    updated = dict(files)
    updated['environment/Dockerfile'] = docker.replace(old, 'FROM ' + pinned, 1).encode()
    if recipe['repo'] == 'logstash':
        # Azul renamed its signed Release metadata. Accept only those fields for
        # the Azul source, without disabling signature or package verification.
        azul = ('RUN set -eu; azul_list=$(mktemp --suffix=.list); '
                'trap \'rm -f "$azul_list"\' EXIT; '
                'for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do '
                '[ -f "$source" ] || continue; '
                'grep -E \'^deb[[:space:]].*https?://repos.azul.com/zulu/deb([/[:space:]]|$)\' "$source" >> "$azul_list" || true; done; '
                'if [ -s "$azul_list" ]; then apt-get -o Dir::Etc::sourcelist="$azul_list" '
                '-o Dir::Etc::sourceparts=- --allow-releaseinfo-change-origin '
                '--allow-releaseinfo-change-label update; fi\n')
        updated['environment/Dockerfile'] = updated['environment/Dockerfile'].replace(
            (MARKER + '\nUSER root\n').encode(), (MARKER + '\nUSER root\n' + azul).encode(), 1)
    preinstalled = recipe['repo'] in PREINSTALLED_REPOS
    if preinstalled:
        updated['environment/Dockerfile'] += preinstalled_docker(recipe).encode()
        recipe['files']['prepare.sh'] = ('#!/bin/bash\nset -eu\ncd /home/' + recipe['repo'] +
            '\n[ "$(git rev-parse HEAD)" = ' + recipe['base_sha'] + ' ]\n')
    proxy_setup = ELECTRON_PROXY if recipe['original_image'].startswith('mswebench/kong_m_insomnia:') else ''
    identity = hashlib.sha256((json.dumps(recipe, sort_keys=True) + proxy_setup).encode()).hexdigest()
    marker = '/setup_files/.multiswe-' + identity
    lines = ['#!/bin/bash', 'set -euo pipefail',
             'if [ -f ' + marker + ' ]; then exit 0; fi']
    if not preinstalled and any('./gradlew ' in content for content in recipe['files'].values()):
        updated['setup_files/gradle_proxy.py'] = Path(__file__).with_name('gradle_proxy.py').read_bytes()
        lines.append('/opt/multiswe-python/bin/python3 /setup_files/gradle_proxy.py')
    if proxy_setup:
        lines.extend(proxy_setup.rstrip().splitlines())
    for name, content in sorted(recipe['files'].items()):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name == 'fix.patch':
            raise ValueError('Invalid or answer-bearing setup filename')
        updated['setup_files/upstream/' + name] = content.encode()
        lines.append('install -m 0644 ' + shlex.quote('/setup_files/upstream/' + name) + ' ' + shlex.quote('/home/' + name))
    lines += ['rm -f /home/fix.patch', 'cd ' + shlex.quote('/home/' + recipe['repo']),
              'bash /home/prepare.sh',
              '[ "$(git rev-parse HEAD)" = ' + recipe['base_sha'] + ' ]',
              'git ls-files --others --exclude-standard -z > "$(git rev-parse --git-path multiswe-setup-untracked)"',
              'touch ' + marker]
    updated['setup_files/setup.sh'] = ('\n'.join(lines) + '\n').encode()
    solve = updated['solution/solve.sh'].decode()
    updated['solution/solve.sh'] = solve.replace('set -e\n', 'set -e\nbash /setup_files/setup.sh\n', 1).encode()
    if b'bash /setup_files/setup.sh\n' not in updated['solution/solve.sh']:
        raise ValueError('Unknown reference entrypoint')
    updated['instruction.md'] = updated['instruction.md'].rstrip() + b'\n\nBefore working on the task, run `bash /setup_files/setup.sh`. It initializes this task once and safely does nothing on subsequent calls.\n'
    if 'tests/extract_fix_patch.sh' in updated:
        updated['tests/extract_fix_patch.py'] = Path(__file__).with_name('extract_fix_patch.py').read_bytes()
        updated['tests/extract_fix_patch.sh'] = b'#!/bin/bash\nset -euo pipefail\nexec /opt/multiswe-python/bin/python3 /tests/extract_fix_patch.py "$@"\n'
    if recipe['repo'] in ('dayjs', 'darkreader', 'spotbugs', 'mockito') and 'task.toml' in updated:
        updated['task.toml'] = updated['task.toml'].replace(b'memory_mb = 4096', b'memory_mb = 8192')
    if recipe['repo'] == 'nuxt':
        grade = updated['tests/grade.py'].decode()
        # The pinned parser uses the first token of each result line. Verbose
        # Vitest prefixes individual tests with their file, while the original
        # dataset also records suite names from default-reporter slow-test lines.
        import inspect
        grade = grade.replace('def main():', inspect.getsource(normalize_nuxt_output) + '\n\ndef main():')
        grade = grade.replace('    reward_path = Path(sys.argv[3])',
                              '    output = normalize_nuxt_output(output)\n    reward_path = Path(sys.argv[3])')
        updated['tests/grade.py'] = grade.encode()
    if recipe['repo'] == 'trpc':
        import inspect
        grade = updated['tests/grade.py'].decode()
        grade = grade.replace('def main():', inspect.getsource(normalize_trpc_output) + '\n\ndef main():')
        grade = grade.replace('    reward_path = Path(sys.argv[3])',
                              '    output = normalize_trpc_output(output)\n    reward_path = Path(sys.argv[3])')
        updated['tests/grade.py'] = grade.encode()
    return updated


def expected_missing_source(row, output):
    """Recognize a baseline import failure for a source file supplied by the fix."""
    import posixpath
    import re
    baseline = row.get('test_patch_result', {})
    if any(baseline.get(k, 0) for k in ('passed_count', 'failed_count', 'skipped_count')):
        return False
    match = re.search(r"Cannot find module '([^']+)'\nRequire stack:\n- ([^\n]+)", output)
    if not match or not match[1].startswith('.'):
        return False
    missing = posixpath.normpath(posixpath.join(posixpath.dirname(match[2]), match[1]))
    added = re.findall(r'(?m)^--- /dev/null\n\+\+\+ b/([^\n]+)', row.get('fix_patch', ''))
    if any('/home/' + row.get('repo', '') + '/' + name == missing + suffix
           for name in added for suffix in ('', '.js', '.jsx', '.ts', '.tsx', '/index.js', '/index.ts')):
        return True
    # The fix can also remove a broken import rather than add its target.
    importer = match[2].removeprefix('/home/' + row.get('repo', '') + '/')
    for block in row.get('fix_patch', '').split('diff --git '):
        if '\n--- a/' + importer + '\n' not in block:
            continue
        for line in block.splitlines():
            if line.startswith('-') and re.search(r'''(?:from\s*|require\s*\(\s*|import\s*)['"]''' + re.escape(match[1]) + r'''['"]''', line):
                return True
    return False


def normalize_trpc_output(output):
    """The pinned tRPC parser requires ANSI color even for non-interactive logs."""
    import re
    lines = []
    for line in output.splitlines():
        plain = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        match = re.match(r'^(?:@trpc/tests:test-ci: )?\[0\]\s+([✓❯])\s+(?:\|tests\|\s+)?([^|\s]\S*)', plain)
        if match:
            color = '32' if match[1] == '✓' else '33'
            line = f'[0] \x1b[{color}m{match[1]}\x1b[39m \x1b[32m|tests|\x1b[39m {match[2]}'
        lines.append(line)
    return '\n'.join(lines)


def normalize_nuxt_output(output):
    """Map verbose Vitest results to upstream identifiers, with failures winning."""
    import re
    statuses = {}
    for line in output.splitlines():
        line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        match = re.match(r'^([✓×❯])\s+(\S+)(.*)', line)
        if not match:
            continue
        names = [match[2]]
        detail = re.match(r'^ > (\S+)', match[3])
        if detail and re.search(r'\.(test|spec)\.', match[2]):
            names.append(detail[1])
        for name in names:
            statuses[name] = statuses.get(name, True) and match[1] == '✓'
    return '\n'.join(('✓' if passed else '❯') + ' ' + name + ' result'
                     for name, passed in statuses.items())


def repair_recipe(recipe):
    """Repair runtime/test invocation, without changing tests or their expectations."""
    recipe = copy.deepcopy(recipe)
    for name in ('prepare.sh', 'fix-run.sh'):
        content = recipe['files'].get(name, '')
        content = re.sub(r'--max-workers\s+\d+\s*', '', content)
        content = re.sub(r'(?m)^(\./gradlew )(.*)$', r'\1--no-daemon --max-workers 2 \2', content)
        recipe['files'][name] = content
    if recipe['repo'] == 'checkstyle':
        recipe['files']['fix-run.sh'] = recipe['files']['fix-run.sh'].replace(
            'mvn ', 'mvn -s /opt/multiswe-maven-settings.xml -Dmaven.repo.local=/opt/multiswe-maven ')
    if recipe['repo'] == 'fastjson2':
        recipe['files']['fix-run.sh'] = recipe['files']['fix-run.sh'].replace(
            './mvnw ', './mvnw -s /opt/multiswe-maven-settings.xml -Dmaven.repo.local=/opt/multiswe-maven ')
    if recipe['repo'] == 'junit5':
        recipe['files']['fix-run.sh'] = recipe['files']['fix-run.sh'].replace(
            './gradlew ', './gradlew --init-script /opt/multiswe-junit-local.gradle ')
    run = recipe['files']['fix-run.sh']
    # git apply rejects an empty fix.patch, preventing no-op tests from running.
    def split_apply(match):
        command = match.group(0).replace(' /home/fix.patch', '')
        fix_command = command.replace('/home/test.patch', '/home/fix.patch')
        return command + '\nif [ -s /home/fix.patch ]; then ' + fix_command + '; fi'
    run = re.sub(r'(?m)^git apply[^\n]* /home/test\.patch /home/fix\.patch[ \t]*$', split_apply, run)
    if recipe['repo'] == 'darkreader':
        run = run.replace('npm run test:unit -- ', 'npm run test:unit -- --runInBand ')
    if recipe['repo'] == 'dayjs':
        # npm test runs Jest repeatedly for four time zones. Limit every invocation.
        limit = "node -e 'const fs=require(\"fs\"); const p=JSON.parse(fs.readFileSync(\"package.json\")); for(const k of Object.keys(p.scripts||{})) p.scripts[k]=p.scripts[k].replace(/\\bjest\\b/g, \"jest --runInBand\"); fs.writeFileSync(\"package.json\", JSON.stringify(p));'\n"
        run = run.replace('npm test ', limit + 'npm test ')
    if recipe['repo'] == 'nuxt':
        run = run.replace('pnpm test:unit -- --verbose', 'pnpm test:unit --reporter=verbose')
        run = run.replace('pnpm test:runtime  --no-watch', 'pnpm test:runtime --no-watch --reporter=verbose')
    if recipe['repo'] == 'axios':
        # Local proxy tests must control their own proxy environment. Node 20's
        # new family-autoselection default breaks this older custom lookup API.
        run = run.replace('npm test ', 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY npm_config_proxy npm_config_https_proxy npm_config_noproxy\n'
                          'if node --help | grep -q -- --network-family-autoselection; then\n'
                          '    export NODE_OPTIONS="${NODE_OPTIONS:-} --no-network-family-autoselection"\nfi\n'
                          'npm test ')
    recipe['files']['fix-run.sh'] = run
    return recipe


def blob_files(blob):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}


def patch_blob(blob, recipe=None, locks=None):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        members = archive.getmembers()
        files = {}
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(name) in files:
                    raise ValueError('Duplicate task archive member')
                files[str(name)] = archive.extractfile(member).read()
    updated = patch_files(files)
    if recipe is not None:
        updated = share_image(updated, recipe, locks or {})
    if updated == files:
        return blob
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for member in members:
            member = copy.copy(member)
            if member.isfile():
                data = updated.pop(str(PurePosixPath(member.name)))
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
        for name, data in sorted(updated.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--share-images', action='store_true', help='experimental: move audited upstream PR preparation into setup_files')
    parser.add_argument('--base-image-lock', type=Path, help='JSON mapping upstream base tags to digest-pinned references')
    parser.add_argument('--recipe-python', default=sys.executable, help='Python with multi-swe-bench==1.1.2 installed')
    args = parser.parse_args()
    if args.share_images and not args.base_image_lock:
        parser.error('--share-images requires --base-image-lock')
    locks = json.loads(args.base_image_lock.read_text()) if args.base_image_lock else {}
    sources = [args.source] if args.source.is_file() else sorted(args.source.glob('*.parquet'))
    if not sources:
        raise ValueError('No source Parquets found')
    if args.output.exists():
        raise ValueError('Use a new output directory')
    args.output.mkdir(parents=True)
    for source in sources:
        table = pq.read_table(source)
        rows = table.to_pylist()
        labels, reasons = {}, {}
        recipes, unresolved = {}, {}
        if args.share_images:
            row_data = {r['path']: json.loads(blob_files(r['task_binary'])['tests/row.json']) for r in rows}
            generated = subprocess.run([args.recipe_python, str(Path(__file__).resolve()), '--export-shared-recipes'],
                                       input=json.dumps(row_data), capture_output=True, text=True, check=True)
            recipes = json.loads(generated.stdout)
        for row in rows:
            try:
                patched = patch_blob(row['task_binary'], recipes.get(row['path']), locks)
            except ValueError as exc:
                if not args.share_images:
                    raise
                unresolved[row['path']] = str(exc)
                patched = patch_blob(row['task_binary'])
            if patched != row['task_binary']:
                labels[row['path']] = ['runtime-and-verifier-repair']
                reasons[row['path']] = ('Bake a pinned, isolated grader and required network tools into the image; '
                                        'repair Debian archive downloads and report verifier infrastructure failures explicitly.')
            if row['path'] in recipes and row['path'] not in unresolved:
                reasons[row['path']] += (' Use a digest-pinned base image. '
                                         'Exclude setup artifacts from solution patches, batch Git filenames safely, '
                                         'and run tests when the solution patch is empty.')
                recipe = recipes[row['path']]
                if recipe['repo'] in PREINSTALLED_REPOS:
                    reasons[row['path']] += ' Preinstall baseline dependencies in the image for the exact source commit; remove dependency installation and baseline test execution from task setup.'
                if recipe['repo'] == 'redux':
                    reasons[row['path']] += ' Suppress only the root prepublish release/test hook during dependency installation and restore package.json afterward.'
                if recipe['repo'] == 'material-ui':
                    reasons[row['path']] += ' Skip Playwright browser downloads; these tasks use unit-test verifiers.'
                if recipe['repo'] == 'async':
                    reasons[row['path']] += ' Reconcile stale package-lock entries with npm install while retaining existing locked versions.'
                if recipe['repo'] == 'junit5':
                    reasons[row['path']] += ' Resolve the matching Vintage engine dependency from the baseline project during installation and verification; verify direct Gradle plugin downloads after proxy rate limits.'
                if recipe['repo'] == 'logstash':
                    reasons[row['path']] += ' Accept the Azul repository Origin and Label metadata rename only for its signed apt source.'
                else:
                    reasons[row['path']] += ' Initialize the task checkout in setup while sharing its compatible base image.'
                if recipe['repo'] in ('dayjs', 'darkreader'):
                    reasons[row['path']] += ' Run Jest serially with an 8 GiB task allowance to avoid worker memory kills.'
                if recipe['repo'] in ('nuxt', 'trpc'):
                    reasons[row['path']] += ' Make actual test results parseable despite reporter verbosity or ANSI-color differences.'
                if recipe['repo'] == 'axios':
                    reasons[row['path']] += ' Let local proxy tests control their own proxy environment.'
            row['task_binary'] = patched
        output = args.output / source.name
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), output)
        write_patch_report(source, output, patcher=__file__, source={
            'dataset': 'PrimeIntellect/Multi-SWE-RL-Verified', 'revision': REVISION,
            'url': f'https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified/tree/{REVISION}'},
            dropped={}, change_labels=labels, change_reasons=reasons, unresolved=unresolved,
            patches=[{'version': 'multiswe-preinstalled-dependencies-v10', 'python': '3.11.13', 'grader': '1.1.2', 'share_images': args.share_images,
                      'helper_sha256': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                        for name in ('extract_fix_patch.py', 'gradle_proxy.py', 'preinstall.py', 'preinstall_cache_v8.py')},
                      'base_image_lock': locks if args.share_images else {}}])
        if args.share_images:
            (args.output / (source.stem + '.shared-images.json')).write_text(json.dumps({
                'tasks': len(rows), 'converted': len(recipes) - len(unresolved), 'unresolved': unresolved,
                'unique_dockerfiles': len({hashlib.sha256(blob_files(r['task_binary'])['environment/Dockerfile']).hexdigest() for r in rows}),
            }, indent=2) + '\n')
        print(f'{output}: {len(rows)} tasks, {len(labels)} repaired, none dropped')


if __name__ == '__main__':
    if sys.argv[1:] == ['--export-shared-recipes']:
        print(json.dumps(export_shared_recipes(json.load(sys.stdin))))
    else:
        main()
