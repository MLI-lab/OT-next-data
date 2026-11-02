import json
import os
import subprocess

import pytest

from hpc.offline_prep import verifier_offline
from hpc.offline_prep.python_request_options import write_options


def test_request_options_are_scoped_and_offline_check_ignores_remote_links(tmp_path):
    tool = tmp_path / 'pip-probe'
    tool.write_text('#!/usr/bin/env python3\nimport os, json\nprint(json.dumps({k: os.environ.get(k) for k in ["PIP_FIND_LINKS", "PIP_CONSTRAINT"]}))\n')
    tool.chmod(0o755)
    spec = {'python': [
        {'tool': str(tool), 'requirements': ['example==1'],
         'find_links': ['/opt/image wheels', 'https://example.org/wheels'],
         'constraints': ['setuptools==75.8.0']},
        {'tool': str(tool), 'requirements': ['other==2']} ]}
    # Whitespace is rejected rather than interpreted as multiple locations.
    with pytest.raises(ValueError):
        write_options(spec, tmp_path)
    spec['python'][0]['find_links'][0] = '/opt/image-wheels'
    write_options(spec, tmp_path)
    (tmp_path/'python-requests.tsv').write_text(verifier_offline.python_requests(spec))
    env = {k:v for k,v in os.environ.items() if not k.startswith('PIP_')}
    for text, checking in [(verifier_offline.BUILD, False),
                           (verifier_offline.CHECK.format(setup=''), True)]:
        block = 'i=0\n' + text.split('i=0\n',1)[1].split('done < $W/python-requests.tsv',1)[0] + 'done < $W/python-requests.tsv\n'
        # Match the container mount path in the actual generated options.
        for file in tmp_path.glob('python-*.sh'):
            file.write_text(file.read_text().replace('/ot-work/', str(tmp_path)+'/'))
        result = subprocess.run(['bash','-eu','-c', 'W="$1"; O="$1"; T="$1"\n'+block,
                                 'test',str(tmp_path)],env=env,capture_output=True,text=True,check=True)
        first, second = map(json.loads, result.stdout.splitlines())
        assert first['PIP_CONSTRAINT'] == str(tmp_path/'constraints1.txt')
        assert second['PIP_CONSTRAINT'] is None
        assert first['PIP_FIND_LINKS'] == (str(tmp_path/'wheels') if checking else '/opt/image-wheels https://example.org/wheels')
        assert second['PIP_FIND_LINKS'] == (str(tmp_path/'wheels') if checking else None)


@pytest.mark.parametrize('option', [
    {'constraints': ['--index-url https://bad']},
    {'constraints': ['thing @ https://bad/archive']},
    {'constraints': ['thing==1\nother==2']},
    {'find_links': ['https://user:password@example.org']},
    {'find_links': ['../relative']},
])
def test_option_values_cannot_inject_commands_or_pip_options(tmp_path, option):
    with pytest.raises(ValueError):
        write_options({'python':[option]}, tmp_path)
