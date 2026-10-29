import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from data.multiswe.patch import patch_blob, patch_files, EMBEDDED_FILES


def embedded_helper(name):
    from types import ModuleType
    module = ModuleType(name)
    exec(compile(EMBEDDED_FILES[name + ".py"], name + ".py", "exec"), vars(module))
    return module


@pytest.mark.parametrize('repo', ['mockito', 'spotbugs'])
def test_baked_gradle_caches_are_readable_without_changing_contents(tmp_path, repo):
    from data.multiswe.patch import preinstalled_docker
    recipe = {'repo': repo, 'base_sha': 'a' * 40,
              'files': {'prepare.sh': '#!/bin/bash\ntrue\n'}}
    docker = preinstalled_docker(recipe)
    command = docker.splitlines()[-1].removeprefix('RUN ')
    command = command.replace('/home/' + repo, str(tmp_path))
    for directory in ['.gradle', 'buildSrc/build/nested']:
        cache = tmp_path / directory
        cache.mkdir(parents=True)
        cache.chmod(0o700)
        artifact = cache / 'artifact'
        artifact.write_bytes(b'baseline cache')
        artifact.chmod(0o600)
    subprocess.run(['bash', '-ec', command], check=True)
    for directory in ['.gradle', 'buildSrc/build/nested']:
        cache = tmp_path / directory
        assert cache.stat().st_mode & 0o777 == 0o755
        artifact = cache / 'artifact'
        assert artifact.stat().st_mode & 0o777 == 0o644
        assert artifact.read_bytes() == b'baseline cache'


def original_files():
    from data.utils.prime_conversion import multiswe_grade
    return {
        'environment/Dockerfile': b'FROM mswebench/example:pr-1\nWORKDIR /home/repo\n',
        'tests/test.sh': b'''#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
python3 -m pip install --target /tmp/multiswe-grader 'multi-swe-bench @ url' || true
export PYTHONPATH=/tmp/multiswe-grader:${PYTHONPATH:-}
bash /home/fix-run.sh > /logs/verifier/test_output.txt 2>&1 || true
python3 /tests/grade.py /tests/row.json /logs/verifier/test_output.txt /logs/verifier/reward.json
''',
        'tests/grade.py': Path(multiswe_grade.__file__).read_bytes(),
        'instruction.md': b'Fix this task.',
        'solution/solve.sh': b'#!/bin/bash\nset -e\ngit apply /solution/gold.patch\n',
    }


def test_patch_preserves_task_and_bakes_grader():
    files = original_files()
    patched = patch_files(files)
    assert patched['instruction.md'] == files['instruction.md']
    assert patched['solution/solve.sh'] == files['solution/solve.sh']
    assert b'pip install' not in patched['tests/test.sh']
    assert b'/opt/multiswe-python/bin/python3 -I' in patched['tests/test.sh']
    assert b'curl ca-certificates' in patched['environment/Dockerfile']
    assert b'sha256sum --check' in patched['environment/Dockerfile']
    assert b'COPY ' not in patched['environment/Dockerfile']
    assert b'install-grader.sh' not in patched['environment/Dockerfile']
    assert b'file:///build/' not in patched['environment/Dockerfile']
    assert patch_files(patched) == patched
    subprocess.run(['bash', '-n'], input=patched['tests/test.sh'], check=True)
    for line in patched['environment/Dockerfile'].splitlines():
        if line.startswith(b'RUN '):
            subprocess.run(['sh', '-n'], input=line[4:], check=True)


def test_grader_import_failure_cannot_produce_nop_reward(tmp_path):
    files = patch_files(original_files())
    (tmp_path/'grade.py').write_bytes(files['tests/grade.py'])
    (tmp_path/'row.json').write_text('{}')
    (tmp_path/'output.txt').write_text('')
    reward = tmp_path/'reward.json'
    reward.write_text('{"reward": 0}')
    result = subprocess.run([sys.executable, '-I', '-S', str(tmp_path/'grade.py'),
                             str(tmp_path/'row.json'), str(tmp_path/'output.txt'), str(reward)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Multi-SWE grader error' in result.stderr
    assert not reward.exists()


def test_archive_preserves_modes_and_is_idempotent():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in original_files().items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith('.sh') else 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    patched = patch_blob(stream.getvalue())
    assert patch_blob(patched) == patched
    with tarfile.open(fileobj=io.BytesIO(patched)) as archive:
        assert archive.getmember('tests/test.sh').mode == 0o755
        assert 'environment/install-grader.sh' not in archive.getnames()


def test_unrecognized_verifier_is_not_partially_repaired():
    files = original_files()
    files['tests/test.sh'] = b'unknown verifier'
    with pytest.raises(ValueError, match='layout'):
        patch_files(files)


def shared_recipe():
    return {'original_image': 'mswebench/example:pr-1', 'base': 'mswebench/example:base',
            'base_sha': 'a'*40, 'repo': 'repo',
            'files': {'prepare.sh': '#!/bin/bash\ntrue\n', 'test.patch': 'test patch',
                      'fix-run.sh': 'git apply /home/test.patch /home/fix.patch\n'}}


def test_shared_images_keep_task_specific_files_out_of_build_context(tmp_path):
    from data.multiswe.patch import share_image
    from validation.stages.task_setup import detect
    recipe = shared_recipe()
    lock = {recipe['base']: 'mswebench/example@sha256:' + 'b'*64}
    first = share_image(patch_files(original_files()), recipe, lock)
    second_recipe = {**recipe, 'base_sha': 'c'*40, 'files': {**recipe['files'], 'test.patch': 'other test'}}
    second = share_image(patch_files(original_files()), second_recipe, lock)
    assert first['environment/Dockerfile'] == second['environment/Dockerfile']
    assert first['setup_files/setup.sh'] != second['setup_files/setup.sh']
    assert first['setup_files/upstream/test.patch'] != second['setup_files/upstream/test.patch']
    assert 'setup_files/upstream/fix.patch' not in first
    for name, content in first.items():
        p = tmp_path/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    assert detect(tmp_path) == 'bash /setup_files/setup.sh'
    subprocess.run(['bash', '-n', str(tmp_path/'setup_files/setup.sh')], check=True)


def test_shared_setup_is_idempotent_after_agent_edits(tmp_path):
    from data.multiswe.patch import share_image
    recipe = shared_recipe()
    setup = share_image(patch_files(original_files()), recipe,
                        {recipe['base']: 'mswebench/example@sha256:'+'b'*64})['setup_files/setup.sh'].decode()
    marker = setup.split('if [ -f ', 1)[1].split(' ];', 1)[0]
    local_marker = tmp_path/'ready'
    local_marker.touch()
    # Once initialized, neither copying nor repository preparation may run again.
    setup = setup.replace(marker, str(local_marker))
    result = subprocess.run(['bash'], input=setup, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_verifier_reinstalls_upstream_harness_from_tests():
    # The verifier runs in the agent's container; /home copies are agent-writable.
    from data.multiswe.patch import share_image
    recipe = shared_recipe()
    files = share_image(patch_files(original_files()), recipe, {recipe['base']: 'mswebench/example@sha256:'+'b'*64})
    test = files['tests/test.sh'].decode()
    for name in recipe['files']:
        assert files['tests/upstream/' + name] == files['setup_files/upstream/' + name]
        assert test.index('install -m 0644 /tests/upstream/' + name + ' /home/' + name) < test.index('bash /home/fix-run.sh')


def test_release_repair_reinstalls_harness_and_adds_suffix():
    # Published v1 tasks: same reinstall as share_image, plus the timeout sentence; idempotent.
    from data.multiswe.patch import share_image, release_repair_files
    recipe = shared_recipe()
    files = share_image(patch_files(original_files()), recipe, {recipe['base']: 'mswebench/example@sha256:'+'b'*64})
    published = {k: v for k, v in files.items() if not k.startswith('tests/upstream/')}
    published['tests/test.sh'] = b'\n'.join(l for l in files['tests/test.sh'].split(b'\n')
                                           if not l.startswith(b'install -m 0644 /tests/upstream/'))
    published['task.toml'] = b'version = "1.0"\n[agent]\ntimeout_sec = 1200\n'
    repaired = release_repair_files(published)
    assert repaired['tests/test.sh'] == files['tests/test.sh']
    assert all(repaired['tests/upstream/' + n] == files['tests/upstream/' + n] for n in recipe['files'])
    assert repaired['instruction.md'].decode().endswith('You have 1200 seconds to complete this task. '
                                                        'Do not cheat by using online solutions or hints specific to this task.\n')
    assert release_repair_files(repaired) == repaired


@pytest.mark.parametrize('problem', ['unpinned', 'wrong_image', 'answer_file'])
def test_shared_conversion_rejects_unreviewed_inputs(problem):
    from data.multiswe.patch import share_image
    recipe = shared_recipe()
    locks = {recipe['base']: 'mswebench/example@sha256:' + 'b'*64}
    if problem == 'unpinned': locks[recipe['base']] = 'mswebench/example:base'
    if problem == 'wrong_image': recipe['original_image'] = 'another:tag'
    if problem == 'answer_file': recipe['files']['fix.patch'] = 'answer'
    with pytest.raises(ValueError):
        share_image(patch_files(original_files()), recipe, locks)


@pytest.mark.parametrize('distro,codename,archived', [('debian', 'bullseye', True), ('debian', 'bookworm', False), ('ubuntu', 'focal', False)])
def test_archive_repair_only_changes_debian_11_sources(tmp_path, distro, codename, archived):
    from data.multiswe.patch import BULLSEYE_ARCHIVE
    etc = tmp_path/'etc'
    (etc/'apt/sources.list.d').mkdir(parents=True)
    (etc/'apt/apt.conf.d').mkdir()
    (etc/'os-release').write_text(f'ID={distro}\nVERSION_CODENAME={codename}\n')
    lines = ('deb http://deb.debian.org/debian bullseye main\n'
             'deb http://security.debian.org/debian-security bullseye-security main\n'
             'deb https://third-party.example/repo stable main\n')
    sources = etc/'apt/sources.list'
    sources.write_text(lines)
    deb822 = etc/'apt/sources.list.d/debian.sources'
    deb822.write_text('Types: deb\nURIs: https://deb.debian.org/debian-security\nSuites: bullseye-security\n')
    subprocess.run(['sh', '-ec', BULLSEYE_ARCHIVE.replace('/etc/', str(etc)+'/')], check=True)
    if archived:
        assert sources.read_text().count('https://archive.debian.org/') == 2
        assert 'https://third-party.example/repo' in sources.read_text()
        assert 'https://archive.debian.org/debian-security' in deb822.read_text()
        assert 'trusted=yes' not in sources.read_text()
        assert (etc/'apt/apt.conf.d/99-multiswe-archive').exists()
    else:
        assert sources.read_text() == lines
        assert not (etc/'apt/apt.conf.d/99-multiswe-archive').exists()


def test_parallel_python_bootstrap_uses_private_checked_archives(tmp_path):
    import hashlib
    import os
    from data.multiswe.patch import PYTHON_INSTALL
    bindir = tmp_path/'bin'
    bindir.mkdir()
    curl = bindir/'curl'
    curl.write_text('#!/bin/bash\nprintf "%s\\n" "${@: -1}" >> "$BOOTSTRAP_LOG"\ncp "$BOOTSTRAP_ARCHIVE" "${@: -1}"\nsleep 0.1\n')
    curl.chmod(0o755)
    processes = []
    for i in range(2):
        archive = tmp_path/f'python-{i}.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            content = f'python runtime {i}'.encode()
            member = tarfile.TarInfo('python/runtime')
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
        command = PYTHON_INSTALL.replace('22232b7e892726fbf898e449cdae9ccfabf080319655575a8c5e54a39b553c96', hashlib.sha256(archive.read_bytes()).hexdigest())
        command = command.replace('/tmp/multiswe-python.', str(tmp_path/'download.')).replace('/opt/multiswe-python', str(tmp_path/f'installed-{i}'))
        env = dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH'], BOOTSTRAP_ARCHIVE=str(archive), BOOTSTRAP_LOG=str(tmp_path/'downloads.log'))
        processes.append(subprocess.Popen(['sh', '-ec', command], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    for p in processes:
        out, err = p.communicate(timeout=10)
        assert p.returncode == 0, err
    downloads = (tmp_path/'downloads.log').read_text().splitlines()
    assert len(set(downloads)) == 2
    assert all(not Path(p).parent.exists() for p in downloads)
    for i in range(2):
        assert (tmp_path/f'installed-{i}/runtime').read_text() == f'python runtime {i}'


@pytest.mark.parametrize('proxy_env,expected', [
    ({}, ['', '', '', '']),
    ({'https_proxy': 'http://proxy.example:80', 'no_proxy': 'localhost'}, ['1', 'http://proxy.example:80', 'http://proxy.example:80', 'localhost']),
    ({'HTTP_PROXY': 'http://proxy.example:8080'}, ['1', 'http://proxy.example:8080', 'http://proxy.example:8080', '']),
    ({'HTTPS_PROXY': 'http://default:80', 'GLOBAL_AGENT_HTTPS_PROXY': 'http://custom:80', 'GLOBAL_AGENT_NO_PROXY': 'custom.local'}, ['1', 'http://custom:80', 'http://default:80', 'custom.local']),
])
def test_electron_proxy_adapts_environment_without_hardcoding_cluster(proxy_env, expected):
    from data.multiswe.patch import ELECTRON_PROXY
    command = ELECTRON_PROXY + '\nprintf "%s\\n" "${ELECTRON_GET_USE_PROXY:-}" "${GLOBAL_AGENT_HTTPS_PROXY:-}" "${GLOBAL_AGENT_HTTP_PROXY:-}" "${GLOBAL_AGENT_NO_PROXY:-}"\n'
    result = subprocess.run(['/bin/bash', '-eu', '-c', command], env=proxy_env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == expected


def test_insomnia_proxy_is_available_during_image_preinstallation():
    from data.multiswe.patch import share_image
    files = patch_files(original_files())
    recipe = shared_recipe()
    recipe['original_image'] = 'mswebench/kong_m_insomnia:pr-8284'
    recipe['repo'] = 'insomnia'
    files['environment/Dockerfile'] = files['environment/Dockerfile'].replace(b'mswebench/example:pr-1', recipe['original_image'].encode())
    lock = {recipe['base']: 'mswebench/example@sha256:'+'b'*64}
    result = share_image(files, recipe, lock)
    assert b'ELECTRON_GET_USE_PROXY' in result['setup_files/setup.sh']
    assert b'ELECTRON_GET_USE_PROXY' in result['environment/Dockerfile']
    assert result['setup_files/setup.sh'].index(b'ELECTRON_GET_USE_PROXY') < result['setup_files/setup.sh'].index(b'bash /home/prepare.sh')
    assert b'proxy.nhr.fau.de' not in result['setup_files/setup.sh']


def test_extraction_batches_literal_paths_and_preserves_setup_files(tmp_path):
    extract_script = tmp_path / "extract_fix_patch.py"
    extract_script.write_text(EMBEDDED_FILES["extract_fix_patch.py"])
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    git('init', '-q')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Test')
    (tmp_path/'tracked').write_text('before\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD').stdout.decode().strip()
    # A dependency installed by setup must not enter the fix or be cleaned away.
    (tmp_path/'dependency').write_text('installed\n')
    (tmp_path/'.git/multiswe-setup-untracked').write_bytes(b'dependency\0')
    (tmp_path/'tracked').write_text('after\n')
    paths = ['space name', 'line\nbreak', 'literal[*]'] + [f'new-{i:04d}-' + 'a'*180 for i in range(400)]
    for path in paths:
        (tmp_path/path).write_text('new\n')
    (tmp_path/'new-test.js').write_text('test only\n')
    output = tmp_path/'.git/fix.patch'
    subprocess.run([sys.executable, str(extract_script), str(tmp_path), base, str(output)], check=True)
    assert (tmp_path/'dependency').read_text() == 'installed\n'
    assert not (tmp_path/'new-test.js').exists()
    assert (tmp_path/'tracked').read_text() == 'before\n'
    git('apply', str(output))
    assert (tmp_path/'tracked').read_text() == 'after\n'
    for path in paths:
        assert (tmp_path/path).read_text() == 'new\n'
    assert not (tmp_path/'new-test.js').exists()
    assert 'dependency' not in output.read_text()


def test_empty_fix_still_runs_tests(tmp_path):
    from data.multiswe.patch import repair_recipe
    recipe = shared_recipe()
    recipe['files']['fix-run.sh'] = 'set -e\ngit apply /home/test.patch /home/fix.patch\nprintf ran > ran\n'
    run = repair_recipe(recipe)['files']['fix-run.sh']
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path/'test.patch').write_text('diff --git a/new b/new\nnew file mode 100644\n--- /dev/null\n+++ b/new\n@@ -0,0 +1 @@\n+test\n')
    (tmp_path/'fix.patch').write_text('')
    subprocess.run(['bash', '-c', run.replace('/home/', str(tmp_path)+'/')], cwd=tmp_path, check=True)
    assert (tmp_path/'ran').read_text() == 'ran'
    assert (tmp_path/'new').read_text() == 'test\n'


def test_gradle_proxy_supports_credentials_and_bypass_without_logging(tmp_path, monkeypatch):
    gradle_proxy = embedded_helper("gradle_proxy")
    properties, main = gradle_proxy.properties, gradle_proxy.main
    def unavailable(*args):
        raise OSError('offline test')
    monkeypatch.setattr(gradle_proxy, 'build_opener', unavailable)
    values = properties({'https_proxy': 'http://user:p%40ss@proxy.example:3128', 'NO_PROXY': 'localhost,.example.org,10.0.0.0/8'})
    assert values['systemProp.https.proxyPassword'] == 'p@ss'
    assert values['systemProp.https.proxyPort'] == '3128'
    assert '*.example.org' in values['systemProp.http.nonProxyHosts']
    assert '10.0.0.0/8' not in values['systemProp.http.nonProxyHosts']
    monkeypatch.setenv('GRADLE_USER_HOME', str(tmp_path))
    monkeypatch.setenv('https_proxy', 'http://proxy.example:3128')
    config = tmp_path/'gradle.properties'
    config.write_text('systemProp.https.proxyHost=custom.example\norg.gradle.jvmargs=-Xmx1g\n')
    main()
    assert config.read_text().count('systemProp.https.proxyHost=') == 1
    assert 'custom.example' in config.read_text()
    assert 'org.gradle.jvmargs=-Xmx1g' in config.read_text()


@pytest.mark.parametrize('proxy_status,direct_status,bypass', [(429, 200, True), (200, 200, False), (429, 403, False)])
def test_gradle_direct_fallback_requires_rate_limit_and_success(tmp_path, monkeypatch, proxy_status, direct_status, bypass):
    gradle_proxy = embedded_helper("gradle_proxy")
    from urllib.error import HTTPError
    monkeypatch.setenv('GRADLE_USER_HOME', str(tmp_path))
    monkeypatch.setenv('https_proxy', 'http://proxy.example:3128')
    class Reply:
        status = 200
        def read(self): return b'<artifactId>kotlin-stdlib</artifactId>'
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Opener:
        def __init__(self, handler): self.proxied = bool(handler.proxies)
        def open(self, url, timeout):
            status = proxy_status if self.proxied else direct_status
            if status != 200: raise HTTPError(url, status, 'test', {}, None)
            return Reply()
    monkeypatch.setattr(gradle_proxy, 'build_opener', Opener)
    gradle_proxy.main()
    content = (tmp_path/'gradle.properties').read_text()
    assert ('|repo.maven.apache.org' in content) == bypass
    assert ('|repo1.maven.org' in content) == bypass


@pytest.mark.parametrize('repo', ['dayjs', 'darkreader', 'nuxt', 'axios', 'spotbugs', 'mockito'])
def test_repaired_runners_are_valid_shell(repo):
    from data.multiswe.patch import repair_recipe
    recipe = shared_recipe()
    recipe['repo'] = repo
    recipe['files']['fix-run.sh'] += 'npm test -- --verbose\nnpm run test:unit -- --json\npnpm test:unit -- --verbose\n./gradlew clean test --max-workers 8\n'
    result = repair_recipe(recipe)
    for name in ('prepare.sh', 'fix-run.sh'):
        subprocess.run(['bash', '-n'], input=result['files'][name], text=True, check=True)


def test_nuxt_verbose_results_preserve_names_and_failures():
    from data.multiswe.patch import normalize_nuxt_output
    output = normalize_nuxt_output('''✓ packages/nuxt/test/load-nuxt.test.ts > loadNuxt > default 10ms
✓ packages/nuxt/test/load-nuxt.test.ts > loadNuxt > custom 20ms
✓ built in 20ms
''')
    assert '✓ loadNuxt result' in output
    assert '✓ packages/nuxt/test/load-nuxt.test.ts result' in output
    failed = normalize_nuxt_output('''✓ packages/nuxt/test/load-nuxt.test.ts > loadNuxt > default 10ms
× packages/nuxt/test/load-nuxt.test.ts > loadNuxt > custom 20ms
✓ packages/nuxt/test/load-nuxt.test.ts > loadNuxt > another 30ms
''')
    assert '✓ loadNuxt' not in failed
    assert '❯ loadNuxt result' in failed
    assert '❯ packages/nuxt/test/load-nuxt.test.ts result' in failed
    assert 'loadNuxt' not in normalize_nuxt_output('✓ packages/nuxt/test/load-nuxt.test.ts (1 test) 10ms')


def test_no_test_output_is_an_error_not_a_passing_nop(tmp_path, monkeypatch):
    import types
    files = patch_files(original_files())
    for module, symbol in [('dataset', 'Dataset'), ('image', 'Config'), ('instance', 'Instance')]:
        mod = types.ModuleType('multi_swe_bench.harness.' + module)
        cls = type(symbol, (), {'from_dict': staticmethod(lambda row: None),
                               'create': staticmethod(lambda **kwargs: None),
                               '__init__': lambda self, **kwargs: None})
        setattr(mod, symbol, cls)
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    dataset = types.SimpleNamespace(run_result='', test_patch_result='')
    sys.modules['multi_swe_bench.harness.dataset'].Dataset.from_dict = staticmethod(lambda row: dataset)
    report = types.ModuleType('multi_swe_bench.harness.report')
    report.generate_report = lambda *args: types.SimpleNamespace(fix_patch_result=types.SimpleNamespace(all_count=0))
    monkeypatch.setitem(sys.modules, report.__name__, report)
    (tmp_path/'row.json').write_text('{}')
    (tmp_path/'output.txt').write_text('download failed')
    reward = tmp_path/'reward.json'
    reward.write_text('{"reward": 0}')
    monkeypatch.setattr(sys, 'argv', ['grade.py', str(tmp_path/'row.json'), str(tmp_path/'output.txt'), str(reward)])
    namespace = {'__name__': 'test_grader'}
    exec(compile(files['tests/grade.py'], 'grade.py', 'exec'), namespace)
    with pytest.raises(RuntimeError, match='no parsed test results'):
        namespace['main']()
    assert not reward.exists()


def test_preinstalled_images_are_commit_specific_and_setup_is_lightweight():
    from data.multiswe.patch import share_image
    base = 'mswebench/mockito_m_mockito:base'
    images = []
    for commit in ('a'*40, 'b'*40):
        recipe = shared_recipe()
        recipe.update(base=base, base_sha=commit, repo='mockito')
        recipe['files']['prepare.sh'] = 'set -e\n./gradlew clean test || true\n'
        recipe['files']['fix-run.sh'] = 'set -e\ngit apply /home/test.patch /home/fix.patch\n./gradlew test\n'
        result = share_image(patch_files(original_files()), recipe, {base: 'mswebench/mockito_m_mockito@sha256:'+'b'*64})
        images.append(result['environment/Dockerfile'])
        assert b'./gradlew' not in result['setup_files/upstream/prepare.sh']
        assert b'./gradlew --no-daemon --max-workers 2 test' in result['setup_files/upstream/fix-run.sh']
        assert b'--offline' not in result['setup_files/upstream/fix-run.sh']
        assert b'multisweCacheDependencies' not in images[-1]
        assert commit.encode() in images[-1]
        assert b'testClasses' in images[-1]
        for line in images[-1].splitlines():
            if line.startswith(b'RUN '): subprocess.run(['sh', '-n'], input=line[4:], check=True)
    assert images[0] != images[1]


def test_trpc_color_normalization_preserves_pass_and_fail():
    from data.multiswe.patch import normalize_trpc_output
    output = normalize_trpc_output('[0] ✓ |tests| server/a.test.ts (1 test)\n[0] ❯ |tests| server/b.test.ts (1 failed)\n')
    assert '[0] \x1b[32m✓\x1b[39m \x1b[32m|tests|\x1b[39m server/a.test.ts' in output
    assert '[0] \x1b[33m❯\x1b[39m \x1b[32m|tests|\x1b[39m server/b.test.ts' in output
    assert normalize_trpc_output(output) == output


def test_expected_missing_source_requires_baseline_and_reference_evidence():
    from data.multiswe.patch import expected_missing_source
    output = "Error: Cannot find module '../utils/missing'\nRequire stack:\n- /home/repo/src/feature/main.ts\n"
    row = {'repo': 'repo', 'test_patch_result': {'passed_count': 0},
           'fix_patch': "diff --git a/src/feature/main.ts b/src/feature/main.ts\n--- a/src/feature/main.ts\n+++ b/src/feature/main.ts\n@@ -1 +0 @@\n-import {x} from '../utils/missing';\n"}
    assert expected_missing_source(row, output)
    assert not expected_missing_source({**row, 'fix_patch': ''}, output)
    assert not expected_missing_source({**row, 'test_patch_result': {'passed_count': 1}}, output)
    assert not expected_missing_source(row, output.replace('../utils/missing', 'missing-package'))


@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('repo_kind', ['example', 'mockito'])
def test_preinstallation_failure_is_fatal_and_dependencies_remain(tmp_path, monkeypatch, fail, repo_kind):
    installer = embedded_helper("preinstall")
    repo = tmp_path/'home'/repo_kind
    repo.mkdir(parents=True)
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True).stdout.decode().strip()
    git('init', '-q'); git('config', 'user.email', 'test@example.invalid'); git('config', 'user.name', 'Test')
    (repo/'source').write_text('baseline')
    (repo/'gradlew').write_text('#!/bin/bash\nprintf "%s\\n" "$@" > arguments\nmkdir -p node_modules\necho installed > node_modules/dependency\nexit '+str(int(fail))+'\n')
    (repo/'gradlew').chmod(0o755)
    git('add', '.'); git('commit', '-qm', 'baseline')
    commit = git('rev-parse', 'HEAD')
    class LocalPaths:
        def __call__(self, *args, **kwargs):
            if args and args[0] == '/home': args = (str(tmp_path/'home'), *args[1:])
            if args and args[0] == '/home/fix.patch': args = (str(tmp_path/'fix.patch'), *args[1:])
            if args and args[0] == '/opt/multiswe-preinstalled.json': args = (str(tmp_path/'record.json'), *args[1:])
            return Path(*args, **kwargs)
    monkeypatch.setattr(installer, 'Path', LocalPaths())
    gradle_home = tmp_path/'gradle'
    monkeypatch.setenv('GRADLE_USER_HOME', str(gradle_home))
    def proxy():
        gradle_home.mkdir()
        (gradle_home/'gradle.properties').write_text('temporary proxy configuration')
    spec = {'repo':repo_kind, 'base_sha':commit, 'electron_proxy':'',
            'prepare':'mkdir -p node_modules; echo installed > node_modules/dependency; '+('false || true' if fail else 'true')}
    if fail:
        with pytest.raises(RuntimeError, match='dependency installation failed'):
            installer.preinstall(spec, proxy)
    else:
        installer.preinstall(spec, proxy)
    assert (repo/'node_modules/dependency').read_text().strip() == 'installed'
    assert (tmp_path/'record.json').exists() != fail
    if repo_kind == 'mockito':
        arguments = (repo/'arguments').read_text().splitlines()
        assert 'testClasses' in arguments
        assert 'test' not in arguments and 'clean' not in arguments
        assert not (gradle_home/'gradle.properties').exists()

def test_redux_install_skips_only_root_prepublication_and_restores_source():
    from data.multiswe.patch import patched_image_prepare
    script = patched_image_prepare({'repo':'redux', 'base_sha':'d'*40,
                                    'files': {'prepare.sh':'yarn install || true\n'}})
    assert 'delete p.scripts.prepublish' in script
    assert 'cp "$backup" package.json' in script
    assert 'yarn install' in script


def test_material_ui_preinstall_skips_unused_playwright_browser_downloads():
    from data.multiswe.patch import patched_image_prepare
    script = patched_image_prepare({'repo':'material-ui', 'base_sha':'d'*40,
                                   'files': {'prepare.sh':'yarn install\n'}})
    assert script.startswith('export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1\n')


def test_async_preinstall_repairs_stale_npm_ci_lockfile():
    from data.multiswe.patch import patched_image_prepare
    script = patched_image_prepare({'repo':'async', 'base_sha':'d'*40,
                                   'files': {'prepare.sh':'npm ci\n'}})
    assert 'npm install --no-audit --no-fund' in script
    assert 'npm ci' not in script


def test_fastjson_preinstalls_dependencies_but_keeps_verifier_tests():
    from data.multiswe.patch import share_image
    recipe = shared_recipe()
    recipe.update(repo='fastjson2')
    recipe['files']['prepare.sh'] = '#!/bin/bash\n./mvnw -Pgen-javadoc -Pgen-dokka clean package -Dmaven.test.skip=false || true\n'
    recipe['files']['fix-run.sh'] = '#!/bin/bash\ngit apply /home/test.patch /home/fix.patch\n./mvnw -Pgen-javadoc -Pgen-dokka clean test -Dmaven.test.skip=false\n'
    files = share_image(patch_files(original_files()), recipe,
                        {recipe['base']:'mswebench/example@sha256:'+'b'*64})
    assert b'./mvnw' not in files['setup_files/upstream/prepare.sh']
    assert b'MAVEN_USER_HOME=/opt/multiswe-maven-user' in files['environment/Dockerfile']
    verifier = files['setup_files/upstream/fix-run.sh']
    assert b'-Dmaven.repo.local=/opt/multiswe-maven' in verifier
    assert b'-Pgen-javadoc -Pgen-dokka clean test -Dmaven.test.skip=false' in verifier
    assert b'-DskipTests' not in verifier


@pytest.mark.parametrize('fail', [False, True])
def test_maven_wrapper_proxy_reaches_bootstrap_and_is_restored(monkeypatch, fail):
    properties = embedded_helper("gradle_proxy").properties
    maven_wrapper_proxy = embedded_helper("maven_proxy").maven_wrapper_proxy
    monkeypatch.setenv('MAVEN_OPTS', '-Xmx512m')
    monkeypatch.setenv('https_proxy', 'http://build-proxy.invalid:3128')
    monkeypatch.setenv('no_proxy', 'localhost,.example.invalid')
    try:
        with maven_wrapper_proxy(properties):
            options = subprocess.check_output(
                ['bash', '-c', 'printf "%s" "$MAVEN_OPTS"'], text=True)
            assert '-Xmx512m' in options
            assert '-Dhttps.proxyHost=build-proxy.invalid' in options
            assert '-Dhttps.proxyPort=3128' in options
            assert '*.example.invalid' in options
            if fail:
                raise RuntimeError('download failed')
    except RuntimeError:
        assert fail
    import os
    assert os.environ['MAVEN_OPTS'] == '-Xmx512m'


def test_junit_collection_repair_preserves_verifier_and_other_baselines():
    from data.multiswe.patch import share_image
    recipe = shared_recipe()
    recipe.update(repo='junit5', base_sha='511ab7d04dd5deec372157381b37a42003778b18')
    recipe['files']['prepare.sh'] = 'set -e\n./gradlew test\n'
    recipe['files']['fix-run.sh'] = 'set -e\ngit apply /home/test.patch /home/fix.patch\n./gradlew test\n'
    locks = {recipe['base']: 'mswebench/example@sha256:' + 'b'*64}
    repaired = share_image(patch_files(original_files()), recipe, locks)
    other = share_image(patch_files(original_files()), {**recipe, 'base_sha': 'a'*40}, locks)
    assert b'Test).toList().each' in repaired['environment/Dockerfile']
    assert b'--stacktrace --no-daemon --max-workers 1' in repaired['environment/Dockerfile']
    assert b'Test).toList().each' not in other['environment/Dockerfile']
    assert repaired['setup_files/upstream/fix-run.sh'] == other['setup_files/upstream/fix-run.sh']
    assert repaired['solution/solve.sh'] == other['solution/solve.sh']
    assert repaired['tests/test.sh'] == other['tests/test.sh']
    for line in repaired['environment/Dockerfile'].splitlines():
        if line.startswith(b'RUN '):
            subprocess.run(['sh', '-n'], input=line[4:], check=True)


def test_prepared_grader_keeps_repository_toolchains_and_harness_repairs():
    import hashlib
    from data.multiswe.patch import prepare_verifier_runtime, rewrite_blob, blob_files
    files = {
        'environment/Dockerfile': b'FROM original\n',
        'tests/test.sh': b'#!/bin/bash\nset -euo pipefail\nexport PATH=/repo/tools:/usr/bin\ninstall -m 0644 /tests/upstream/fix-run.sh /home/fix-run.sh\nbash /home/fix-run.sh > /logs/verifier/test_output.txt 2>&1 || true\n/opt/multiswe-python/bin/python3 -I /tests/grade.py /tests/row.json /logs/verifier/test_output.txt /logs/verifier/reward.json\n',
        'tests/extract_fix_patch.sh': b'#!/bin/bash\nexec /opt/multiswe-python/bin/python3 /tests/extract_fix_patch.py "$@"\n',
        'tests/extract_fix_patch.py': b'import subprocess\nsubprocess.run(["git", "diff"], check=True)\n',
        'tests/grade.py': b'from multi_swe_bench.harness.dataset import Dataset\n',
        'tests/upstream/fix-run.sh': b'node repo-test.js\n',
        'setup_files/setup.sh': b'bash /home/prepare.sh\n',
        'solution/solve.sh': b'git apply /solution/fix.patch\n',
    }
    distributions = {line.split('==')[0]: line.split('==')[1]
                     for line in EMBEDDED_FILES['grader-requirements.txt'].splitlines() if '==' in line}
    distributions['multi-swe-bench'] = '1.1.2'
    observed = {'verified_bundle': True, 'probe_returncode': 0, 'python': '3.11.13',
                'executable': '/opt/multiswe-python/bin/python3', 'image_sha256': 'a' * 64,
                'dockerfile_sha256': hashlib.sha256(files['environment/Dockerfile']).hexdigest(),
                'distributions': distributions}
    blob = rewrite_blob([], files)
    result, spec = prepare_verifier_runtime(blob, observed, observed)
    patched = blob_files(result)
    for name in files:
        if name not in ('tests/test.sh', 'tests/extract_fix_patch.sh'):
            assert patched[name] == files[name]
    assert b'export PATH=/repo/tools:/usr/bin' in patched['tests/test.sh']
    assert b'install -m 0644 /tests/upstream/fix-run.sh /home/fix-run.sh' in patched['tests/test.sh']
    assert b'bash /home/fix-run.sh > /logs/verifier/test_output.txt 2>&1 || true' in patched['tests/test.sh']
    assert b'OT_VERIFIER_PYTHON' in patched['tests/extract_fix_patch.sh']
    assert b'= "--prepare" ]' in patched['tests/setup.sh']
    assert spec['python'] == '3.11.13'
    assert any('multi_swe_bench-1.1.2.tar.gz#sha256=' in p for p in spec['requirements'])
    with pytest.raises(ValueError, match='exact observed Python'):
        prepare_verifier_runtime(blob, observed, {**observed, 'python': '3.11.14'})
    with pytest.raises(ValueError, match='dependencies differ'):
        prepare_verifier_runtime(blob, {**observed, 'distributions': {}}, observed)
    with pytest.raises(ValueError, match='existing MultiSWE verifier setup'):
        prepare_verifier_runtime(result, observed, observed)
