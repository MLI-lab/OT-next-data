#!/usr/bin/env python3
"""Run validation stage 2: llm rubric review."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.stages.runner import stage_main

if __name__ == '__main__':
    raise SystemExit(stage_main(2))
