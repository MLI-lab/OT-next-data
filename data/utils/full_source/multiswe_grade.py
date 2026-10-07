"""Score Multi-SWE task output with the pinned upstream multi-swe-bench package."""

import json
from pathlib import Path
import sys


def main():
    row = json.loads(Path(sys.argv[1]).read_text())
    output = Path(sys.argv[2]).read_text(errors="replace")
    reward_path = Path(sys.argv[3])
    reward = 0.0
    try:
        from multi_swe_bench.harness.dataset import Dataset
        from multi_swe_bench.harness.image import Config
        from multi_swe_bench.harness.instance import Instance
        from multi_swe_bench.harness.report import generate_report

        dataset = Dataset.from_dict(row)
        instance = Instance.create(pr=dataset, config=Config(need_clone=False, global_env=None, clear_env=False))
        report = generate_report(instance, dataset.run_result, dataset.test_patch_result, output)
        reward = float(report.valid and all(
            name in getattr(report, field)
            for field in ("p2p_tests", "f2p_tests", "s2p_tests", "n2p_tests")
            for name in getattr(dataset, field)
        ))
        print(report.error_msg or "valid report")
    except Exception as exc:
        print(f"Multi-SWE grader error: {type(exc).__name__}: {exc}", file=sys.stderr)
    reward_path.write_text(json.dumps({"reward": reward}) + "\n")


if __name__ == "__main__":
    main()
