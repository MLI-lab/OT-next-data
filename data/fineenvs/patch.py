"""Repair rules for the pinned full FineEnvs Harbor source."""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.utils.full_source.copy_source import copy_source

if __name__ == "__main__":
    copy_source()
