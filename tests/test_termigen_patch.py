import pytest

from data.termigen import patch as P

BASE = b"FROM x\nRUN pip3 install pandas==2.3.2 scipy==1.16.1 pytest==8.4.1\n"


def test_r2_pins_from_same_dockerfile_and_numpy_keeping_other_bytes():
    original = BASE + b"RUN pip3 install pandas numpy \\\n    scipy && echo hi\nCOPY a /w/\n"
    new, rule, replacements = P.pin_dockerfile(original, {})
    assert rule == "R2"
    assert new == BASE + b"RUN pip3 install pandas==2.3.2 numpy==%s \\\n    scipy==1.16.1 && echo hi\nCOPY a /w/\n" % P.NUMPY_PIN.encode()
    assert replacements == {"pandas": "pandas==2.3.2", "numpy": f"numpy=={P.NUMPY_PIN}", "scipy": "scipy==1.16.1"}
    assert not P.flagged(new.decode())[0]


def test_r2_skips_dockerfile_with_other_unpinned_package():
    assert P.pin_dockerfile(BASE + b"RUN pip install torch numpy\n", {}) is None


def test_r2_conflicting_pins_raise():
    with pytest.raises(ValueError):
        P.pin_dockerfile(BASE + b"RUN pip install pandas==1.0\nRUN pip install pandas\n", {})


def test_r3_uses_lock_only_when_every_name_is_locked():
    original = BASE + b"RUN pip install torch 'dask[complete]' yara-python\n".replace(b"'", b"")
    key = P.sha256(original)
    partial = {key: {"versions": {"torch": "2.0.0"}}}
    assert P.pin_dockerfile(original, partial) is None
    full = {key: {"versions": {"torch": "2.0.0", "dask": "1.0", "yara_python": "4.5.4"}}}
    new, rule, _ = P.pin_dockerfile(original, full)
    assert rule == "R3" and b"torch==2.0.0 dask[complete]==1.0 yara-python==4.5.4" in new


def test_r3_locked_version_must_satisfy_specifier():
    original = BASE + b"RUN pip install torch>=2.1\n"
    with pytest.raises(ValueError):
        P.pin_dockerfile(original, {P.sha256(original): {"versions": {"torch": "1.0"}}})


def test_r1_copy_sources_ignore_globs_urls_and_stages():
    text = "COPY --from=b /x /y\nCOPY a.pyc b/ /w/\nCOPY *.txt /w/\nADD http://h/f /w/\n"
    assert list(P.copy_sources(text)) == ["a.pyc", "b"]


@pytest.mark.parametrize('include_repairs', [True, False])
def test_native_baseline_omits_instruction_repairs_but_preserves_agent_initialization(tmp_path, monkeypatch, include_repairs):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.harbor_parquet import pack_task
    instruction = b'Run /workspace/setup_data.sh to generate inputs.\n'
    files = {'instruction.md': instruction, 'task.toml': b'[agent]\ntimeout_sec=900\n',
             'environment/Dockerfile': b'FROM ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624\n',
             'tests/test.sh': b'pytest /tests/test_outputs.py\n',
             'tests/test_outputs.py': b'def test_task(): assert True\n'}
    source = tmp_path / 'upstream.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': 'task', 'task_binary': pack_task(files)}]), source)
    monkeypatch.setattr(P, 'load_upstream', lambda path: {})
    report = P.convert_native(source, tmp_path / 'out', tmp_path / 'unused.zip', include_repairs=include_repairs)
    row = pq.read_table(tmp_path / 'out/tasks.parquet').to_pylist()[0]
    result = {m.name: b for m, b in P.read_task(row['task_binary']) if b is not None}
    assert result['instruction.md'].startswith(instruction)
    assert (b'You have 900 seconds' in result['instruction.md']) is include_repairs
    assert result['environment/Dockerfile'] == files['environment/Dockerfile']
    assert report['include_repairs'] is include_repairs
    import tomllib
    assert tomllib.loads(result['task.toml'].decode())['environment']['allow_internet'] is False


@pytest.mark.parametrize('config', [b'[agent]\ntimeout_sec=900\n',
    b'[environment]\nallow_internet = true # upstream online\n[agent]\ntimeout_sec=900\n',
    b'[environment]\nallow_internet=false\n', b'[environment]\n'])
def test_offline_first_overrides_explicit_online_and_unset(config):
    import tomllib
    from data.termigen.network import offline_config
    result = offline_config(config)
    assert tomllib.loads(result.decode())['environment']['allow_internet'] is False
    assert offline_config(result) == result


def test_historical_instruction_repair_rejects_changed_measurement_input():
    from data.termigen.repairs import repair_instruction
    with pytest.raises(ValueError, match='input changed'):
        repair_instruction('benchmark_criterion_rust_hard', b'A different task\n', b'[agent]\ntimeout_sec=900\n')


def test_upstream_materialization_rejects_unpinned_archive_before_writing(tmp_path):
    from data.termigen.source_inputs import prepare
    archive = tmp_path / 'source.tar.gz'
    archive.write_bytes(b'not the pinned public source')
    with pytest.raises(ValueError, match='checksum'):
        prepare(archive, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()
