"""Repair the NL2Bash verifier schema and let the harness run the seed setup."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import copy
import gzip
import hashlib
import io
import re
import tarfile
import tomllib

REVISION = '946884702046be1a7dcea2638186ad6b0d2ea103'
PATCH_VERSIONS = (
    'nl2bash-remove-unsupported-verifier-restart-v1',
    'nl2bash-harness-seed-setup-v1',
    'nl2bash-output-path-v1',
)
LABEL = 'static:task-toml-schema.py'
SETUP_LABEL = 'repair:harness-seed-setup.py'
OUTPUT_LABEL = 'repair:instruction-output-path.py'
REASONS = {
    LABEL: ('Removed verifier.restart_environment from task.toml: the pinned Harbor '
            'schema does not recognize this field and the runtime ignores it.'),
    SETUP_LABEL: ('Renamed setup_files/setup_seeds.sh to setup_files/setup.sh and pointed '
                  'solve.sh at it (it called the missing /tests/setup_seeds.sh), so the harness '
                  'seeds /workspace before every trial; removed the instruction block that '
                  'asked the agent to run the script; the fixtures line now says the input '
                  'files are already in /workspace, and the claim that the verifier inspects '
                  'helper files is removed (it reads only /output/command_capture.txt).'),
    OUTPUT_LABEL: ('The goal named the capture file as $PWD/command_capture.txt. The agent runs from '
                   '/workspace, so $PWD expands to /workspace, but the verifier reads only '
                   '/output/command_capture.txt (as the requirements say). The goal now names '
                   '/output/command_capture.txt; nothing else changes.'),
}
# Reviewed 2026-10-17 (stage-4 path warnings): goal sentences that name a capture file other than
# the one the verifier reads. Exact one-occurrence replacement, per task.
OUTPUT_PATH_FIXES = {
    'task_267': (b'capturing output in $PWD/command_capture.txt.',
                 b'capturing output in /output/command_capture.txt.'),
}
SEED_SCRIPT = 'setup_files/setup_seeds.sh'
HARNESS_SCRIPT = 'setup_files/setup.sh'
BROKEN_SETUP_CALL = b'bash /tests/setup_seeds.sh'
HARNESS_SETUP_CALL = b'bash /setup_files/setup.sh'
INSTRUCTION_SETUP = (b'## Environment Setup (run before starting)\n\n```bash\n'
                     b'bash /setup_files/setup_seeds.sh\n```\n\n---\n\n')
INPUT_LINES = (
    b'- From `/workspace`, assemble any missing fixtures using standard shell utilities '
    b'before running your command.\n',
    b'- The workspace already contains pre-seeded fixtures copied to `/workspace`; '
    b'run the command from that directory.\n',
)
INPUT_LINE = (b'- The input files for this task are already in `/workspace`; '
              b'run the command from that directory.\n')
HELPER_LINE = b'- Do not delete any helper files generated during execution; the verifier inspects them.\n'


def rewrite_instruction(body):
    """Drop the setup block and the two lines that misdescribe the seeded start state."""
    text = body[len(INSTRUCTION_SETUP):]
    if [text.count(line) for line in INPUT_LINES].count(1) != 1 or \
            sum(text.count(line) for line in INPUT_LINES) != 1 or text.count(HELPER_LINE) != 1:
        raise ValueError('instruction.md lacks the expected fixtures and helper-files lines')
    for line in INPUT_LINES:
        text = text.replace(line, INPUT_LINE)
    return text.replace(HELPER_LINE, b'')


def clean_config(content):
    """Remove only the unsupported field; verify every other TOML value survives."""
    text = content.decode('utf-8')
    before = tomllib.loads(text)
    if 'restart_environment' not in before.get('verifier', {}):
        return content
    expected = copy.deepcopy(before)
    del expected['verifier']['restart_environment']
    section = re.compile(r'(?ms)(^\[verifier\][^\n]*\n)(.*?)(?=^\[|\Z)')
    field = re.compile(r'(?m)^[ \t]*(?:restart_environment|"restart_environment"|\'restart_environment\')[ \t]*=[^\n]*(?:\n|$)')
    cleaned = section.sub(lambda m: m[1] + field.sub('', m[2]), text)
    if tomllib.loads(cleaned) != expected:
        raise ValueError('Unsupported task.toml layout for verifier.restart_environment removal')
    return cleaned.encode('utf-8')


def patch_archive(blob, task_id=None):
    """Apply narrowly scoped fixes while preserving all other archive payloads."""
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as source:
        members = source.getmembers()
        configs = [m for m in members if Path(m.name).as_posix() == 'task.toml']
        if len(configs) != 1 or not configs[0].isfile():
            raise ValueError('Expected exactly one regular task.toml in task archive')
        config = configs[0]
        original = source.extractfile(config).read()
        cleaned = clean_config(original)
        edited = {}
        labels = []
        if cleaned != original:
            edited[id(config)] = cleaned
            labels.append(LABEL)

        by_name = {Path(m.name).as_posix(): m for m in members}
        helper = by_name.get(SEED_SCRIPT)
        solutions = [m for m in members
                     if Path(m.name).as_posix().startswith('solution/') and m.isfile()]
        solution_bodies = {id(m): source.extractfile(m).read() for m in solutions}
        instruction = by_name.get('instruction.md')
        instruction_body = source.extractfile(instruction).read() if instruction else b''
        calls = any(BROKEN_SETUP_CALL in body for body in solution_bodies.values())
        renamed = {}
        if helper is not None or calls or b'setup_seeds' in instruction_body:
            # All three parts belong together; anything else needs a human look.
            if helper is None or not helper.isfile() or not calls or HARNESS_SCRIPT in by_name:
                raise ValueError('Unexpected seed setup layout: need setup_files/setup_seeds.sh, '
                                 'a /tests/setup_seeds.sh call in solve.sh and no setup_files/setup.sh')
            if not instruction_body.startswith(INSTRUCTION_SETUP) or \
                    b'setup_seeds' in instruction_body[len(INSTRUCTION_SETUP):]:
                raise ValueError('instruction.md does not start with the expected seed setup block')
            renamed[id(helper)] = HARNESS_SCRIPT
            edited[id(instruction)] = rewrite_instruction(instruction_body)
            for member in solutions:
                body = solution_bodies[id(member)]
                fixed = body.replace(BROKEN_SETUP_CALL, HARNESS_SETUP_CALL)
                if fixed != body:
                    edited[id(member)] = fixed
            labels.append(SETUP_LABEL)
        if task_id in OUTPUT_PATH_FIXES:
            old, new = OUTPUT_PATH_FIXES[task_id]
            body = edited.get(id(instruction), instruction_body) if instruction else b''
            if body.count(old) == 1:
                edited[id(instruction)] = body.replace(old, new)
                labels.append(OUTPUT_LABEL)
            elif not (body.count(old) == 0 and body.count(new) == 1):
                raise ValueError(f'{task_id}: expected exactly one {old!r} in instruction.md')
        if not edited:
            return blob, []
        output = io.BytesIO()
        compressed = blob[:2] == b'\x1f\x8b'
        stream = gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) if compressed else output
        with tarfile.open(fileobj=stream, mode='w', format=source.format) as target:
            for member in members:
                info = copy.copy(member)
                info.name = renamed.get(id(member), member.name)
                body = edited.get(id(member), source.extractfile(member).read()) if member.isfile() else None
                if member.isfile() and id(member) in edited:
                    info.size = len(body)
                target.addfile(info, io.BytesIO(body) if body is not None else None)
        if compressed:
            stream.close()
        return output.getvalue(), labels


def patch(source, output):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report

    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / 'tasks.parquet'
    if destination.exists():
        raise ValueError('Use a new patch output directory')
    parquet = pq.ParquetFile(source)
    changed = {}
    with pq.ParquetWriter(destination, parquet.schema_arrow) as writer:
        for batch in parquet.iter_batches(batch_size=32):
            rows = batch.to_pylist()
            for row in rows:
                row['task_binary'], labels = patch_archive(row['task_binary'], row['path'])
                if labels:
                    changed[row['path']] = labels
            writer.write_table(pa.Table.from_pylist(rows, schema=parquet.schema_arrow))
    with source.open('rb') as source_stream:
        source_hash = hashlib.file_digest(source_stream, 'sha256').hexdigest()
    return write_patch_report(source, destination, source={
        'dataset': 'open-thoughts/TaskTrove/nl2bash (TOML-normalized input)',
        'url': 'https://huggingface.co/datasets/open-thoughts/TaskTrove',
        'revision': f'sha256:{source_hash}'}, dropped={}, change_labels=changed,
        change_reasons={name: '; '.join(REASONS[label] for label in labels)
                        for name, labels in changed.items()},
        patches=PATCH_VERSIONS, patcher=__file__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = patch(args.source, args.output)
    print(f"NL2Bash: {sum(t['action'] == 'changed' for t in report['tasks'])} tasks repaired")


if __name__ == '__main__':
    main()
