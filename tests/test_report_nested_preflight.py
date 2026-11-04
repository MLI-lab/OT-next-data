import io
import json
import tarfile

from validation.publishing.report import write_report


def test_nested_preflight_summary_is_not_a_pipeline_stage(tmp_path):
    archive = tmp_path / 'evidence.tar.gz'
    stage = {'stage_layout': 'environment-first-v3', 'stage': 1,
             'name': 'build_validation', 'complete': True,
             'items': [{'task': 'sample', 'status': 'passed'}]}
    with tarfile.open(archive, 'w:gz') as tar:
        for name, record in [
            ('results/static-preflight/preflight/summary.json', {'passed': True}),
            ('results/stage_1_environment/run/summary.json', stage),
        ]:
            data = json.dumps(record).encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    output = tmp_path / 'report'
    write_report(archive, output)
    result = json.loads((output / 'summary.json').read_text())
    assert result['complete']
    assert [item['stage'] for item in result['stages']] == [1]
    assert not (output / 'stage-None-preflight.json').exists()
