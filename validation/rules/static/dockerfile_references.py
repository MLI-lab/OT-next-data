"""Our patch to Terminal-Bench check-dockerfile-references.sh: ignore full-line Dockerfile comments, and catch the
task's own test files under any path.

Upstream greps `tests/test_*.py` as a regular expression ("test", any number of `_`, any character, "py"), so it
misses the usual names such as `tests/test_outputs.py`. Matching every `test_<name>.py` instead would also flag the
project's own tests (a repository's `tests/test_api.py`). So a reference is added only when a file of that name is
one of the task's verifier tests (`tests/test_<name>.py`): it is rewritten to `tests/test_.py`, which the unchanged
upstream pattern matches. Everything upstream already matches stays matched.
"""
import re

CHECK = 'check-dockerfile-references.sh'
ADAPTATION = (
    'Ignore full-line Dockerfile comments; also flag references to the task\'s own tests/test_<name>.py files under '
    'any path; executable references remain checked on a scratch copy.')
# A path ending in test_<name>.py, e.g. files/test_basic.py or /tests/test_basic.py.
TEST_FILE = re.compile(r'(?<![\w.-])[\w./-]*?\btest_[\w.-]*\.py(?![\w.-])')


def dockerfile_without_comments(text):
    """Ignore full-line Dockerfile comments, retaining inline arguments/data."""
    kept, heredocs = [], []
    for line in text.splitlines(keepends=True):
        if heredocs:
            kept.append(line)
            if line.strip() == heredocs[0]:
                heredocs.pop(0)
            continue
        if line.lstrip().startswith('#'):
            continue
        kept.append(line)
        # Heredoc payload is data, not Dockerfile comments.
        heredocs.extend(re.findall(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line))
    return ''.join(kept)


def verifier_tests(task):
    """Names of the task's verifier test files, tests/**/test_<name>.py."""
    tests = task / 'tests'
    return {p.name for p in tests.rglob('test_*.py')} if tests.is_dir() else set()


def dockerfile_references_text(text, tests=()):
    """The Dockerfile as the upstream check sees it: no full-line comments, and references to the task's own
    verifier test files (by name, under any path) rewritten so that the upstream pattern matches them."""
    names = set(tests)
    return TEST_FILE.sub(lambda m: 'tests/test_.py' if m.group(0).rsplit('/', 1)[-1] in names else m.group(0),
                         dockerfile_without_comments(text))
