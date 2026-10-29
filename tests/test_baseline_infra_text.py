"""Benign apt-hook and test-source text must not mark a completed no-op as an infrastructure failure."""
from validation.stages.harbor import baseline_infra_error

APT_HOOK = "rm: cannot remove '/var/cache/apt/archives/partial/*.deb': Permission denied"
SOURCE = ('        result = subprocess.run(\n            ["bash", RUN_TESTS],\n'
          '            capture_output=True,\n            text=True,\n            timeout=60\n        )')


def test_docker_clean_hook_and_timeout_keyword_are_not_infrastructure_errors():
    # Observed in every Terminal-Lego verifier on Helma and CLAIX fakeroot (2026-10-28).
    assert not baseline_infra_error('Reading package lists...\n' + APT_HOOK + '\n' + SOURCE + '\nE   AssertionError')
    assert not baseline_infra_error('cwd=BASE_DIR, capture_output=True, text=True, timeout=10)')


def test_real_infrastructure_messages_still_count():
    for message in ('subprocess.TimeoutExpired: Command timed out after 60 seconds', 'Killed',
                    'bash: /app/run.sh: Permission denied', 'OSError: [Errno 28] No space left on device',
                    "rm: cannot remove '/app/output': Permission denied", 'pytest: command not found'):
        assert baseline_infra_error(APT_HOOK + '\n' + message), message
    # Only the exact hook line is exempt, not other text on a line that contains it.
    assert baseline_infra_error(APT_HOOK + ' (then: Permission denied writing /app)')
