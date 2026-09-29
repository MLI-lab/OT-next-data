"""Launch the pinned hardening engine with our runtime-only bridge fixes."""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.stages.harbor import install_runtime_patches
from validation.upstream import checkout

if __name__ == '__main__':
    install_runtime_patches()
    root = checkout('harden-v0')
    sys.path.insert(0, str(root))
    sys.argv[0] = str(root / 'harden.py')
    runpy.run_path(sys.argv[0], run_name='__main__')
