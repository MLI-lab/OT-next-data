import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from harbor_patches import agent_package_fallback as fallback


def run(python, code, *flags):
    return subprocess.run([str(python), *flags, '-c', code], capture_output=True, text=True, check=True)


def venv(path):
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(path)], check=True)
    python = path / 'bin/python3'
    site = Path(run(python, 'import site; print(site.getsitepackages()[0])', '-I').stdout.strip())
    return python, site


def probe(python):
    p = run(python, fallback.PROBE, '-I', '-S')
    return fallback.parse_probe(SimpleNamespace(return_code=p.returncode, stdout=p.stdout, stderr=p.stderr))


def install(root, records):
    subprocess.run(fallback.install_command(str(root), records), shell=True, check=True,
                   executable='/bin/bash', capture_output=True, text=True)


def test_clean_precedence_agent_edits_and_no_agent_startup_hooks(tmp_path):
    agent, site = venv(tmp_path / 'agent env')
    clean, clean_site = venv(tmp_path / 'clean')
    marker = tmp_path / 'startup-ran'
    poison = 'open(%r, "a").write("ran")' % str(marker)
    (site / 'evil.pth').write_text('import pathlib; ' + poison + '\n')
    for name in ('sitecustomize', 'usercustomize'):
        (site / (name + '.py')).write_text(poison)
    (site / 'grading_tool.py').write_text('value = "tampered"')
    (clean_site / 'grading_tool.py').write_text('value = "clean"')
    (site / 'task_package.py').write_text('value = "agent edit"')
    records = probe(agent)
    assert str(site) in records[0]['directories']  # includes venv with -S on 3.12
    assert not marker.exists()
    install(clean.parent.parent, records)
    code = '''import grading_tool, task_package, sys
print(grading_tool.value, task_package.value)
assert not any(p.endswith('agent env/lib/python%d.%d/site-packages' % sys.version_info[:2]) for p in sys.path)
'''
    assert run(clean, code, '-I').stdout.strip() == 'clean agent edit'
    (site / 'task_package.py').write_text('value = "changed agent package"')
    assert 'changed agent package' in run(clean, code, '-I').stdout
    assert not marker.exists()


def test_clean_namespace_not_extended_and_clean_import_error_not_hidden(tmp_path):
    agent, site = venv(tmp_path / 'agent')
    clean, clean_site = venv(tmp_path / 'clean')
    for directory in (site, clean_site):
        (directory / 'shared_namespace').mkdir()
    (clean_site / 'shared_namespace/clean.py').write_text('value = 1')
    (site / 'shared_namespace/injected.py').write_text('value = 2')
    (clean_site / 'broken.py').write_text('raise ImportError("clean failure")')
    (site / 'broken.py').write_text('value = "tampered"')
    (site / 'agent_namespace').mkdir()
    (site / 'agent_namespace/module.py').write_text('value = 3')
    install(clean.parent.parent, probe(agent))
    code = '''
import shared_namespace.clean, agent_namespace.module
assert agent_namespace.module.value == 3
try:
    import shared_namespace.injected
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('agent extended clean namespace')
try:
    import broken
except ImportError as e:
    assert str(e) == 'clean failure'
else:
    raise AssertionError('fell back after clean import failed')
'''
    run(clean, code, '-I')


def test_incompatible_interpreter_paths_are_not_loaded(tmp_path):
    agent, site = venv(tmp_path / 'agent')
    clean, _ = venv(tmp_path / 'clean')
    (site / 'agent_only.py').write_text('value = 1')
    records = probe(agent)
    records[0]['version'] = [3, 99, 0]
    install(clean.parent.parent, records)
    run(clean, 'import importlib.util; assert importlib.util.find_spec("agent_only") is None', '-I')


def test_named_metadata_works_without_advertising_agent_plugins(tmp_path):
    agent, site = venv(tmp_path / 'agent')
    clean, clean_site = venv(tmp_path / 'clean')
    for directory, name, version in [(site, 'agent_only', '1.0'),
                                      (site, 'grading_tool', '666'),
                                      (clean_site, 'grading_tool', '1.0')]:
        metadata = directory / (name + '-' + version + '.dist-info')
        metadata.mkdir()
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + name + '\nVersion: ' + version + '\n')
        (metadata / 'entry_points.txt').write_text('[pytest11]\n' + name + ' = evil_plugin\n')
    install(clean.parent.parent, probe(agent))
    run(clean, '''from importlib import metadata
assert metadata.version('agent_only') == '1.0'
assert metadata.version('grading_tool') == '1.0'
assert 'agent_only' not in [d.metadata['Name'] for d in metadata.distributions()]
''', '-I')


def test_real_pytest_uses_clean_tools_and_evaluates_agent_package(tmp_path):
    import importlib.util
    agent, agent_site = venv(tmp_path / 'agent')
    clean, clean_site = venv(tmp_path / 'clean')
    for name in ('pytest', '_pytest', 'pluggy', 'packaging', 'pygments', 'iniconfig', 'py', 'attr', 'attrs'):
        spec = importlib.util.find_spec(name)
        if spec.submodule_search_locations:
            source = Path(next(iter(spec.submodule_search_locations)))
            (clean_site / name).symlink_to(source, target_is_directory=True)
        else:
            (clean_site / (name + '.py')).symlink_to(spec.origin)
    (agent_site / 'pytest.py').write_text('raise RuntimeError("agent pytest must not run")')
    (agent_site / 'task_package.py').write_text('value = 42')
    install(clean.parent.parent, probe(agent))
    test = tmp_path / 'test_agent_work.py'
    test.write_text('import task_package\ndef test_agent_edit():\n    assert task_package.value == 42\n')
    def grade():
        return subprocess.run([str(clean), '-I', '-m', 'pytest', '-q', str(test)],
                              capture_output=True, text=True, cwd=tmp_path)
    passed = grade()
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert '1 passed' in passed.stdout
    (agent_site / 'task_package.py').write_text('value = -123')
    failed = grade()
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert '1 failed' in failed.stdout


def test_discovery_failure_is_reported(tmp_path):
    class Env:
        async def exec(self, *a, **kw):
            return SimpleNamespace(return_code=1, stdout='', stderr='broken agent python')
    with pytest.raises(RuntimeError, match='discovery failed'):
        asyncio.run(fallback.prepare(Env(), {}, tmp_path, tmp_path))
    assert json.loads((tmp_path / 'agent-package-fallback.json').read_text())['status'] == 'error'


def test_no_python_is_recorded_as_unavailable(tmp_path):
    class Env:
        async def exec(self, *a, **kw):
            return SimpleNamespace(return_code=0, stdout='', stderr='')
    asyncio.run(fallback.prepare(Env(), {}, tmp_path, tmp_path))
    assert json.loads((tmp_path / 'agent-package-fallback.json').read_text())['status'] == 'unavailable'


@pytest.mark.parametrize('image', [False, True])
def test_prepare_records_paths_and_installs_in_private_runtime(tmp_path, monkeypatch, image):
    from harbor_patches import verifier_mount
    agent, site = venv(tmp_path / 'agent')
    root = tmp_path / 'runtime'
    clean, _ = venv(root / ('active' if image else 'python'))
    (site / 'agent_only.py').write_text('value = "agent"')
    context = tmp_path / 'tests'
    context.mkdir()
    (context / 'ot-verifier-assets.json').write_text(json.dumps({'task_python': str(agent)}))
    wrapped = []
    def mount(bundle, command):
        wrapped.append(bundle)
        return command
    monkeypatch.setattr(verifier_mount, 'command', mount)
    class Env:
        async def exec(self, command, **kwargs):
            p = subprocess.run(command, shell=True, capture_output=True, text=True, executable='/bin/bash')
            return SimpleNamespace(return_code=p.returncode, stdout=p.stdout, stderr=p.stderr)
    asyncio.run(fallback.prepare(Env(), {'directory': str(root), 'image_backed': image}, context, tmp_path))
    assert json.loads((tmp_path / 'agent-package-fallback.json').read_text())['status'] == 'installed'
    assert bool(wrapped) == image
    if image:
        assert wrapped[0]['mount_mode'] == 'writable'
    assert run(clean, 'import agent_only; print(agent_only.value)', '-I').stdout.strip() == 'agent'
