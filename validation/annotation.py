"""Generate dataset cards using the versioned annotation prompt and codebooks.

Sampled task archives are staged as evidence for a Harbor annotation task.
Model output is validated before any card is included in a Hugging Face commit.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import tarfile
from urllib.parse import urlparse

ASSETS = Path(__file__).resolve().parents[1] / 'data' / 'annotate_dataset'


def codebook_labels(text):
    # The final paragraph is the label list; preceding text is documentation.
    return set(re.findall(r'^([A-Z][A-Za-z &-]+): ', text.strip().rsplit('\n\n', 1)[-1], re.M))


def catalog_labels(text):
    inside = text.split('### In Distribution', 1)[1].split('### Out of Distribution', 1)
    labels = {m: 'in_distribution' for m in re.findall(r'^- \*\*(.+?)\.\*\* ', inside[0], re.M)}
    outside = inside[1].split('### ', 1)[0]
    labels.update({m.strip(): 'out_of_distribution' for m in re.findall(r'^\s*[-*] (.+)$', outside, re.M)})
    return labels


def evidence_for(folder, rows, destination, extra_paths=(), seed=0):
    """Stage ten reproducibly sampled kept tasks; prompt contains paths, not files."""
    destination = Path(destination)
    destination.mkdir(parents=True)
    evidence = []
    for index, path in enumerate(extra_paths):
        path = Path(path)
        target = destination / 'source' / f'{index:03d}' / path.name
        target.parent.mkdir(parents=True)
        shutil.copyfile(path, target)
        evidence.append({'source': str(path), 'path': '/evidence/' + target.relative_to(destination).as_posix(),
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    rows = sorted(rows, key=lambda row: row['path'])
    indices = sorted(random.Random(seed).sample(range(len(rows)), min(10, len(rows))))
    for index in indices:
        row = rows[index]
        name = row['path']
        if not isinstance(name, str) or PurePosixPath(name).name != name or name in ('', '.', '..'):
            raise ValueError(f'unsafe task name: {name!r}')
        target = destination / 'tasks' / name
        target.mkdir(parents=True)
        blob = row['task_binary']
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
            members = archive.getmembers()
            for member in members:
                rel = PurePosixPath(member.name)
                if rel.is_absolute() or '..' in rel.parts or not (member.isfile() or member.isdir()):
                    raise ValueError(f'unsafe archive member: {member.name}')
            archive.extractall(target, members=members, filter='data')
        evidence.append({'source': name, 'path': f'/evidence/tasks/{name}',
                         'sha256': hashlib.sha256(blob).hexdigest()})
    return {'dataset_name': folder, 'kept_tasks': len(rows),
            'sampling': 'Up to ten randomly sampled kept tasks; not a statistical coverage guarantee. No trajectories inferred.',
            'seed': seed, 'sampled_task_ids': [rows[i]['path'] for i in indices],
            'evidence': evidence}


def validate(answer, domains, capabilities, benchmarks):
    """Reject unknown labels, malformed entries and URLs."""
    keys = {'dataset_name', 'original_datasources', 'domains', 'capabilities',
            'expected_benchmark_transfer', 'no_transfer_reason'}
    if not isinstance(answer, dict) or set(answer) - {'model'} != keys:
        raise ValueError('annotation has incorrect top-level keys')
    def string(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('annotation requires a nonempty string')
    def fields(entry, expected):
        if not isinstance(entry, dict) or set(entry) != set(expected):
            raise ValueError('annotation entry has incorrect fields')
    string(answer['dataset_name'])
    for key in ('no_transfer_reason',):
        if answer[key] is not None:
            string(answer[key])
    for key in ('original_datasources', 'domains', 'capabilities', 'expected_benchmark_transfer'):
        if not isinstance(answer[key], list):
            raise ValueError(f'{key} must be a list')
    for entry in answer['original_datasources']:
        fields(entry, ('name', 'url'))
        string(entry['name'])
        if entry['url'] is not None:
            string(entry['url'])
            url = urlparse(entry['url'])
            if url.scheme not in ('http', 'https') or not url.netloc:
                raise ValueError('source URL must be HTTP(S)')
    for key, allowed in (('domains', domains), ('capabilities', capabilities)):
        seen = set()
        for entry in answer[key]:
            fields(entry, ('label', 'reason'))
            string(entry['label'])
            if entry['label'] not in allowed or entry['label'] in seen:
                raise ValueError(f'unknown or duplicate {key} label: {entry["label"]}')
            seen.add(entry['label'])
            string(entry['reason'])
    seen = set()
    for entry in answer['expected_benchmark_transfer']:
        fields(entry, ('benchmark', 'reason'))
        string(entry['benchmark'])
        if entry['benchmark'] not in benchmarks or entry['benchmark'] in seen:
            raise ValueError('unknown or duplicate benchmark')
        seen.add(entry['benchmark'])
        string(entry['reason'])
    if bool(answer['expected_benchmark_transfer']) != (answer['no_transfer_reason'] is None):
        raise ValueError('empty transfer selection requires a reason; nonempty requires null')


def render(answer, folder):
    def clean(text):
        return str(text).replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')
    lines = [f'# {clean(folder)}', '', '## Original datasource', '']
    for source in answer['original_datasources']:
        lines.append(f'- {clean(source["name"])}' + (f' — {clean(source["url"])}' if source['url'] else ''))
    if not answer['original_datasources']:
        lines.append('Original datasource not established from the supplied material.')
    for key, heading in (('domains', 'Domains'), ('capabilities', 'Agent capabilities')):
        lines += ['', f'## {heading}', '']
        if answer[key]:
            lines += [', '.join(clean(item['label']) for item in answer[key]), '']
        lines.extend(f'- **{clean(item["label"])}:** {clean(item["reason"])}' for item in answer[key])
        if not answer[key]:
            lines.append('No supported labels selected.')
    lines += ['', '## Expected benchmark transfer', '', 'These are hypotheses, not measured training gains.', '']
    for item in answer['expected_benchmark_transfer']:
        lines += [f'- **{clean(item["benchmark"])}**: {clean(item["reason"])}']
    if answer['no_transfer_reason']:
        lines.append(clean(answer['no_transfer_reason']))
    lines += ['', '## Files', '', '- `tasks.parquet`: kept tasks.', '- `archive.parquet`: excluded tasks with validation reasons.',
              '- `annotation.json`: structured annotation and generation provenance.', '',
              f'Generated with {clean(answer.get("model", "configured model"))}; domain and capability labels follow CLI-Universe.',
              'Taxonomy source: https://arxiv.org/abs/2606.22883v1', '']
    return '\n'.join(lines)


def generate(tables, model, runner, work_dir, evidence_paths=None, assets=ASSETS, seed=0):
    assets = Path(assets)
    documents = {name: (assets / filename).read_text() for name, filename in {
        'EVALUATION_SUITE': 'evaluation_suite.txt', 'DOMAIN_TAXONOMY': 'domain_taxonomy.txt',
        'CAPABILITY_TAXONOMY': 'capability_taxonomy.txt'}.items()}
    template = (assets / 'prompt_template.txt').read_text()
    suite_header, separator, suite_body = documents['EVALUATION_SUITE'].partition('--- BEGIN SOURCE EXCERPT ---')
    if not separator or '--- END SOURCE EXCERPT ---' not in suite_body:
        raise ValueError('evaluation suite snapshot is missing excerpt markers')
    prompt_documents = {
        'EVALUATION_SUITE': suite_body.split('--- END SOURCE EXCERPT ---', 1)[0].strip(),
        # Insert only taxonomy labels and definitions, omitting reference docstrings.
        **{key: documents[key].split('"""', 2)[-1].strip()
           for key in ('DOMAIN_TAXONOMY', 'CAPABILITY_TAXONOMY')},
    }
    evidence_paths = evidence_paths or {}
    if set(evidence_paths) - set(tables):
        raise ValueError('README evidence refers to an unknown output folder')
    cards = {}
    for folder, (kept, archived) in sorted(tables.items()):
        if Path(folder).is_absolute() or '..' in Path(folder).parts:
            raise ValueError('invalid dataset folder')
        task = Path(work_dir) / folder / 'task'
        evidence = evidence_for(folder, kept, task / 'setup_files/evidence', evidence_paths.get(folder, ()), seed)
        values = {**prompt_documents, 'DATASET_EVIDENCE': json.dumps(evidence, ensure_ascii=False)}
        # A single substitution pass prevents evidence from introducing placeholders.
        prompt = re.sub(r'\{\{(EVALUATION_SUITE|DOMAIN_TAXONOMY|CAPABILITY_TAXONOMY|DATASET_EVIDENCE)\}\}',
                        lambda match: values[match[1]], template)
        from validation.annotation_runtime import stage, run
        stage(task, prompt, documents)
        answer, runtime_provenance = (runner or run)(task, model)
        validate(answer, codebook_labels(documents['DOMAIN_TAXONOMY']), codebook_labels(documents['CAPABILITY_TAXONOMY']),
                 catalog_labels(documents['EVALUATION_SUITE']))
        answer = {**answer, 'model': answer.get('model', model)}
        cards[folder] = {'annotation': answer, 'readme': render(answer, folder), 'provenance': {
            'evaluation_snapshot_metadata': suite_header.strip(),
            'benchmark_distributions': {item['benchmark']: catalog_labels(documents['EVALUATION_SUITE'])[item['benchmark']]
                                        for item in answer['expected_benchmark_transfer']},
            'model_requested': model, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'assets_sha256': {key: hashlib.sha256(value.encode()).hexdigest() for key, value in {**documents, 'template': template}.items()},
            'evidence': evidence, 'runtime': runtime_provenance}}
    return cards
