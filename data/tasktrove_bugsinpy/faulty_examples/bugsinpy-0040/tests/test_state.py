"""Harbor verifier: reward.txt must be "1". Reports pass_ratio when available."""
from pathlib import Path

REWARD = Path("/logs/verifier/reward.txt")
RATIO = Path("/logs/verifier/pass_ratio.txt")


def test_reward_is_pass():
    assert REWARD.exists(), f"reward file missing: {REWARD}"
    if RATIO.exists():
        print(f"pass_ratio={RATIO.read_text().strip()}")
    val = REWARD.read_text().strip()
    assert val == "1", f"Task not solved (reward.txt={val!r})"
