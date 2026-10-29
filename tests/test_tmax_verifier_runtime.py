"""TMAX prepared verifier runtime planning and verifier rewrites."""
import json
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
    record = {"return_code": 0, "version": "3.10.12", "python3": ["/usr/bin/python3", "/usr/bin/python3.10"],
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


def test_ubuntu_packaged_modules_come_from_the_task_environment():
    planned = vr.plan(task("import h5py\n"), image())
    assert planned["task_environment"] and planned["excluded"] == ["h5py.-debian-h5py-serial"]
    assert any("Ubuntu-packaged" in reason for reason in planned["reasons"])


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
    assert reference["archive_sha256"] == "0" * 64 and reference["mount_mode"] == "readonly"
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
    files['environment/Dockerfile'] = (b'FROM example\n', 0o644)
    before = files_digest((name, value[0]) for name, value in files.items())
    observed = image(environment_sha256='environment', sif_sha256='a' * 64)
    planned = vr.plan(files, observed)
    cache = tmp_path / 'cache'; cache.mkdir()
    key = spec_id(planned['spec'])
    archive = cache / (key + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        entry = tarfile.TarInfo('python/bin/python3'); entry.size = 1
        tar.addfile(entry, io.BytesIO(b'x'))
    (cache / (key + '.json')).write_text(json.dumps(dict(format=FORMAT, id=key, spec=planned['spec'],
        archive_sha256=digest(archive), status='ready', resolved_requirements=planned['spec']['requirements'])))
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
    bound = json.loads(result.schema.metadata[b'ot.images.v1'])
    assert bound['tasks']['task_1']['content_sha256'] == new['content_sha256']
    assert json.loads(result.schema.metadata[b'ot.runtime_migration.v1'])['effective_network'] == 'unverified'
    history = json.loads((output / 'historical-evidence.json').read_text())
    assert history['tasks'][0]['content_sha256'] == before and history['tasks'][0]['stages_passed'] == '1,3,4,5'
    assert (output / 'tasks.manifest.json').exists()


def test_wrapper_interpreter_is_called_before_grading():
    planned = vr.plan(task("import os\n"), image(python3=["/usr/bin/python3", "/usr/bin/python3"]))
    files = task("import os\n")
    from harbor_patches.verifier_bundle import FORMAT, spec_id
    vr.apply(files, planned, {spec_id(planned["spec"]): {"format": FORMAT, "id": spec_id(planned["spec"]),
                                                          "spec": planned["spec"], "status": "ready",
                                                          "archive_sha256": "1" * 64}})
    assert files["tests/test.sh"][0].decode().count("python3 -c '' >/dev/null 2>&1 || true\n") == 1


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
