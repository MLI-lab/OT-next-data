"""Pinned upstream assets, checkout management and file-based imports."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PINS = json.loads((Path(__file__).parent / 'upstream.lock.json').read_text())
from config.runtime import harbor_commit
PINS['harbor-validation']['commit'] = harbor_commit()


def checkout(name):
    path = ROOT / 'external' / name
    if not (path / '.git').exists():
        raise RuntimeError('Run python -m validation.upstream setup first')
    head = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if head != PINS[name]['commit']:
        raise RuntimeError(f'{name}: expected {PINS[name]["commit"]}, found {head}')
    if subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise RuntimeError(f'{name}: upstream checkout has tracked modifications')
    return path


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def setup():
    for name, pin in PINS.items():
        path = ROOT / 'external' / name
        if not path.exists():
            subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout', pin['url'], str(path)], check=True)
            if pin.get('sparse'):
                subprocess.run(['git', '-C', str(path), 'sparse-checkout', 'set', *pin['sparse']], check=True)
            subprocess.run(['git', '-C', str(path), 'checkout', '--detach', pin['commit']], check=True)
        checkout(name)  # Never reset an existing user's checkout.
        print(f'{name}: {pin["commit"]}')
