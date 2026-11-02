"""Pinned model and scientific inputs for TMAX, without generated task state."""
import json
import re
from pathlib import Path

from data.utils.verifier_runtime import offline_preparation_command, offline_package_reference, task_offline_reference
from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id, verify, validate_offline_archive

MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
REVISION = '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'
WHISPER_SHA = 'd3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03'
PROTEIN_URL = 'https://files.rcsb.org/download/1CRN.pdb'
PROTEIN_SHA = '42199a30a0701864a2a5cc76cd7f35cc544cd0e65fbcf63e03c166543249b811'
FILES = {'1_Pooling/config.json': '4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23',
 'README.md': 'dcd602d2fd35c203a247304a06fec6654a12f7941b739f9221a064fe8dc3b7f0',
 'config.json': '953f9c0d463486b10a6871cc2fd59f223b2c70184f49815e7efbcab5d8908b41',
 'config_sentence_transformers.json': '061ca9d39661d6c6d6de5ba27f79a1cd5770ea247f8d46412a68a498dc5ac9f3',
 'model.safetensors': '53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db',
 'modules.json': '84e40c8e006c9b1d6c122e02cba9b02458120b5fb0c87b746c41e0207cf642cf',
 'sentence_bert_config.json': 'fc1993fde0a95c24ec6c022539d41cf6e2f7c9721e5415d6fb6897472a9cd4b7',
 'special_tokens_map.json': '303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3',
 'tokenizer.json': 'be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037',
 'tokenizer_config.json': 'acb92769e8195aabd29b7b2137a9e6d6e25c476a4f15aa4355c233426c61576b',
 'vocab.txt': '07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3'}


def request():
    return dict(apt=[], python=[], uv=False, sources=[
        dict(url=f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}',
             sha256=checksum, filename='minilm-' + name.replace('/', '--'))
        for name, checksum in sorted(FILES.items())])


def plan(files, image):
    setup = files.get('setup_files/task_install.sh', (b'',))[0].decode()
    if PROTEIN_URL in files['tests/test_final_state.py'][0].decode():
        recipe = dict(apt=[], python=[], uv=False, sources=[dict(
            url=PROTEIN_URL, sha256=PROTEIN_SHA, filename='1CRN.pdb')])
        return dict(spec={**recipe, 'image_sha256': image['sif_sha256']}, verifier=True, protein=True)
    if "whisper.load_model('tiny.en')" in setup:
        recipe = dict(apt=[], python=[], uv=False, sources=[dict(
            url=f'https://openaipublic.azureedge.net/main/whisper/models/{WHISPER_SHA}/tiny.en.pt',
            sha256=WHISPER_SHA, filename='tiny.en.pt')])
        return dict(spec={**recipe, 'image_sha256': image['sif_sha256']}, verifier=False)
    if not re.search(r'SentenceTransformer\([\"\']sentence-transformers/all-MiniLM-L6-v2[\"\']\)', setup):
        return None
    return dict(spec={**request(), 'image_sha256': image['sif_sha256']},
                verifier="SentenceTransformer('all-MiniLM-L6-v2')" in files['tests/test_final_state.py'][0].decode())


def catalog(cache, planned):
    if not planned:
        return {}
    spec = planned['spec']; key = spec_id(spec)
    record = json.loads((Path(cache) / (key + '.json')).read_text())
    if record.get('spec') != spec:
        raise ValueError('Prepared model inputs do not match their recipe')
    validate_offline_archive(verify(record, cache, OFFLINE_FORMAT))
    return {key: record}


# This helper is uploaded with setup/tests. Dependency definitions stay in the
# setup recipe supplied on stdin, rather than in the helper or runtime reference.
MATERIALIZE = r'''import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

phase, staged = sys.argv[1:]
request = json.load(sys.stdin)
records = []
revisions = set()
whisper = len(request['sources']) == 1 and request['sources'][0]['filename'] == 'tiny.en.pt'
protein = len(request['sources']) == 1 and request['sources'][0]['filename'] == '1CRN.pdb'
for source in request['sources']:
    url = urlsplit(source['url'])
    if protein:
        if (source['url'] != 'https://files.rcsb.org/download/1CRN.pdb'
                or not re.fullmatch('[0-9a-f]{64}', source['sha256'])):
            raise ValueError('Invalid pinned protein input')
        revisions.add(source['sha256'])
        records.append((source, '1CRN.pdb'))
        continue
    if whisper:
        checksum = source['sha256']
        if (not re.fullmatch('[0-9a-f]{64}', checksum) or source['url'] !=
                f'https://openaipublic.azureedge.net/main/whisper/models/{checksum}/tiny.en.pt'):
            raise ValueError('Invalid pinned Whisper input')
        revisions.add(checksum)
        records.append((source, 'tiny.en.pt'))
        continue
    prefix = '/sentence-transformers/all-MiniLM-L6-v2/resolve/'
    if url.scheme != 'https' or url.netloc != 'huggingface.co' or not url.path.startswith(prefix):
        raise ValueError('Unexpected model source')
    revision, relative = url.path[len(prefix):].split('/', 1)
    if (not re.fullmatch('[0-9a-f]{40}', revision) or '..' in PurePosixPath(relative).parts
            or PurePosixPath(relative).is_absolute()
            or not re.fullmatch('[0-9a-f]{64}', source['sha256'])
            or Path(source['filename']).name != source['filename']):
        raise ValueError('Invalid pinned model input')
    revisions.add(revision)
    records.append((source, relative))
if len(revisions) != 1 or not records:
    raise ValueError('Model inputs require one pinned revision')
revision = revisions.pop()
if phase == 'task':
    cache = Path('/root/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2')
    destination = Path('/root/.cache/whisper') if whisper else cache / 'snapshots' / revision
    if protein:
        destination = Path('/home/user')
elif phase == 'verifier':
    destination = Path('/tests/.tmax-model')
    if protein:
        destination = Path('/tests/.tmax-inputs')
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
else:
    raise ValueError('Unknown model phase')
destination.mkdir(parents=True, exist_ok=True)
for source, relative in records:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    local = Path(staged) / 'sources' / source['filename'] if staged else None
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
        pending = Path(output.name)
        try:
            stream = local.open('rb') if local and local.is_file() else urlopen(source['url'], timeout=180)
            with stream:
                shutil.copyfileobj(stream, output, 1024 * 1024)
            output.flush()
            with pending.open('rb') as check:
                checksum = hashlib.file_digest(check, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else None
                if checksum is None:
                    value = hashlib.sha256()
                    for chunk in iter(lambda: check.read(1024 * 1024), b''):
                        value.update(chunk)
                    checksum = value.hexdigest()
            if checksum != source['sha256']:
                raise ValueError('Model source checksum mismatch: ' + relative)
            pending.replace(target)
        finally:
            pending.unlink(missing_ok=True)
if phase == 'task' and not whisper and not protein:
    (cache / 'refs').mkdir(exist_ok=True)
    (cache / 'refs/main').write_text(revision)
print('Prepared pinned model files for ' + phase)
'''


def setup(planned, *, phase):
    spec = planned['spec']
    recipe = {key: value for key, value in spec.items() if key != 'image_sha256'}
    prepare = offline_preparation_command(recipe, image_sha256=spec['image_sha256'],
                                          phase=phase, task_reference=phase == 'task')
    executable = 'python3' if phase == 'task' else '"${OT_VERIFIER_PYTHON:?clean verifier required}"'
    directory = '/setup_files' if phase == 'task' else '/tests'
    variable = 'OT_TASK_OFFLINE' if phase == 'task' else 'OT_VERIFIER_OFFLINE'
    command = (f'{executable} -I {directory}/tmax_model_cache.py {phase} "${{{variable}:-}}" '
               "<<'OT_TMAX_MODEL_INPUTS' || exit $?\n" + json.dumps(recipe, sort_keys=True)
               + '\nOT_TMAX_MODEL_INPUTS\n')
    if phase == 'task':
        command += 'export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1\n'
    return prepare + '\n' + command


def apply(files, planned, records):
    record = records[spec_id(planned['spec'])]
    recipe = {key: value for key, value in planned['spec'].items() if key != 'image_sha256'}
    reference = next(iter(task_offline_reference(recipe, record).values()))
    files['setup_files/ot-task-runtime.json'] = (reference, 0o644)
    files['setup_files/tmax_model_cache.py'] = (MATERIALIZE.encode(), 0o644)
    if planned['verifier']:
        for name, content in offline_package_reference(planned['spec'], records, phase='verifier').items():
            files[name] = (content, 0o644)
        files['tests/tmax_model_cache.py'] = (MATERIALIZE.encode(), 0o644)
        source, mode = files['tests/test_final_state.py']
        if planned.get('protein'):
            old = b'req = urllib.request.urlopen(url)\nlines = req.read().decode(\'utf-8\').split(\'\\\\n\')'
            new = b"with open('/tests/.tmax-inputs/1CRN.pdb', encoding='utf-8') as source:\n    lines = source.read().split('\\\\n')"
            if source.count(old) != 1:
                raise ValueError('Unexpected protein input fetch; review grading semantics')
            source = source.replace(old, new)
            old_python = b'subprocess.run(["python3", "-c", code]'
            if source.count(old_python) != 1:
                raise ValueError('Unexpected protein reference interpreter')
            source = source.replace(old_python,
                b'subprocess.run([os.environ["OT_VERIFIER_PYTHON"], "-I", "-c", code]')
            source = source.replace(b'Computes the expected values using a subprocess to leverage the numpy/scipy\n    libraries installed by the agent, keeping this test file stdlib-only.',
                b'Computes the expected values from pinned input bytes using the clean\n    verifier runtime and its NumPy/SciPy dependencies.')
            files['tests/test_final_state.py'] = (source, mode)
            return
        files['tests/test_final_state.py'] = (source.replace(
            b"SentenceTransformer('all-MiniLM-L6-v2')", b"SentenceTransformer('/tests/.tmax-model')"), mode)
