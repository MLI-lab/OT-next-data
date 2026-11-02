"""Reviewed non-Python downloads used by pinned SWE-Lego setup scripts."""
import json
from pathlib import Path

NODE_BASES = {
    '843ab2eb58e3e8f4d68c5e67afc7f69341c3ade7': '16',
    'a2b03449ab6d8722eeb3cd9869d89a2af1b7c098': '18',
    'fe3e2c5ba1d6d6af2a64a29b4980a97a8d5c83b7': '16',
    'c0cf6aa6fc5976a61ed534040b44b21cafb41094': '16',
    'e0fbf32abaf1eb51db27c2b0995a7b3e9cdc3ed0': '18',
    '10442e52aed3d937b252293d556ed1d0afa8b622': '18',
}
NODE_PREREQUISITES = ['apt-transport-https', 'ca-certificates', 'curl', 'gnupg']

IRF_URL = 'https://zenodo.org/record/5499840/files/cta-prod5-zenodo-fitsonly-v0.1.zip?download=1'
IRF_FILE = 'cta-prod5-zenodo-fitsonly-v0.1.zip'
# Downloaded bytes also match Zenodo record 5499840's MD5 and size (44,065,624).
IRF_SHA256 = 'ae1248304fa105f615483b03602f287df63b636c955f20694a5e40149dee5354'
IRF_SCRIPT_SHA256 = '04734d5dfc382c57cdb5275578865dc87cb5b610f73854a784c704883f77db22'
NATS_SCRIPT_SHA256 = '4d7e6c53d16a57517ffa534501c0ba32d68297740aeb93e9844ff9f93e90bd73'
NATS_SCRIPT = '''#!/bin/bash

set -e

export DEFAULT_NATS_SERVER_VERSION=latest

export NATS_SERVER_VERSION="${NATS_SERVER_VERSION:=$DEFAULT_NATS_SERVER_VERSION}"

mkdir -p $HOME/nats-server
curl -sf https://binaries.nats.dev/nats-io/nats-server/v2@$NATS_SERVER_VERSION | PREFIX=$HOME/nats-server/ sh
$HOME/nats-server/nats-server -v
'''


def inputs(metadata, command):
    major = NODE_BASES.get(metadata.get('base_commit'))
    if (metadata.get('repo') == 'reata/sqllineage' and major
            and command == f'curl -sL https://deb.nodesource.com/setup_{major}.x | bash -'):
        package = json.loads(Path(__file__).with_name('nodesource_packages.json').read_text())[major]
        return {'kind': 'nodesource', 'command': command, 'request': {
            'sources': [{key: package[key] for key in ('url', 'filename', 'sha256')}],
            'apt_source_packages': [{key: package[key] for key in ('filename', 'package', 'version')}],
            'apt': [*NODE_PREREQUISITES, 'nodejs=' + package['version']]}}
    if (command == 'python download_irfs.py' and metadata.get('repo') == 'cta-observatory/pyirf'
            and metadata.get('base_commit') in ('4d55170f8b6553f5f78a420fe90a40111d98e7f5',
                                                'fc688fc9acaa53e9a8c95ca777ab4a09dca24c66')):
        return {'kind': 'irfs', 'request': {'sources': [
            {'url': IRF_URL, 'filename': IRF_FILE, 'sha256': IRF_SHA256}]}}
    if (command == 'bash ./scripts/install_nats.sh' and metadata.get('repo') == 'nats-io/nats.py'
            and metadata.get('base_commit') == 'caaf42818eb80e180e40ae2225b49b9c47fd8b1d'):
        return {'kind': 'nats', 'request': {'http': {'commands': [NATS_SCRIPT],
            'home': '/tmp/ot-nats-home', 'workdir': '/tmp/ot-nats-work'}}}
    return None


def prepared_files(files, assets):
    result = dict(files)
    setup = files['setup_files/setup.sh'].decode()
    for asset in assets:
        if asset['kind'] == 'nodesource':
            command = asset['command']
            if setup.count(command + '\n') != 1:
                raise ValueError('NodeSource setup point changed')
            replacement = ('if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
                           '  apt-get update\n'
                           '  apt-get install -y ' + ' '.join(NODE_PREREQUISITES) + '\n'
                           'else\n  ' + command + '\nfi')
            setup = setup.replace(command, replacement, 1)
        elif asset['kind'] == 'irfs':
            # Run the original pinned initializer; supply only its recorded HTTP
            # response from the cache. No task checkout or generated IRFs are
            # saved in the reusable dependency archive.
            helper = ('import hashlib, os, pathlib, runpy\nimport requests\n'
                      'script = pathlib.Path("download_irfs.py")\n'
                      'if hashlib.sha256(script.read_bytes()).hexdigest() != ' + repr(IRF_SCRIPT_SHA256) + ':\n'
                      '    raise ValueError("Unreviewed IRF initialization script")\n'
                      'payload = (pathlib.Path(os.environ["OT_TASK_OFFLINE"]) / "sources" / ' + repr(IRF_FILE) + ').read_bytes()\n'
                      'if hashlib.sha256(payload).hexdigest() != ' + repr(IRF_SHA256) + ':\n'
                      '    raise ValueError("Cached IRF download checksum differs")\n'
                      'original = requests.get\n'
                      'def cached_get(url, *args, **kwargs):\n'
                      '    if url != ' + repr(IRF_URL) + ': return original(url, *args, **kwargs)\n'
                      '    response = requests.Response()\n'
                      '    response.status_code = 200\n'
                      '    response.url = url\n'
                      '    response._content = payload\n'
                      '    return response\n'
                      'requests.get = cached_get\n'
                      'runpy.run_path(str(script), run_name="__main__")\n')
            result['setup_files/ot_cached_irfs.py'] = helper.encode()
            replacement = ('if [ -n "${OT_TASK_OFFLINE:-}" ] && [ -f "$OT_TASK_OFFLINE/sources/' + IRF_FILE + '" ]; then\n'
                           '  python /setup_files/ot_cached_irfs.py\n'
                           'else\n  python download_irfs.py\nfi')
            if setup.count('python download_irfs.py\n') != 1:
                raise ValueError('IRF initialization point changed')
            setup = setup.replace('python download_irfs.py', replacement, 1)
        elif asset['kind'] == 'nats':
            command = 'bash ./scripts/install_nats.sh'
            if setup.count(command+'\n') != 1:
                raise ValueError('NATS initialization point changed')
            check = ('if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
                     '  printf "%s  scripts/install_nats.sh\\n" ' + NATS_SCRIPT_SHA256 + ' | sha256sum -c -\n'
                     'fi\n')
            setup = setup.replace(command, check + command, 1)
        else:
            raise ValueError('Unreviewed setup asset')
    result['setup_files/setup.sh'] = setup.encode()
    return result
