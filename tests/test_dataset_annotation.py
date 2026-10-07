import copy
import io
import json
from pathlib import Path
import tarfile
import tomllib

import pytest

from validation.publishing import annotation, publish
from test_validation_pipeline import publish_fixture


def task_blob(files=None, member=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, text in (files or {'instruction.md': 'Read this task.'}).items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if member:
            archive.addfile(member)
    return output.getvalue()


def answer():
    return {'dataset_name': 'Example', 'original_datasources': [],
            'domains': [{'label': 'Software Engineering', 'reason': 'Code tasks.'}],
            'capabilities': [],
            'expected_benchmark_transfer': [], 'no_transfer_reason': 'Insufficient overlap evidence.'}


def test_publish_cards_dry_run_and_commit(tmp_path, monkeypatch):
    _, contract, reports = publish_fixture(tmp_path)
    monkeypatch.setattr(publish, 'read', lambda _: contract)
    monkeypatch.setattr(publish, 'stage_reports', lambda _: reports)
    monkeypatch.setattr(publish, 'previous_rows', lambda *args: {})
    monkeypatch.setattr(publish, 'readme_exists', lambda *args: False)
    prompts = []
    def call(task, model):
        prompt = (task / 'instruction.md').read_text()
        prompts.append(prompt)
        assert '{{' not in prompt
        assert 'Full issue body SHA-256:' not in prompt
        assert '--- BEGIN SOURCE EXCERPT ---' not in prompt
        assert 'Retrieved:' not in prompt
        assert '### In Distribution' in prompt
        assert 'Working Memory:' in prompt
        assert 'Mean time-to-complete' not in prompt
        assert 'How do we handle in-distribution' not in prompt
        assert 'https://arxiv.org/abs/2305.01210' in prompt
        assert 'evidence_ids' not in prompt
        assert (task / 'setup_files/evidence/tasks').is_dir()
        return answer(), {'backend': 'test'}
    kwargs = dict(readme=True, annotation_runner=call, out=tmp_path / 'out', run_id='r')
    result = publish.publish(tmp_path, tmp_path / 'contract', 'test/repo', dry_run=True, **kwargs)
    assert len(prompts) == 2
    assert 'set-python/README.md' in result['files']
    assert 'set-java/annotation.json' not in result['files']
    text = (tmp_path / 'out/set-python/README.md').read_text()
    assert 'hypotheses, not measured' in text
    assert 'annotation.json' not in text
    assert 'Insufficient overlap evidence.' in text
    card = json.loads((tmp_path / 'out/set-python/annotation.json').read_text())
    assert len(card['provenance']['prompt_sha256']) == 64
    assert 'Full issue body SHA-256:' in card['provenance']['evaluation_snapshot_metadata']
    assert len(card['provenance']['evidence']['evidence']) == 2
    assert 'set-python/README.md' in result['description']
    import huggingface_hub
    commits, descriptions = [], []
    class Api:
        def create_commit(self, **kwargs):
            commits.append(kwargs)
            return type('Commit', (), {'pr_url': 'https://example.test/discussions/3',
                                      'pr_revision': 'refs/pr/3'})()
        def get_discussion_details(self, repo, number, **kwargs):
            assert repo == 'test/repo' and number == 3
            event = type('Comment', (), {'id': 'initial', 'content': commits[-1]['commit_description']})()
            return type('Discussion', (), {'events': [event]})()
        def edit_discussion_comment(self, repo, number, comment_id, content, **kwargs):
            assert comment_id == 'initial'
            descriptions.append(content)
    monkeypatch.setattr(huggingface_hub, 'HfApi', Api)
    publish.publish(tmp_path, tmp_path / 'contract', 'test/repo', **kwargs)
    assert commits[0]['create_pr'] is True
    assert '(set-python/README.md)' not in commits[0]['commit_description']
    assert 'https://huggingface.co/datasets/test/repo/blob/refs%2Fpr%2F3/set-python/README.md' in descriptions[0]
    assert (tmp_path / 'out/pull-request.md').read_text() == descriptions[0]
    assert 'set-python/README.md' in [op.path_in_repo for op in commits[0]['operations']]
    assert not any(op.path_in_repo.endswith('/annotation.json') for op in commits[0]['operations'])
    def broken(*args):
        result = answer()
        result['domains'][0]['label'] = 'invented'
        return result, {}
    with pytest.raises(ValueError, match='unknown'):
        publish.publish(tmp_path, tmp_path / 'contract', 'test/repo', **{**kwargs, 'annotation_runner': broken})
    assert len(commits) == 1


def test_existing_readme_is_preserved_without_model_call(tmp_path, monkeypatch):
    _, contract, reports = publish_fixture(tmp_path)
    monkeypatch.setattr(publish, 'read', lambda _: contract)
    monkeypatch.setattr(publish, 'stage_reports', lambda _: reports)
    monkeypatch.setattr(publish, 'previous_rows', lambda *args: {})
    monkeypatch.setattr(publish, 'readme_exists', lambda *args: True)
    def forbidden(*args, **kwargs):
        raise AssertionError('Existing READMEs must not be regenerated')
    result = publish.publish(tmp_path, tmp_path / 'contract', 'test/repo',
        dry_run=True, readme=True, annotation_runner=forbidden, out=tmp_path / 'out')
    assert not any(p.endswith(('README.md', 'annotation.json')) for p in result['files'])
    assert 'set-python/README.md' in result['description']


def test_force_readme_regenerates_existing_cards_in_proposed_files(tmp_path, monkeypatch):
    _, contract, reports = publish_fixture(tmp_path)
    monkeypatch.setattr(publish, 'read', lambda _: contract)
    monkeypatch.setattr(publish, 'stage_reports', lambda _: reports)
    monkeypatch.setattr(publish, 'previous_rows', lambda *args: {})
    monkeypatch.setattr(publish, 'readme_exists', lambda *args: True)
    calls = []
    def call(task, model):
        calls.append(task)
        return answer(), {'backend': 'test'}
    result = publish.publish(tmp_path, tmp_path / 'contract', 'test/repo', dry_run=True,
        readme=True, readme_force=True, annotation_runner=call, out=tmp_path / 'out')
    assert len(calls) == 2
    assert 'set-python/README.md' in result['files']
    assert 'set-java/README.md' in result['files']


def test_annotation_validation_and_exact_catalog():
    docs = {key: (annotation.ASSETS / name).read_text() for key, name in
            [('domains', 'domain_taxonomy.txt'), ('capabilities', 'capability_taxonomy.txt'), ('suite', 'evaluation_suite.txt')]}
    domains = annotation.codebook_labels(docs['domains'])
    capabilities = annotation.codebook_labels(docs['capabilities'])
    suite = annotation.catalog_labels(docs['suite'])
    assert len(domains) == 17 and len(capabilities) == 11 and len(suite) == 30
    def check(value):
        annotation.validate(value, domains, capabilities, suite)
    check(answer())
    for mutate in [lambda a: a['domains'][0].update(label='invented'),
                   lambda a: a.update(no_transfer_reason=None),
                   lambda a: a.update(original_datasources=[{'name': 'x', 'url': 'javascript:alert(1)'}])]:
        value = copy.deepcopy(answer()); mutate(value)
        with pytest.raises(ValueError):
            check(value)
    value = answer()
    value['expected_benchmark_transfer'] = [{'benchmark': 'Unknown benchmark', 'reason': 'Code.'}]
    value['no_transfer_reason'] = None
    with pytest.raises(ValueError):
        check(value)


def test_source_evidence_is_a_file_not_inlined(tmp_path):
    _, contract, reports = publish_fixture(tmp_path)
    tables, _, _ = publish.build(contract, reports, {})
    doc = tmp_path / 'source.txt'; doc.write_text('Literal {{DOMAIN_TAXONOMY}} in untrusted document.')
    def call(task, model):
        prompt = (task / 'instruction.md').read_text()
        assert 'Literal {{DOMAIN_TAXONOMY}}' not in prompt
        assert '/evidence/source/000/source.txt' in prompt
        assert (task / 'setup_files/evidence/source/000/source.txt').read_text() == doc.read_text()
        return answer(), {}
    annotation.generate({'set-python': tables['set-python']}, 'test', call, tmp_path / 'work', {'set-python': [doc]})


def test_explicit_validation_disclosures_survive_model_output(tmp_path):
    _, contract, reports = publish_fixture(tmp_path)
    tables, record, run_file = publish.build(contract, reports, {})
    doc = tmp_path / 'notes.json'
    note = 'The absolute-path check was excluded, not passed.'
    doc.write_text(json.dumps({'validation_disclosures': [note]}))
    cards = annotation.generate({'set-python': tables['set-python']}, 'test',
                                lambda task, model: (answer(), {}), tmp_path / 'work',
                                {'set-python': [doc]})
    text = cards['set-python']['readme']
    kept, archived = tables['set-python']
    assert note not in text
    assert 'Validation outcomes' not in text
    assert cards['set-python']['validation_disclosures'] == [note]
    publish.write(tables, record, run_file, tmp_path / 'release', cards)
    assert note in (tmp_path / 'release/pull-request.md').read_text()
    assert record['validation_disclosures']['set-python'] == [note]
    published = (tmp_path / 'release/set-python/README.md').read_text()
    assert published == text
    assert 'Archive reasons' not in published


def test_selected_benchmark_and_paper_render_without_mismatch():
    value = answer()
    value['original_datasources'] = [{'name': 'CrossCodeEval', 'url': 'https://arxiv.org/abs/2310.11248'}]
    value['expected_benchmark_transfer'] = [{'benchmark': 'HumanEval+', 'reason': 'Illustrative expected transfer.'}]
    value['no_transfer_reason'] = None
    annotation.validate(value, {'Software Engineering'}, set(), {'HumanEval+': 'in_distribution'})
    text = annotation.render(value, 'example')
    assert 'https://arxiv.org/abs/2310.11248' in text
    assert 'HumanEval+' in text
    assert 'Limitation:' not in text
    assert 'evidence_ids' not in text


def test_advisory_analysis_still_runs_without_tools(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'result': json.dumps(answer())}), stderr='')
    monkeypatch.setattr(subprocess, 'run', run)
    publish.run_llm('analysis')
    command = calls[-1][0]
    assert command[command.index('--tools') + 1] == ''


def test_taxonomy_inputs_and_multiple_label_display():
    for name in ('domain_taxonomy.txt', 'capability_taxonomy.txt'):
        body = (annotation.ASSETS / name).read_text().split('"""', 2)[-1].strip()
        assert all(': ' in line for line in body.splitlines())
        assert 'Local annotation' not in body
    value = answer()
    value['domains'].append({'label': 'Debugging', 'reason': 'Repair tasks.'})
    annotation.validate(value, {'Software Engineering', 'Debugging'}, set(), {})
    assert 'Software Engineering, Debugging' in annotation.render(value, 'example')


def test_sampling_is_random_reproducible_and_preserves_full_files(tmp_path):
    files = {'instruction.md': 'specific instruction', 'tests/test.sh': 'x' * 15000,
             'environment/Dockerfile': 'FROM ubuntu:24.04', 'solution/solve.sh': 'echo answer'}
    rows = [{'path': f'task-{i:03}', 'task_binary': task_blob(files)} for i in range(30)]
    first = annotation.evidence_for('set', rows, tmp_path / 'first', seed=42)
    second = annotation.evidence_for('set', list(reversed(rows)), tmp_path / 'second', seed=42)
    other = annotation.evidence_for('set', rows, tmp_path / 'other', seed=43)
    assert first == second
    assert len(first['sampled_task_ids']) == 10
    assert first['sampled_task_ids'] != other['sampled_task_ids']
    for name in first['sampled_task_ids']:
        for path, text in files.items():
            assert (tmp_path / 'first/tasks' / name / path).read_text() == text
    assert 'specific instruction' not in json.dumps(first)
    assert 'text' not in first['evidence'][0]


@pytest.mark.parametrize('count', [0, 1, 9, 10])
def test_small_datasets_include_all_kept_tasks(tmp_path, count):
    rows = [{'path': f'task-{i}', 'task_binary': task_blob()} for i in range(count)]
    evidence = annotation.evidence_for('set', rows, tmp_path / 'evidence')
    assert len(evidence['sampled_task_ids']) == count
    assert evidence['kept_tasks'] == count


@pytest.mark.parametrize('name', ['../escape', '/absolute'])
def test_evidence_rejects_unsafe_archive_paths(tmp_path, name):
    rows = [{'path': 'task', 'task_binary': task_blob({name: 'bad'})}]
    with pytest.raises(ValueError, match='unsafe archive member'):
        annotation.evidence_for('set', rows, tmp_path / 'evidence')
    assert not (tmp_path / 'escape').exists()


def test_evidence_rejects_links(tmp_path):
    link = tarfile.TarInfo('link')
    link.type = tarfile.SYMTYPE
    link.linkname = '/etc/passwd'
    with pytest.raises(ValueError, match='unsafe archive member'):
        annotation.evidence_for('set', [{'path': 'task', 'task_binary': task_blob(member=link)}], tmp_path / 'evidence')


def test_harbor_runtime_collects_verified_artifact(tmp_path, monkeypatch):
    from validation.publishing import annotation_runtime
    from validation.stages import harbor as runtime
    task = tmp_path / 'task'
    task.mkdir()
    trial = tmp_path / 'jobs/job/trial/attempts/00'
    (trial / 'artifacts').mkdir(parents=True)
    (trial / 'artifacts/annotation.json').write_text(json.dumps(answer()))
    monkeypatch.setenv('HARBOR_SIF_CACHE', str(tmp_path / 'images'))
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test-bridge')
    monkeypatch.setenv('OT_NET_ISOLATION', '0')
    configs = []
    async def execute(config):
        import tempfile
        assert Path(tempfile.gettempdir()).parent == task.parent
        from harbor.models.job.config import JobConfig
        JobConfig.model_validate(config)
        configs.append(config)
        return tmp_path / 'jobs/job'
    monkeypatch.setattr(runtime, 'execute_job', execute)
    monkeypatch.setattr(runtime, 'trial_results', lambda job: [(trial, {'verifier_result': {'rewards': {'reward': 1}}})])
    result, provenance = annotation_runtime.run(task, 'test-model')
    assert result == answer()
    assert provenance['trial_dir'] == str(trial)
    config = configs[0]
    assert config['environment']['type'] == 'apptainer'
    assert config['agents'][0]['name'] == 'claude-code'
    assert config['agents'][0]['model_name'] == 'anthropic/test-model'
    assert 'Read' in config['agents'][0]['kwargs']['allowed_tools']
    assert 'Bash' in config['agents'][0]['kwargs']['allowed_tools']
    assert config['verifier']['disable'] is False
    monkeypatch.setattr(runtime, 'trial_results', lambda job: [(trial, {'verifier_result': {'rewards': {'reward': 0}}})])
    with pytest.raises(RuntimeError, match='annotation trial failed'):
        annotation_runtime.run(task, 'test-model')


def test_staged_task_and_verifier(tmp_path):
    from validation.publishing.annotation_runtime import stage
    docs = {key: (annotation.ASSETS / name).read_text() for key, name in
            [('DOMAIN_TAXONOMY', 'domain_taxonomy.txt'), ('CAPABILITY_TAXONOMY', 'capability_taxonomy.txt'),
             ('EVALUATION_SUITE', 'evaluation_suite.txt')]}
    stage(tmp_path, 'Inspect all examples.', docs)
    config = tomllib.loads((tmp_path / 'task.toml').read_text())
    from harbor.models.task.task import Task
    parsed = Task(tmp_path)
    assert parsed.config.agent.timeout_sec == 1200
    assert parsed.config.artifacts[0].destination == 'annotation.json'
    assert config['artifacts'][0]['source'] == '/app/annotation.json'
    assert config['environment']['allow_internet'] is True
    # Exercise the validator copied into the container, with the actual codebooks.
    # Match the shallow container path, where repository-relative assets do not exist.
    namespace = {'__file__': '/tests/annotation.py', '__name__': 'annotation'}
    exec(compile((tmp_path / 'tests/annotation.py').read_text(), '/tests/annotation.py', 'exec'), namespace)
    validator = namespace['validate']
    labels = json.loads((tmp_path / 'tests/labels.json').read_text())
    validator(answer(), **labels)
    invalid = answer()
    invalid['domains'][0]['label'] = 'invented'
    with pytest.raises(ValueError):
        validator(invalid, **labels)


def test_publish_readme_requires_networked_bridge():
    from validation.stages.runner import parser, check_args
    args = parser().parse_args(['tasks', '--publish-readme'])
    with pytest.raises(ValueError, match='network-mode host'):
        check_args(args)
    args.network_mode = 'host'
    check_args(args)
