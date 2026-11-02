"""Convert NL2Bash to Harbor tasks; repairs.py adds what running them taught us.

patch.py alone keeps two upstream defects out of the tasks, because no task reaches a valid start
state with them: verifier.restart_environment, which the pinned Harbor schema does not recognize,
and a seed script solve.sh calls at a missing path. `--no-repairs` stops there, so the validation
rules can be measured on tasks they have not already shaped.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import copy
import hashlib
import io
import json
import re
import tarfile
import tomllib

from data.tasktrove_nl2bash.repairs import REASONS as REPAIR_REASONS, repair_instruction

REVISION = '946884702046be1a7dcea2638186ad6b0d2ea103'
UPSTREAM = 'DCAgent2__nl2bash-tasks-cleaned-oracle-v2'
PATCH_VERSIONS = (
    'nl2bash-remove-unsupported-verifier-restart-v1',
    'nl2bash-harness-seed-setup-v1',
    'nl2bash-output-path-v1',
    'nl2bash-repairs-split-v1',
)
LABEL = 'static:task-toml-schema.py'
SETUP_LABEL = 'repair:harness-seed-setup.py'
REASONS = {
    LABEL: ('Removed verifier.restart_environment from task.toml: the pinned Harbor '
            'schema does not recognize this field and the runtime ignores it.'),
    SETUP_LABEL: ('Renamed setup_files/setup_seeds.sh to setup_files/setup.sh and pointed '
                  'solve.sh at it (it called the missing /tests/setup_seeds.sh), so the harness '
                  'seeds /workspace before every trial; removed the instruction block that '
                  'asked the agent to run the script.'),
    **REPAIR_REASONS,
}
SEED_SCRIPT = 'setup_files/setup_seeds.sh'
HARNESS_SCRIPT = 'setup_files/setup.sh'
BROKEN_SETUP_CALL = b'bash /tests/setup_seeds.sh'
HARNESS_SETUP_CALL = b'bash /setup_files/setup.sh'
INSTRUCTION_SETUP = (b'## Environment Setup (run before starting)\n\n```bash\n'
                     b'bash /setup_files/setup_seeds.sh\n```\n\n---\n\n')

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


def patch_archive(blob, task_id=None, repairs=True):
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
            edited[id(instruction)] = instruction_body[len(INSTRUCTION_SETUP):]
            for member in solutions:
                body = solution_bodies[id(member)]
                fixed = body.replace(BROKEN_SETUP_CALL, HARNESS_SETUP_CALL)
                if fixed != body:
                    edited[id(member)] = fixed
            labels.append(SETUP_LABEL)
        if repairs and instruction:
            body = edited.get(id(instruction), instruction_body)
            fixed, repaired = repair_instruction(task_id, body, edited.get(id(config), original))
            if fixed != body:
                edited[id(instruction)] = fixed
                labels.extend(repaired)
        if not edited:
            return blob, []
        output = io.BytesIO()
        # Plain tar, as in the published tasks; Parquet compresses the column.
        with tarfile.open(fileobj=output, mode='w', format=source.format) as target:
            for member in members:
                info = copy.copy(member)
                info.name = renamed.get(id(member), member.name)
                body = edited.get(id(member), source.extractfile(member).read()) if member.isfile() else None
                if member.isfile() and id(member) in edited:
                    info.size = len(body)
                target.addfile(info, io.BytesIO(body) if body is not None else None)
        return output.getvalue(), labels


def patch(source, output, repairs=True, image_evidence=None, runtime_builds=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.tasktrove_nl2bash.runtime import load_conversion, convert_task
    from data.utils.patch_reporting import write_patch_report

    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / 'tasks.parquet'
    if destination.exists():
        raise ValueError('Use a new patch output directory')
    conversion = load_conversion(image_evidence, runtime_builds) if image_evidence else None
    parquet = pq.ParquetFile(source)
    changed = {}
    with pq.ParquetWriter(destination, parquet.schema_arrow) as writer:
        for batch in parquet.iter_batches(batch_size=32):
            rows = batch.to_pylist()
            for row in rows:
                row['task_binary'], labels = patch_archive(row['task_binary'], row['path'], repairs)
                if conversion:
                    row['task_binary'] = convert_task(row['task_binary'], *conversion)
                if labels:
                    changed[row['path']] = labels
            writer.write_table(pa.Table.from_pylist(rows, schema=parquet.schema_arrow))
    report = write_patch_report(source, destination, source={
        'dataset': f'open-thoughts/TaskTrove/{UPSTREAM}',
        'url': 'https://huggingface.co/datasets/open-thoughts/TaskTrove',
        'revision': REVISION}, dropped={}, change_labels=changed,
        change_reasons={name: '; '.join(REASONS[label] for label in labels)
                        for name, labels in changed.items()},
        patches=PATCH_VERSIONS, patcher=__file__)
    # Keep repairs.py beside the patcher snapshot, so publication includes it.
    code = Path(__file__).with_name('repairs.py').read_bytes()
    (output / 'repairs.py').write_bytes(code)
    recorded = json.loads((output / 'tasks.manifest.json').read_text())
    recorded['repairs'] = repairs
    recorded['patcher_dependencies'] = [{'path': 'repairs.py', 'sha256': hashlib.sha256(code).hexdigest()}]
    (output / 'tasks.manifest.json').write_text(json.dumps(recorded, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True, help=f'{UPSTREAM}/tasks.parquet at the pinned revision')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-repairs', action='store_true', help='stop at the plain conversion; see repairs.py')
    parser.add_argument('--image-evidence', type=Path, help='add the verifier runtime, with --runtime-builds')
    parser.add_argument('--runtime-builds', type=Path)
    args = parser.parse_args()
    report = patch(args.source, args.output, not args.no_repairs, args.image_evidence, args.runtime_builds)
    print(f"NL2Bash: {sum(t['action'] == 'changed' for t in report['tasks'])} tasks repaired")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'prepare-runtime':
        from data.tasktrove_nl2bash.runtime import cli
        sys.argv.pop(1)
        cli()
    else:
        main()
