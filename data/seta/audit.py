"""SETA is graded from filesystem state; audit its shared grader in the task image."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from data.seta.patch import audit_rewards

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pilot', type=Path, required=True)
    ap.add_argument('--image', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    raise SystemExit(audit_rewards(args.pilot.resolve(), args.image.resolve(), args.out.resolve()))
