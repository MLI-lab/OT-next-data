import hashlib
import json

import pytest

from data.swelego import setup_assets
from data.swelego.runtime_inputs import plan, prepared_files
from test_swelego_inputs import fixture


def test_nats_records_exact_installer_and_keeps_original_installation_point():
    files,image=fixture()
    files['setup_files/swelego.json']=json.dumps(dict(compiled_checkout=False,repo='nats-io/nats.py',
        base_commit='caaf42818eb80e180e40ae2225b49b9c47fd8b1d')).encode()
    files['setup_files/setup.sh']=files['setup_files/setup.sh'].replace(
        b'finish_phase preinstall\n',b'finish_phase preinstall\nbash ./scripts/install_nats.sh\n')
    planned=plan(files,image)
    script=planned['request']['http']['commands'][0]
    assert hashlib.sha256(script.encode()).hexdigest()==setup_assets.NATS_SCRIPT_SHA256
    adapted=prepared_files(files,planned)['setup_files/setup.sh']
    assert b'bash ./scripts/install_nats.sh' in adapted
    assert b'$OT_TASK_OFFLINE/http/bin:$PATH' in adapted
    assert adapted.index(b'bash ./scripts/install_nats.sh') < adapted.index(b'python -m pip install')


def test_cached_irf_response_runs_original_initializer_without_network(tmp_path,monkeypatch):
    import requests
    monkeypatch.chdir(tmp_path)
    script=('from pathlib import Path\nimport requests\n'
            'response = requests.get('+repr(setup_assets.IRF_URL)+')\n'
            'response.raise_for_status()\nPath("initialized.dat").write_bytes(response.content)\n')
    (tmp_path/'download_irfs.py').write_text(script)
    payload=b'original download bytes'
    monkeypatch.setattr(setup_assets,'IRF_SCRIPT_SHA256',hashlib.sha256(script.encode()).hexdigest())
    monkeypatch.setattr(setup_assets,'IRF_SHA256',hashlib.sha256(payload).hexdigest())
    cache=tmp_path/'cache';(cache/'sources').mkdir(parents=True)
    (cache/'sources'/setup_assets.IRF_FILE).write_bytes(payload)
    monkeypatch.setenv('OT_TASK_OFFLINE',str(cache))
    monkeypatch.setattr(requests,'get',lambda *a,**kw:pytest.fail('cached initialization contacted network'))
    generated=setup_assets.prepared_files({'setup_files/setup.sh':b'python download_irfs.py\n'},[{'kind':'irfs'}])
    exec(compile(generated['setup_files/ot_cached_irfs.py'],'cached_irfs.py','exec'),{})
    assert (tmp_path/'initialized.dat').read_bytes()==payload
    (cache/'sources'/setup_assets.IRF_FILE).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum differs'):
        exec(compile(generated['setup_files/ot_cached_irfs.py'],'cached_irfs.py','exec'),{})
    assert b'else\n  python download_irfs.py\nfi' in generated['setup_files/setup.sh']


def test_other_revisions_do_not_inherit_reviewed_assets():
    assert setup_assets.inputs({'repo':'cta-observatory/pyirf','base_commit':'b'*40},'python download_irfs.py') is None


@pytest.mark.parametrize('base,major', setup_assets.NODE_BASES.items())
def test_nodesource_cache_keeps_install_order_and_online_fallback(tmp_path, base, major):
    import os
    import subprocess
    files, image = fixture()
    command = f'curl -sL https://deb.nodesource.com/setup_{major}.x | bash -'
    files['setup_files/swelego.json'] = json.dumps({
        'repo': 'reata/sqllineage', 'base_commit': base, 'compiled_checkout': False}).encode()
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'apt-get update && apt-get install -y cmake',
        (command + '\napt-get update && apt-get install -y nodejs').encode())
    planned = plan(files, image)
    assert 'nodejs' not in planned['request']['apt']
    package = planned['request']['apt_source_packages'][0]
    assert 'nodejs=' + package['version'] in planned['request']['apt']
    assert package['filename'] == planned['request']['sources'][0]['filename']
    script = setup_assets.prepared_files({'setup_files/setup.sh':
        (command + '\napt-get update && apt-get install -y nodejs\n').encode()},
        planned['setup_assets'])['setup_files/setup.sh'].decode()
    # Execute both branches with mocked installers; an offline run must never curl.
    wrapper = ('set -eu\napt-get() { echo "apt $*"; }\n'
               'curl() { echo "echo original-installer"; }\n' + script)
    for cached in (True, False):
        env = dict(os.environ, OT_TASK_OFFLINE=str(tmp_path) if cached else '')
        run = subprocess.run(['bash', '-c', wrapper], env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert ('original-installer' in run.stdout) is not cached
        assert run.stdout.rstrip().endswith('apt install -y nodejs')
        if cached:
            assert 'apt install -y apt-transport-https ca-certificates curl gnupg' in run.stdout
