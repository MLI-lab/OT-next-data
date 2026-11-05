"""Evidence for requested destinations and archive members, without executing artifacts."""
import re
import shlex
from pathlib import Path

PATH = r'/[A-Za-z0-9_./+-]+'


def named(path, text):
    return bool(re.search(r'(?<![\w/.-])' + re.escape(path) + r'(?![\w/.-])', text))


def mount_destinations(text):
    """Only explicit destination grammar, not every path near the word mount."""
    found = set()
    for line in text.splitlines():
        if re.search(r"\b(?:do\s+not|never|don't)\s+(?:mount|create|set\s+up)\b", line, re.I):
            continue
        patterns = [
            r'\bmount\b[^\n]*?\b(?:at|onto|into|to|on)\s+(?:the\s+)?(?:directory\s+)?[`\"\']?(' + PATH + ')',
            r'\b(?:create|set\s+up)\b[^\n!?]*?\bmount\s*(?:point|directory)\s+(?:at\s+)?[`\"\']?(' + PATH + ')',
        ]
        for pattern in patterns:
            found.update(m.group(1).rstrip('./') for m in re.finditer(pattern, line, re.I))
    return found


def artifact_instruction_outputs(task, instruction):
    """Use only artifacts explicitly delegated to for instructions.

    Literal text files and supported speech-generation commands are evidence
    providers. A path appearing in any other supplied file is not an exemption.
    """
    from validation.rules.static.test_file_references.supplied_references import supplied_files, literal_writes
    files = supplied_files(task)
    speech = {}
    for folder in ('setup_files', 'environment'):
        for source in sorted((task / folder).rglob('*.sh')):
            text = source.read_text(errors='replace')
            for path, body, append in literal_writes(text, None):
                if not append:
                    files.setdefault(path, (body, str(source.relative_to(task))))
            for number, line in enumerate(text.splitlines(), 1):
                try:
                    words = shlex.split(line, comments=True)
                except ValueError:
                    continue
                if not words or Path(words[0]).name not in ('espeak', 'espeak-ng'):
                    continue
                if '-w' not in words or any(w in words for w in (';', '|', '&&', '||', '-f')):
                    continue
                index = words.index('-w')
                if index + 2 >= len(words):
                    continue
                audio, transcript = words[index + 1], words[-1]
                if audio.startswith('/') and ' ' in transcript and not any(c in transcript for c in ('$','`')):
                    speech[audio] = (transcript, f'{source.relative_to(task)}:{number}')
    result = {p: dict(kind='written_mount_destination') for p in mount_destinations(instruction)}
    delegated = bool(re.search(r'\b(?:transcribe|listen|follow|instructions|specified|described)\b', instruction, re.I))
    mount_action = bool(re.search(r'\bmount(?:ing)?\b', instruction, re.I))
    if not delegated or not mount_action:
        return result
    for path, (content, source) in {**files, **speech}.items():
        if not any(named(path, line) and re.search(r'\b(?:transcribe|listen|follow|instructions|specified|described)\b', line, re.I)
                   for line in instruction.splitlines()):
            continue
        for destination in mount_destinations(content):
            result[destination] = dict(kind='artifact_mount_destination', artifact=path,
                                       source=source, text=content[:4096])
    return result


def extraction_requests(instruction):
    """Associate explicit extraction destinations with named source locations."""
    requests = []
    # A numbered objective may contain several newline-separated bullets.
    blocks = re.split(r'\n(?=\s*\d+[.)]\s)', instruction)
    for block in blocks:
        if not re.search(r'\b(?:archive|tar|zip|unpack)\b', block, re.I):
            continue
        destinations = re.findall(r'\bextract(?:ing)?\b[^\n]*?\b(?:into|to)\s+(?:the\s+)?(?:directory\s+)?[`\"\']?(' + PATH + ')', block, re.I)
        for destination in destinations:
            destination = destination.rstrip('/')
            # Sources must be explicitly named, not every archive on the image.
            paths = re.findall(PATH, block)
            sources = [p.rstrip('/') for p in paths if p.rstrip('/') != destination
                       and (p.endswith('/') or re.search(r'\.(?:tar(?:\.gz|\.bz2|\.xz)?|tgz|zip|bin|pkg)$', p))]
            if sources:
                requests.append(dict(destination=destination, sources=sorted(set(sources)),
                                     recursive=bool(re.search(r'\b(?:recursive|recursively|nested)\b', block, re.I))))
    return requests


def archive_member(path, requests, deadline=None):
    """Read-only, bounded TAR/ZIP indexing, including archives named .bin.

    Never extract to disk or execute an extractor. Only a regular member whose
    destination matches the requested output establishes recoverability.
    """
    import os
    import io
    import time
    import tarfile
    import zipfile
    import posixpath
    deadline = min(time.monotonic() + 3, deadline) if deadline is not None else time.monotonic() + 3
    limit = 32 * 1024 * 1024
    visited = 0
    nesting = 0

    def safe(name):
        return not name.startswith('/') and '..' not in name.split('/')

    def inspect(data, relative, chain, depth=0):
        nonlocal visited
        if depth > nesting or time.monotonic() > deadline:
            return None
        stream = io.BytesIO(data)
        nested = []
        if zipfile.is_zipfile(stream):
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                for item in archive.infolist():
                    visited += 1
                    if visited > 4096 or time.monotonic() > deadline:
                        return None
                    if not safe(item.filename) or item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                        continue
                    if posixpath.normpath(item.filename) == relative:
                        return dict(status='recoverable', archive=chain[0], members=chain[1:] + [item.filename])
                    if item.file_size <= limit and depth < nesting and item.filename.endswith(('.tar','.tgz','.tar.gz','.zip','.bin')):
                        nested.append((item.filename, archive.read(item)))
        else:
            stream.seek(0)
            try:
                with tarfile.open(fileobj=stream, mode='r:*') as archive:
                    for item in archive:
                        visited += 1
                        if visited > 4096 or time.monotonic() > deadline or item.size > limit:
                            return None
                        if not item.isfile() or not safe(item.name):
                            continue
                        if posixpath.normpath(item.name) == relative:
                            return dict(status='recoverable', archive=chain[0], members=chain[1:] + [item.name])
                        if depth < nesting and item.name.endswith(('.tar','.tgz','.tar.gz','.zip','.bin')):
                            nested.append((item.name, archive.extractfile(item).read(limit + 1)))
            except tarfile.TarError:
                return None
        for name, payload in nested:
            answer = inspect(payload, relative, chain + [name], depth + 1)
            if answer:
                return answer
        return None

    for request in requests:
        nesting = 2 if request.get('recursive') else 0
        root = request['destination'].rstrip('/') + '/'
        if not path.startswith(root):
            continue
        relative = path[len(root):]
        for source in request['sources']:
            candidates = []
            if os.path.isfile(source):
                candidates = [source]
            elif os.path.isdir(source):
                for directory, dirs, names in os.walk(source, followlinks=False):
                    dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(directory, d))]
                    candidates.extend(os.path.join(directory, n) for n in names)
                    if len(candidates) > 256 or time.monotonic() > deadline:
                        break
            for candidate in candidates[:256]:
                if time.monotonic() > deadline:
                    return {'status':'unresolved', 'reason':'archive inspection budget reached'}
                try:
                    if os.path.islink(candidate) or os.path.getsize(candidate) > limit:
                        continue
                    with open(candidate,'rb') as stream:
                        answer = inspect(stream.read(limit + 1), relative, [candidate])
                    if answer:
                        answer['destination'] = request['destination']
                        return answer
                except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile):
                    continue
    return {'status':'not_found_or_unsupported'}
