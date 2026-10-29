"""Our patch to Terminal-Bench check-dockerfile-references.sh: ignore full-line Dockerfile comments."""
import re

CHECK = 'check-dockerfile-references.sh'
ADAPTATION = (
    'Ignore full-line Dockerfile comments; executable references remain checked on a scratch copy.')


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
