"""Score SWE-Lego's required pytest node IDs from its -rA summary."""

import json
from pathlib import Path
import re
import sys


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OUTCOMES = {"PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"}


def parse(output):
    outcomes = {}
    for line in ANSI_RE.sub("", output).splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        outcome, test_id = parts
        if outcome not in OUTCOMES or test_id.startswith("["):
            continue
        if outcome in {"FAILED", "ERROR", "XFAIL", "XPASS"} and " - " in test_id:
            sections = test_id.split(" - ")
            for index in range(1, len(sections)):
                candidate = " - ".join(sections[:index])
                if candidate.count("[") == candidate.count("]"):
                    test_id = candidate
                    break
        outcomes[test_id.rstrip()] = outcome
    return outcomes


def main():
    output = Path(sys.argv[1]).read_text(errors="replace")
    required = json.loads(Path(sys.argv[2]).read_text())
    outcomes = parse(output)
    reward = float(bool(outcomes and required and all(outcomes.get(test) == "PASSED" for test in required)))
    Path("/logs/verifier/reward.json").write_text(json.dumps({"reward": reward}) + "\n")
    print(f"Matched {sum(test in outcomes for test in required)}/{len(required)} required tests; reward={reward}")


if __name__ == "__main__":
    main()
