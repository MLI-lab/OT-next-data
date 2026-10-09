"""Validate task.toml against Harbor and reject silently ignored model fields."""
from pathlib import Path
import sys
import tomllib

from harbor.models.task.config import TaskConfig
from pydantic import BaseModel, ValidationError


def unknown_fields(raw, parsed, path=''):
    """Walk parsed models; dictionaries such as metadata and env stay open."""
    findings = []
    if isinstance(parsed, BaseModel) and isinstance(raw, dict):
        fields = type(parsed).model_fields
        for key, value in raw.items():
            location = f'{path}.{key}' if path else key
            if key not in fields:
                findings.append(f'{location}: unrecognized field')
            else:
                findings.extend(unknown_fields(value, getattr(parsed, key), location))
    elif isinstance(raw, list) and isinstance(parsed, list):
        for index, (value, item) in enumerate(zip(raw, parsed)):
            findings.extend(unknown_fields(value, item, f'{path}[{index}]'))
    elif isinstance(raw, dict) and isinstance(parsed, dict):
        for key, value in raw.items():
            if key in parsed:
                findings.extend(unknown_fields(value, parsed[key], f'{path}.{key}'))
    return findings


def check(task):
    try:
        raw = tomllib.loads((Path(task) / 'task.toml').read_text(encoding='utf-8'))
        # The pinned Harbor validators migrate legacy fields in-place (version,
        # memory and storage, including nested environments). Audit that migrated
        # input so supported aliases pass while genuinely ignored extras remain.
        parsed = TaskConfig.model_validate(raw)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return [f'task.toml: {exc}']
    except ValidationError as exc:
        return [f"{'.'.join(map(str, error['loc'])) or 'task.toml'}: {error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)]
    return unknown_fields(raw, parsed)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    args = parser.parse_args()
    findings = check(args.task)
    for finding in findings:
        print(f'FAIL {finding}')
    if not findings:
        print('PASS task.toml matches the Harbor schema; no unrecognized fields')
    return int(bool(findings))


if __name__ == '__main__':
    sys.exit(main())
