"""Count distinct image build contexts without building or downloading images."""
import hashlib
import json
from pathlib import PurePosixPath


def fingerprints(files):
    """Use patch-report file signatures, including contents, modes and links."""
    roots = {PurePosixPath(name).parent for name in files
             if PurePosixPath(name).name == 'Dockerfile'}
    result = set()
    for root in roots:
        context = sorted((PurePosixPath(name).relative_to(root).as_posix(), signature)
                         for name, signature in files.items()
                         if PurePosixPath(name).is_relative_to(root))
        result.add(hashlib.sha256(json.dumps(context).encode()).hexdigest())
    return sorted(result)


def summarize(groups):
    groups = list(groups)
    return {'unique': len({value for group in groups for value in group}),
            'tasks': len(groups), 'tasks_with_build_context': sum(bool(group) for group in groups)}
