"""TMAX prepared verifier runtime planning and verifier rewrites."""
import json
import os
import io
import tarfile
import subprocess
import sys

import pytest

from data.tmax import verifier_runtime as vr

TEST_SH = ("set -e\nmkdir -p /logs/verifier\ncd /tests\n" + vr.UPSTREAM_COMMAND
           + "\nTEST_EXIT=${PIPESTATUS[0]}\n")


def image(**extra):
    distributions = {
        "pytest": {"version": "9.1.1", "requires": ["pluggy<2,>=1.5", "iniconfig>=1", "tomli>=1; python_version < \"3.11\"",
                                                    "pygments>=2.7.2; extra == \"dev\""],
                   "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "pluggy": {"version": "1.7.0", "requires": [], "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "iniconfig": {"version": "2.3.1", "requires": [], "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "tomli": {"version": "2.5.0", "requires": [], "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "Pygments": {"version": "2.21.0", "requires": [], "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "pytest-timeout": {"version": "2.4.0", "requires": ["pytest>=7"], "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "pandas": {"version": "2.3.3", "requires": ["numpy>=1.22", "openpyxl>=3; extra == \"excel\"", "xlrd; extra == \"excel\""],
                   "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "numpy": {"version": "2.2.6", "requires": [], "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "openpyxl": {"version": "3.0.9", "requires": [], "location": "/usr/local/lib/python3.10/dist-packages", "installer": "pip"},
        "h5py.-debian-h5py-serial": {"version": "3.6.0", "requires": [], "location": "/usr/lib/python3/dist-packages", "installer": ""},
        "logparser": {"version": "1.0", "requires": [], "location": "/app/vendored_logparser", "installer": ""},
        "python-apt": {"version": "2.4.0+ubuntu1", "requires": [], "location": "/usr/lib/python3/dist-packages", "installer": ""},
    }
    record = {"return_code": 0, "version": "3.10.12", "sif_sha256": "a" * 64,
              "python3": ["/usr/bin/python3", "/usr/bin/python3.10"],
              "path": ["", "/usr/lib/python310.zip", "/usr/lib/python3.10", "/usr/local/lib/python3.10/dist-packages",
                       "/usr/lib/python3/dist-packages"],
              "distributions": distributions,
              "modules": {"pytest": ["pytest"], "pandas": ["pandas"], "numpy": ["numpy"], "openpyxl": ["openpyxl"],
                          "logparser": ["logparser"], "apt": ["python-apt"],
                          "h5py": ["h5py.-debian-h5py-serial"]}}
    record.update(extra)
    return record


def task(test, instruction="Write the report."):
    return {"tests/test_final_state.py": (test.encode(), 0o644), "tests/test.sh": (TEST_SH.encode(), 0o755),
            "instruction.md": (instruction.encode(), 0o644)}


def test_runtime_pins_image_versions_without_development_extras():
    planned = vr.plan(task("import os\nimport pandas\n"), image())
    assert not planned["task_environment"]
    assert planned["spec"]["python"] == "3.10.12"
    # pytest's dev extra (Pygments) stays out; pandas' installed optional openpyxl is kept.
    assert planned["spec"]["requirements"] == sorted([
        "iniconfig==2.3.1", "numpy==2.2.6", "openpyxl==3.0.9", "pandas==2.3.3", "pluggy==1.7.0",
        "pytest-timeout==2.4.0", "pytest==9.1.1", "tomli==2.5.0"])


def test_unsatisfied_optional_extra_is_not_a_dependency_but_direct_import_is():
    observed = image()
    observed['distributions']['pandas']['requires'].append('openpyxl>=3.1; extra == "excel"')
    observed['distributions']['pandas']['requires'].remove('openpyxl>=3; extra == "excel"')
    planned = vr.plan(task('import pandas\n'), observed)
    assert 'openpyxl==3.0.9' not in planned['spec']['requirements']
    planned = vr.plan(task('import pandas\nimport openpyxl\n'), observed)
    assert 'openpyxl==3.0.9' in planned['spec']['requirements']


@pytest.mark.parametrize("test,instruction,reason", [
    ("import my_solution\n", "Write it.", "does not install (my_solution)"),
    ("import sys\nsys.path.insert(0, '/app')\n", "Write it.", "loads task code by path"),
    ("import logparser\n", "Fix it.", "task's own files (logparser)"),
    ("import pandas\n", "Upgrade pandas to the latest release.", "install or change pandas"),
])
def test_task_environment_exceptions(test, instruction, reason):
    planned = vr.plan(task(test, instruction), image())
    assert planned["task_environment"] and any(reason in r for r in planned["reasons"])
    assert planned["paths"] == ["/usr/local/lib/python3.10/dist-packages", "/usr/lib/python3/dist-packages"]
    assert "pandas==2.3.3" not in planned["spec"]["requirements"] or "pandas" not in instruction


def test_ubuntu_packaged_modules_come_from_pristine_image():
    planned = vr.plan(task("import h5py\n"), image())
    assert not planned["task_environment"] and planned["excluded"] == []
    assert planned['spec']['image_python']['packages'] == {'h5py.-debian-h5py-serial==3.6.0': ['h5py']}
    assert planned['spec']['install_no_deps'] is True


def test_pypi_module_shadows_differently_named_distro_distribution():
    observed = image()
    observed['distributions']['h5py'] = dict(version='3.14.0', requires=['numpy>=1.19.3'],
        location='/usr/local/lib/python3.10/dist-packages', installer='pip')
    observed['modules']['h5py'].append('h5py')
    planned = vr.plan(task('import h5py\n'), observed)
    assert 'h5py==3.14.0' in planned['spec']['requirements']
    assert 'numpy==2.2.6' in planned['spec']['requirements']
    assert not any('debian' in pin for pin in planned['spec']['requirements'])
    assert 'image_python' not in planned['spec']


def test_distro_metadata_omissions_use_image_versions():
    observed = image()
    observed['distributions'].update({
        'html5lib': dict(version='1.1', requires=[], installer='', location='/usr/lib/python3/dist-packages'),
        'six': dict(version='1.17.0', requires=[], installer='pip', location='/usr/local/lib/python3.10/dist-packages'),
        'webencodings': dict(version='0.6.1', requires=[], installer='pip', location='/usr/local/lib/python3.10/dist-packages'),
    })
    observed['modules']['html5lib'] = ['html5lib']
    planned = vr.plan(task('import html5lib\n'), observed)
    assert {'html5lib==1.1', 'six==1.17.0', 'webencodings==0.6.1'} <= set(planned['spec']['requirements'])
    assert planned['spec']['image_python']['packages'] == {'html5lib==1.1': ['html5lib']}
    del observed['distributions']['six']
    with pytest.raises(ValueError, match='lacks distro dependency six'):
        vr.plan(task('import html5lib\n'), observed)


def test_packages_needing_an_excluded_package_are_excluded_too():
    planned = vr.plan(task("import pandas\n", "Upgrade numpy to 2.3 before running."), image())
    assert planned["excluded"] == ["numpy", "pandas"]
    assert not any(pin.startswith(("numpy==", "pandas==")) for pin in planned["spec"]["requirements"])


def test_english_mentions_are_not_install_requests():
    assert vr.mentioned_changes("The server handles requests from clients.", {"requests"}) == set()
    assert vr.mentioned_changes("Install requests==2.31 first.", {"requests"}) == {"requests"}


def test_sys_executable_rewrite_is_exact():
    source = ('import subprocess, sys\n'
              'subprocess.run([sys.executable, "/app/run.py"])\n'
              'cmd = f"{sys.executable} -c pass"\n')
    rewritten = vr.replace_executable(source, "/usr/bin/python3")
    assert "sys.executable" not in rewritten
    assert "f\"{'/usr/bin/python3'} -c pass\"" in rewritten
    compile(rewritten, "test", "exec")


def test_apply_writes_reference_test_sh_and_plugin():
    files = task("import sys, my_solution\nprint(sys.executable)\n")
    planned = vr.plan(files, image())
    catalog = {}
    from harbor_patches.verifier_bundle import FORMAT, spec_id
    catalog[spec_id(planned["spec"])] = {"format": FORMAT, "id": spec_id(planned["spec"]), "spec": planned["spec"],
                                          "status": "ready", "archive_sha256": "0" * 64}
    labels = vr.apply(files, planned, catalog)
    assert labels == [vr.LABEL, vr.TASK_ENVIRONMENT_LABEL, vr.EXECUTABLE_LABEL]
    reference = json.loads(files["tests/ot-verifier-runtime.json"][0])
    assert reference["archive_sha256"] == "0" * 64 and reference["mount_mode"] == "writable"
    script = files["tests/test.sh"][0].decode()
    assert vr.UPSTREAM_COMMAND not in script
    assert vr.RUNTIME + " test_final_state.py -v" in script
    assert "/usr/lib/python3/dist-packages" in files[vr.CONFTEST][0].decode()
    assert 'print("/usr/bin/python3")' in files["tests/test_final_state.py"][0].decode()
    assert '= "--prepare" ]' in files['tests/setup.sh'][0].decode()


def test_preparation_recipe_keeps_existing_initialization_and_allows_trusted_fallback(tmp_path):
    from harbor_patches.verifier_bundle import reference, setup_recipe, REFERENCE
    files = task('import pytest\n')
    files['tests/setup.sh'] = (b'#!/bin/bash\nset -e\necho original-initialization\n', 0o755)
    planned = vr.plan(files, image())
    vr.apply(files, planned, None)
    for name, (contents, mode) in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    context = tmp_path / 'tests'
    assert setup_recipe(context, reference(context, REFERENCE)) == planned['spec']
    setup = files['tests/setup.sh'][0].decode()
    assert setup.index('= "--prepare" ]') < setup.index('echo original-initialization')


def test_published_migration_preserves_membership_and_records_history(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.harbor_parquet import pack_task
    from harbor_patches.verifier_bundle import FORMAT, spec_id, digest
    from validation.contract import files_digest
    files = task('import pytest\n')
    files['task.toml'] = (b'[environment]\nallow_internet = true\n', 0o644)
    files['environment/Dockerfile'] = (b'FROM example\n', 0o644)
    before = files_digest((name, value[0]) for name, value in files.items())
    observed = image(environment_sha256='environment', sif_sha256='a' * 64)
    planned = vr.plan(files, observed)
    cache = tmp_path / 'cache'; cache.mkdir()
    key = spec_id(planned['spec'])
    archive = cache / (key + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        entry = tarfile.TarInfo('python/bin/python3'); entry.size = 1; entry.mode = 0o755
        tar.addfile(entry, io.BytesIO(b'x'))
    (cache / (key + '.json')).write_text(json.dumps(dict(format=FORMAT, id=key, spec=planned['spec'],
        archive_sha256=digest(archive), status='ready', resolved_requirements=planned['spec']['requirements'])))
    from harbor_patches.runtime_images import build
    build(json.loads((cache / (key + '.json')).read_text()), cache, processors=1)
    archive.unlink()
    (cache / (key + '.json')).unlink()
    inventory = tmp_path / 'inventory'; inventory.mkdir()
    (inventory / 'image.json').write_text(json.dumps(observed))
    images = dict(bundles={'bundle': dict(name='image.sif', files={'image.sif': dict(sha256='a' * 64)})},
                  tasks={'task_1': dict(content_sha256=before, environments=[dict(path='environment',
                      environment_sha256='environment', bundle='bundle')])})
    row = dict(path='task_1', task_binary=pack_task(files), content_sha256=before,
               stages_passed='1,3,4,5', historical_score=0.5)
    table = pa.Table.from_pylist([row]).replace_schema_metadata({b'ot.images.v1': json.dumps(images).encode()})
    source = tmp_path / 'source.parquet'; pq.write_table(table, source)
    output = tmp_path / 'migrated'
    vr.migrate_published(source, output, inventory, cache, 'b' * 40, 'tmax_v1')
    result = pq.read_table(output / 'tasks.parquet')
    new = result.to_pylist()[0]
    assert new['path'] == row['path'] and new['historical_score'] == 0.5
    assert new['stages_passed'] == '' and new['content_sha256'] != before
    import tomllib
    from data.tmax.patch import read_task
    migrated = read_task(new['task_binary'])
    assert tomllib.loads(migrated['task.toml'][0].decode())['environment']['allow_internet'] is False
    bound = json.loads(result.schema.metadata[b'ot.images.v1'])
    assert bound['tasks']['task_1']['content_sha256'] == new['content_sha256']
    assert json.loads(result.schema.metadata[b'ot.runtime_migration.v1'])['effective_network'] == 'unverified'
    history = json.loads((output / 'historical-evidence.json').read_text())
    assert history['tasks'][0]['content_sha256'] == before and history['tasks'][0]['stages_passed'] == '1,3,4,5'
    assert (output / 'tasks.manifest.json').exists()


def test_wrapper_interpreter_is_called_only_before_agent(tmp_path):
    planned = vr.plan(task("import os\n"), image(python3=["/usr/bin/python3", "/usr/bin/python3"]))
    files = task("import os\n")
    from harbor_patches.verifier_bundle import FORMAT, spec_id
    vr.apply(files, planned, {spec_id(planned["spec"]): {"format": FORMAT, "id": spec_id(planned["spec"]),
                                                          "spec": planned["spec"], "status": "ready",
                                                          "archive_sha256": "1" * 64}})
    assert "python3 -c" not in files["tests/test.sh"][0].decode()
    assert '/setup_files' not in files['tests/test.sh'][0].decode()
    setup = files['setup_files/setup.sh'][0]
    assert setup.count(b"python3 -c '' >/dev/null 2>&1\n") == 1
    # Preparation must neither start services nor invoke the task interpreter.
    script = tmp_path / 'setup.sh'
    script.write_bytes(setup.replace(b"python3 -c '' >/dev/null 2>&1", b'exit 37'))
    assert subprocess.run(['bash', str(script), '--prepare']).returncode == 0
    assert subprocess.run(['bash', str(script)]).returncode == 37


def test_task_prepare_does_not_initialize_or_save_task_state(tmp_path):
    files = task('import os\n')
    files['setup_files/setup.sh'] = (b'#!/bin/bash\nexit 23\n', 0o755)
    vr.apply(files, vr.plan(files, image()), None)
    script = tmp_path / 'setup.sh'
    script.write_bytes(files['setup_files/setup.sh'][0])
    assert subprocess.run(['bash', str(script), '--prepare']).returncode == 0
    assert subprocess.run(['bash', str(script)]).returncode == 23


def test_model_inputs_prepare_without_initialization_and_share_one_artifact(tmp_path, monkeypatch):
    from harbor_patches.verifier_bundle import FORMAT, OFFLINE_FORMAT, spec_id
    files = task("import os\nmodel = SentenceTransformer('all-MiniLM-L6-v2')\n")
    files['setup_files/task_install.sh'] = (
        b"model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')\n", 0o755)
    files['setup_files/setup.sh'] = (b'#!/bin/bash\nexit 43\n', 0o755)
    planned = vr.plan(files, image())
    model = planned['model_assets']['spec']
    records = {spec_id(spec): dict(format=fmt, spec=spec, id=spec_id(spec), status='ready', archive_sha256='b'*64)
               for spec,fmt in ((planned['spec'], FORMAT), (model, OFFLINE_FORMAT))}
    vr.apply(files, planned, records)
    task_ref = json.loads(files['setup_files/ot-task-runtime.json'][0])
    verifier_ref = json.loads(files['tests/ot-verifier-offline.json'][0])
    assert task_ref['id'] == verifier_ref['id'] == spec_id(model)
    assert 'spec' not in task_ref and 'spec' not in verifier_ref
    assert b"SentenceTransformer('/tests/.tmax-model')" in files['tests/test_final_state.py'][0]
    fake = tmp_path / 'python3'
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$PREP_LOG"\n')
    fake.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':/usr/bin:/bin')
    log = tmp_path / 'calls'
    monkeypatch.setenv('PREP_LOG', str(log))
    for phase, calls in (('setup_files', 1), ('tests', 2)):
        script = tmp_path / (phase + '.sh')
        script.write_bytes(files[phase + '/setup.sh'][0])
        log.write_text('')
        result = subprocess.run(['bash', str(script), '--prepare'], capture_output=True)
        assert result.returncode == 0, result.stderr
        assert len(log.read_text().splitlines()) == calls
        assert 'tmax_model_cache.py' not in log.read_text()


@pytest.mark.parametrize('kind', ['minilm', 'whisper', 'protein'])
def test_model_inputs_are_private_verified_and_have_online_fallback(tmp_path, monkeypatch, kind):
    import hashlib
    import urllib.request
    from data.tmax import model_assets
    cached = tmp_path / 'cached'
    (cached / 'sources').mkdir(parents=True)
    content = b'{"hidden_size":384}\n'
    source = cached / 'sources/config.json'
    source.write_bytes(content)
    request = dict(sources=[dict(
        url=f'https://huggingface.co/{model_assets.MODEL}/resolve/{model_assets.REVISION}/config.json',
        filename='config.json', sha256=hashlib.sha256(content).hexdigest())])
    name = 'config.json'
    if kind == 'whisper':
        name = 'tiny.en.pt'
        source = cached / 'sources' / name
        source.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        request['sources'] = [dict(filename=name, sha256=checksum,
            url=f'https://openaipublic.azureedge.net/main/whisper/models/{checksum}/{name}')]
    elif kind == 'protein':
        name = '1CRN.pdb'
        source = cached / 'sources' / name
        source.write_bytes(content)
        request['sources'] = [dict(filename=name, sha256=hashlib.sha256(content).hexdigest(),
                                  url=model_assets.PROTEIN_URL)]
    destination = tmp_path / 'private'
    script = model_assets.MATERIALIZE.replace("Path('/tests/.tmax-model')", f'Path({str(destination)!r})')
    script = script.replace("Path('/tests/.tmax-inputs')", f'Path({str(destination)!r})')
    def run(staged):
        monkeypatch.setattr(sys, 'argv', ['helper', 'verifier', str(staged)])
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))
        exec(compile(script, 'model-cache.py', 'exec'), {})
    def unavailable(*args, **kwargs):
        raise OSError('network unavailable')
    monkeypatch.setattr(urllib.request, 'urlopen', unavailable)
    run(cached)
    (destination / name).write_bytes(b'agent poison')
    run(cached)
    assert (destination / name).read_bytes() == source.read_bytes() == content
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(content))
    run(tmp_path / 'missing')
    assert (destination / name).read_bytes() == content
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'wrong bytes'))
    with pytest.raises(ValueError, match='checksum mismatch'):
        run(tmp_path / 'missing')
    assert not (destination / name).exists()
    monkeypatch.setattr(urllib.request, 'urlopen', unavailable)
    with pytest.raises(OSError, match='network unavailable'):
        run(tmp_path / 'missing')


def test_prober_uses_the_https_endpoint_required_by_the_task():
    source = ('import subprocess\nserver = "https://127.0.0.1:8443/health"\n'
              'public_corpus = "https://google.com"\n'
              'def probe(prober_path):\n'
              '    return subprocess.run([prober_path, "https://google.com"], timeout=10)\n')
    files = task(source)
    planned = vr.plan(files, image())
    assert planned['local_prober']
    vr.apply(files, planned, None)
    updated = files['tests/test_final_state.py'][0].decode()
    assert 'subprocess.run([prober_path, "https://127.0.0.1:8443/health"], timeout=10)' in updated
    assert 'public_corpus = "https://google.com"' in updated
    assert 'Google' in vr.reason(planned)


def test_protein_reference_uses_pinned_input_and_clean_scientific_runtime():
    from harbor_patches.verifier_bundle import FORMAT, OFFLINE_FORMAT, spec_id
    observed = image()
    observed['distributions']['scipy'] = dict(version='1.15.3', requires=['numpy>=1.23'],
        location='/usr/local/lib/python3.10/dist-packages', installer='pip')
    observed['modules']['scipy'] = ['scipy']
    source = r'''import os, subprocess
code = """
import numpy, scipy.stats, urllib.request
url = "https://files.rcsb.org/download/1CRN.pdb"
req = urllib.request.urlopen(url)
lines = req.read().decode('utf-8').split('\\n')
"""
def calculate():
    return subprocess.run(["python3", "-c", code], capture_output=True)
'''
    files = task(source, instruction='Ensure numpy and scipy are installed to run your analysis script.')
    files['setup_files/setup.sh'] = (b'#!/bin/bash\nexit 0\n', 0o755)
    planned = vr.plan(files, observed)
    assert {'numpy==2.2.6', 'scipy==1.15.3'} <= set(planned['spec']['requirements'])
    assert not planned['task_environment']
    records = {spec_id(spec): dict(format=fmt, spec=spec, id=spec_id(spec), status='ready', archive_sha256='b'*64)
               for spec,fmt in ((planned['spec'], FORMAT), (planned['model_assets']['spec'], OFFLINE_FORMAT))}
    vr.apply(files, planned, records)
    updated = files['tests/test_final_state.py'][0]
    assert b'urllib.request.urlopen' not in updated
    assert b'/tests/.tmax-inputs/1CRN.pdb' in updated
    assert b'subprocess.run([os.environ["OT_VERIFIER_PYTHON"], "-I", "-c", code]' in updated
    compile(updated, '<protein-test>', 'exec')


def test_plugin_appends_after_autoload_and_before_collection(tmp_path):
    # A package in the task environment registers a pytest11 plugin; it must not load,
    # while the test still imports the agent's module from there.
    site = tmp_path / "site"
    (site / "evil-1.0.dist-info").mkdir(parents=True)
    (site / "evil-1.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: evil\nVersion: 1.0\n")
    (site / "evil-1.0.dist-info" / "entry_points.txt").write_text("[pytest11]\nevil = evil_plugin\n")
    (site / "evil_plugin.py").write_text("def pytest_collection_modifyitems(items):\n    items.clear()\n")
    (site / "agent_module.py").write_text("VALUE = 42\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(vr.CONFTEST_SOURCE.format(paths=[str(site)]))
    (tests / "test_final_state.py").write_text("import agent_module\n\ndef test_value():\n    assert agent_module.VALUE == 42\n")
    result = subprocess.run([sys.executable, "-I", "-m", "pytest", "-q", "test_final_state.py"],
                            cwd=tests, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_reference_child_uses_clean_python_only_for_restored_tools(monkeypatch):
    from data.tmax import reference_tools
    calls = []
    class Recorder:
        def __init__(self, args, *positional, **kwargs):
            calls.append((args, kwargs))
    monkeypatch.setattr(subprocess, 'Popen', Recorder)
    monkeypatch.setenv('OT_VERIFIER_PYTHON', '/trusted/python/bin/python3')
    exec(reference_tools.child_environment(['/app/oracle.py']), {})
    subprocess.Popen(['/usr/bin/python3', '/app/oracle.py'])
    subprocess.Popen(['/usr/bin/python3', '/home/user/answer.py'])
    subprocess.Popen(['/usr/bin/python3', '/home/user/answer.py', '/app/oracle.py'])
    assert calls[0][0] == ['/trusted/python/bin/python3', '-I', '/app/oracle.py']
    assert calls[0][1]['env']['PATH'] == os.environ['PATH']
    assert calls[1] == (['/usr/bin/python3', '/home/user/answer.py'], {})
    assert calls[2] == (['/usr/bin/python3', '/home/user/answer.py', '/app/oracle.py'], {})


def test_reference_sources_preserve_paths_and_future_imports():
    from data.tmax import reference_tools
    from harbor_patches.verifier_assets import clean_script
    source = b'#!/usr/bin/env python3\n"""Oracle."""\nfrom __future__ import annotations\nimport helper\nprint(helper.VALUE)\n'
    files = task('import subprocess\nORACLE = "/app/oracle.py"\nAGENT = "/home/user/answer.py"\n')
    files['tests/setup.sh'] = (b'#!/bin/bash\nset -e\n', 0o755)
    files['setup_files/task_install.sh'] = (("cat > /app/oracle.py <<'EOF'\n" + source.decode() + "EOF\n"
        + "cat > /app/helper.py <<'EOF'\nVALUE = 42\nEOF\n").encode(), 0o755)
    planned = reference_tools.plan(files)
    assert planned['paths'] == ['/app/helper.py', '/app/oracle.py']
    assert 'helper' not in planned['imports']
    original_test = files[vr.TEST]
    assert reference_tools.apply(files, planned)
    assert files[vr.TEST] == original_test
    manifest = json.loads(files['tests/ot-verifier-assets.json'][0])
    assert manifest['paths'] == planned['paths']
    assert b'--remove-destination' in files['tests/setup.sh'][0]
    rewritten = clean_script(source, '/app/oracle.py', '/clean/bin/python3', [])
    compile(rewritten, 'oracle.py', 'exec')
    assert rewritten.startswith(b'#!/clean/bin/python3 -I\n')
    assert rewritten.index(b'from __future__') < rewritten.index(b'_ot_sys.path.append')


def test_reference_program_requested_by_instruction_is_not_restored():
    from data.tmax import reference_tools
    files = task('import subprocess\nORACLE = "/app/oracle.py"\n', instruction='Repair /app/oracle.py to implement the protocol.')
    planned = reference_tools.plan(files)
    assert planned['paths'] == []
    assert '/app/oracle.py' in planned['exceptions']


def test_reference_imports_are_included_in_clean_runtime():
    files = task('import subprocess\nORACLE = "/app/oracle.py"\n')
    files['setup_files/task_install.sh'] = (b"cat > /app/oracle.py <<'EOF'\nimport numpy\nEOF\n", 0o755)
    planned = vr.plan(files, image())
    assert 'numpy==2.2.6' in planned['spec']['requirements']
    from harbor_patches.verifier_bundle import spec_id, FORMAT
    key = spec_id(planned['spec'])
    catalog = {key: dict(format=FORMAT, spec=planned['spec'], id=key, status='ready', archive_sha256='a'*64)}
    vr.apply(files, planned, catalog)
    assert json.loads(files['tests/ot-verifier-assets.json'][0])['paths'] == ['/app/oracle.py']
    # Preparation exits before touching any per-trial reference state.
    setup = files['tests/setup.sh'][0].decode()
    assert setup.index('exit $?') < setup.index('Missing trusted verifier asset capture')


def test_reference_task_package_changes_are_still_visible():
    files = task('import subprocess\nORACLE = "/app/oracle.py"\n', instruction='Upgrade pandas to the latest release.')
    files['setup_files/task_install.sh'] = (b"cat > /app/oracle.py <<'EOF'\nimport pandas\nEOF\n", 0o755)
    planned = vr.plan(files, image())
    assert planned['task_environment']
    assert 'pandas==2.3.3' not in planned['spec']['requirements']


def test_reference_source_closure_uses_oracle_package_not_broken_task_package():
    from data.tmax import reference_tools as rt
    files = task('import subprocess\nORACLE = "/opt/oracle/main.py"\n')
    source = """cat > /app/custom/custom/__init__.py <<'EOF'
raise RuntimeError('agent must repair this')
EOF
cat > /opt/oracle/main.py <<'EOF'
import sys
sys.path.insert(0, '/opt/oracle')
import custom
EOF
cat > /opt/oracle/custom/__init__.py <<'EOF'
from .math import value
EOF
cat > /opt/oracle/custom/math.py <<'EOF'
import numpy
value = 42
EOF
"""
    files['setup_files/task_install.sh'] = (source.encode(), 0o755)
    planned = rt.plan(files)
    assert planned['paths'] == ['/opt/oracle/custom/__init__.py', '/opt/oracle/custom/math.py', '/opt/oracle/main.py']
    assert 'numpy' in planned['imports'] and 'custom' not in planned['imports']


def test_literal_generated_reference_imports_are_detected_without_execution():
    from data.tmax import reference_tools as rt
    files = task('import subprocess\nORACLE = "/app/oracle.py"\n')
    files['environment/post_install.sh'] = (b'''cat > /tmp/generate.py <<'EOF'
import os
raise RuntimeError('must never execute initialization during planning')
source = "import numpy\\nprint(numpy.__version__)\\n"
with open('/app/oracle.py', 'w') as stream:
    stream.write(source)
EOF
''', 0o755)
    assert 'numpy' in rt.plan(files)['imports']


def test_reference_comparison_is_not_an_agent_write_target():
    from data.tmax.reference_tools import task_owned
    assert not task_owned('Write a C program that replicates the behavior of /app/oracle.', '/app/oracle')
    assert not task_owned('Compile your program to /home/user/encoder. Its output must equal /app/oracle.', '/app/oracle')
    assert task_owned('Write a replacement script at `/home/user/new_oracle.py`.', '/home/user/new_oracle.py')
    assert task_owned('Your final solution must be a script located at `/home/user/fast_oracle.py`.', '/home/user/fast_oracle.py')


def test_reviewed_grader_does_not_capture_submitted_program(monkeypatch):
    from data.tmax import reference_tools as rt
    files = task('import subprocess\nsubprocess.run(["python3", "/app/check.py"])\n')
    files['setup_files/task_install.sh'] = (b'''cat > /app/check.py <<'EOF'
import numpy
import subprocess
subprocess.run(['python3', '/app/answer.py'])
EOF
cat > /app/answer.py <<'EOF'
raise RuntimeError('repair me')
EOF
''', 0o755)
    catalog = {rt.source_identity(files): {'programs': [
        dict(path='/app/check.py', role='reference', reason='provided grader'),
        dict(path='/app/answer.py', role='task_code', reason='repair target')]}}
    monkeypatch.setattr(rt, 'reviewed_programs', lambda: catalog)
    planned = rt.plan(files)
    assert planned['paths'] == ['/app/check.py']
    assert 'numpy' in planned['imports']
    assert planned['exceptions']['/app/answer.py'] == 'repair target'
    files['instruction.md'] = (b'Different task', 0o644)
    assert not rt.plan(files)['paths']


def test_repaired_package_members_remain_task_owned():
    from data.tmax.reference_tools import task_owned
    instruction = 'Fix the package at `/app/package`. Compare against `/opt/oracle/package`.'
    assert task_owned(instruction, '/app/package/core.py')
    assert not task_owned(instruction, '/opt/oracle/package/core.py')


def test_service_launcher_restored_without_capturing_package_under_repair(monkeypatch):
    from data.tmax import reference_tools as rt
    files = task('import subprocess\nsubprocess.run(["python3", "/app/start.py"])\n')
    files['setup_files/task_install.sh'] = (b'''cat > /app/start.py <<'EOF'
import service
service.serve()
EOF
cat > /app/service.py <<'EOF'
raise RuntimeError('repair me')
EOF
''', 0o755)
    monkeypatch.setattr(rt, 'reviewed_programs', lambda: {rt.source_identity(files): {'programs': [
        dict(path='/app/start.py', role='task_launcher', reason='starts repaired service')]}})
    planned = rt.plan(files)
    assert planned['paths'] == planned['task_launchers'] == ['/app/start.py']
    assert planned['imports'] == []
    files['tests/setup.sh'] = (b'#!/bin/bash\n', 0o755)
    rt.apply(files, planned)
    assert json.loads(files['tests/ot-verifier-assets.json'][0])['task_launchers'] == ['/app/start.py']
    assert 'frozenset([])' in files['tests/conftest.py'][0].decode()


def test_write_target_does_not_claim_sibling_reference_directory():
    from data.tmax.reference_tools import task_owned
    instruction = 'Write a program at `/home/user/answer.py`. Compare its output with `/home/user/oracle`.'
    assert task_owned(instruction, '/home/user/answer.py')
    assert not task_owned(instruction, '/home/user')
    assert not task_owned(instruction, '/home/user/oracle')
    assert not task_owned('Write a program at `/app/oracle_fast.py`.', '/app/oracle')


def test_image_scikit_image_alias_is_a_clean_grading_dependency():
    measured = image()
    measured['distributions']['scikit-image'] = dict(version='0.25.2', requires=['numpy>=1.24'],
        location='/usr/local/lib/python3.10/dist-packages', installer='pip')
    planned = vr.plan(task('from skimage.metrics import structural_similarity\n'), measured)
    assert 'scikit-image==0.25.2' in planned['spec']['requirements']
    assert 'skimage' not in planned['unresolved']
    assert not planned['task_environment']


def test_generated_schema_uses_clean_protobuf_and_preserves_submitted_module():
    measured = image()
    measured['distributions']['protobuf'] = dict(version='6.33.5', requires=[],
        location='/usr/local/lib/python3.10/dist-packages', installer='pip')
    measured['distributions']['google-auth'] = dict(version='2.62.0', requires=[],
        location='/usr/local/lib/python3.10/dist-packages', installer='pip')
    measured['modules']['google'] = ['google-auth']  # Namespace metadata can omit protobuf.
    measured['distributions']['grpcio'] = dict(version='1.78.0', requires=[],
        location='/usr/local/lib/python3.10/dist-packages', installer='pip')
    measured['modules']['grpc'] = ['grpcio']
    planned = vr.plan(task('import job_pb2\nimport job_pb2_grpc\n',
        instruction='Compile the protobuf schema /home/user/job.proto and compile the gRPC stubs.'), measured)
    assert 'protobuf==6.33.5' in planned['spec']['requirements']
    assert 'grpcio==1.78.0' in planned['spec']['requirements']
    assert planned['unresolved'] == ['job_pb2', 'job_pb2_grpc']
    assert planned['task_environment']
    assert planned['reference_tools']['paths'] == []


def test_native_package_subdirectory_is_covered_by_its_import_root():
    measured = image()
    measured['distributions']['pythran'] = dict(version='0.10.0', requires=[],
        location='/usr/lib/python3/dist-packages', installer='')
    for name, version in [('beniget', '0.4.1'), ('gast', '0.5.2'), ('ply', '3.11')]:
        measured['distributions'][name] = dict(version=version, requires=[],
            location='/usr/lib/python3/dist-packages', installer='')
        measured['modules'][name] = [name]
    for module in ('pythran', 'pythran/pythonic', 'omp'):
        measured['modules'][module] = ['pythran']
    planned = vr.plan(task('import pythran\n'), measured)
    assert planned['spec']['image_python']['packages']['pythran==0.10.0'] == ['omp', 'pythran']

    assert {'beniget==0.4.1', 'gast==0.5.2', 'ply==3.11'} <= set(planned['spec']['requirements'])


def test_no_repairs_retains_offline_prober_adaptation():
    source = ('import subprocess\nserver = "https://127.0.0.1:8443/health"\n'
              'def probe(prober_path):\n'
              '    return subprocess.run([prober_path, "https://google.com"], timeout=10)\n')
    files = task(source)
    planned = vr.plan(files, image(), with_repairs=False)
    assert planned.get('local_prober')
    vr.apply(files, planned, None)
    assert b'subprocess.run([prober_path, "https://127.0.0.1:8443/health"]' in files['tests/test_final_state.py'][0]
    assert 'HTTPS health endpoint' in vr.reason(planned)
