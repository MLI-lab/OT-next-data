"""Find references in initial supplied files without executing task setup."""
import json
import posixpath
import re
import shlex
import tarfile
from pathlib import Path

LIMIT = 1024 * 1024
PATHS = re.compile(r'(?<![\w/])/(?!/)[A-Za-z0-9_./+-]+')
# Basenames also occur after relative directories or shell variables:
# output/results.csv and "$WORKDIR/active.txt".
NAMES = re.compile(r'(?<![\w.-])[A-Za-z0-9_-][A-Za-z0-9_.-]*\.[A-Za-z0-9_.-]+(?![\w.-])')
PRIVATE = ('/setup_files', '/tests', '/solution')


def public(path):
    return path.startswith('/') and not any(path == p or path.startswith(p + '/') for p in PRIVATE)


def text_payload(data):
    if len(data) > LIMIT or b'\0' in data:
        return ''
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return ''


def literal_writes(command, cwd):
    """Only simple literal writes; never run shell commands or substitutions."""
    # Literal heredocs used to supply scripts/configuration.
    consumed = 0
    masked = command
    for match in re.finditer(r"(?:^|\n)\s*cat\s+([^\n]+)\n", command):
        if match.start() < consumed:
            continue
        header = match.group(1)
        end = re.search(r"<<\s*(['\"]?)(\w+)\1", header)
        dest = re.search(r'>\s*([^\s<>]+)', header)
        if not end or not dest:
            continue
        tail = command[match.end():]
        close = re.search(r'^' + re.escape(end.group(2)) + r'\s*$', tail, re.M)
        if close:
            body = tail[:close.start()]
            consumed = match.end() + close.end()
            masked = masked.replace(command[match.start():consumed], '[heredoc]', 1)
            # Unquoted heredocs with expansions cannot be reconstructed safely.
            if not end.group(1) and re.search(r'[$`]', body):
                continue
            if cwd is not None or dest.group(1).startswith('/'):
                yield posixpath.normpath(posixpath.join(cwd or '/', dest.group(1))), body, False
    try:
        lexer = shlex.shlex(masked, posix=False, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return
    segments, segment = [], []
    for token in tokens:
        if token in ('&&', ';'):
            segments.append(segment)
            segment = []
        else:
            segment.append(token)
    segments.append(segment)
    for parts in segments:
        if len(parts) < 4 or parts[-2] not in ('>', '>>') or parts[0] not in ('printf', 'echo'):
            continue
        decoded = []
        for token in parts[1:-2] + [parts[-1]]:
            if token.startswith("'") and token.endswith("'"):
                decoded.append(token[1:-1])
            elif re.search(r'[$`]', token):
                break
            elif token.startswith('"') and token.endswith('"'):
                decoded.append(token[1:-1])
            elif not re.search(r'[;&|<>]', token):
                decoded.append(token)
            else:
                break
        else:
            args, destination = decoded[:-1], decoded[-1]
            if parts[0] == 'echo':
                if any(x.startswith('-') for x in args):
                    continue
                body = ' '.join(args) + '\n'
            else:
                if not args or re.search(r'%(?!s|%)', args[0]):
                    continue
                fmt = args[0].replace(r'\n', '\n').replace(r'\t', '\t')
                count = len(re.findall(r'(?<!%)%s', fmt))
                values = args[1:]
                if count and len(values) % count:
                    continue
                body = ''.join(fmt % tuple(values[i:i + count]) for i in range(0, len(values), count)) if count else fmt.replace('%%', '%')
            if cwd is not None or destination.startswith('/'):
                yield posixpath.normpath(posixpath.join(cwd or '/', destination)), body, parts[-2] == '>>'


def setup_statements(text):
    """Read straight-line statements, excluding heredoc bodies and control flow."""
    lines = text.splitlines(keepends=True)
    i, blocked = 0, 0
    while i < len(lines):
        start = i
        line = lines[i]
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        here = re.search(r"<<\s*(['\"]?)([A-Za-z_]\w*)\1", line)
        if here:
            while i < len(lines) and lines[i].strip() != here.group(2):
                i += 1
            if i >= len(lines):
                return
            i += 1
            if not blocked and stripped.startswith('cat '):
                yield ''.join(lines[start:i]), start + 1
            continue
        if re.match(r'(?:if|for|while|until|case|select)\b', stripped) or re.match(r'(?:function\s+)?\w+\s*\(\)\s*\{', stripped):
            blocked += 1
            continue
        if re.match(r'(?:fi|done|esac|\})\s*(?:;|$)', stripped):
            blocked = max(0, blocked - 1)
            continue
        if not blocked:
            yield line, start + 1


def setup_segments(statement):
    try:
        lexer = shlex.shlex(statement, posix=True, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return
    segment = []
    for token in tokens:
        if token in ('&&', ';'):
            yield segment
            segment = []
        else:
            segment.append(token)
    yield segment


def read_setup_files(command, cwd, files, source, directories, active=()):
    """Follow bounded, explicitly invoked shell scripts; never execute them."""
    if len(active) > 4:
        return
    if re.search(r'^\s*cd\b', command, re.M):
        cwd = None  # Preserve only absolute destinations when cwd can change.
    for count, (statement, line) in enumerate(setup_statements(command)):
        if count >= 10000:
            return
        if re.search(r'(?:^|[;&])\s*(?:exit|return|false)\b', statement):
            return
        origin = f'{source}:statement {line}'
        for path, content, append in literal_writes(statement, cwd):
            if public(path):
                if append:
                    content = files.get(path, ('', ''))[0] + content
                files[path] = (content, origin)
        if '\n' in statement.rstrip('\n'):
            continue  # A heredoc payload is supplied code, not executed setup.
        for words in setup_segments(statement):
            if not words or any(re.search(r'[$`]', word) for word in words):
                continue
            if words[0] in ('exit', 'return', 'false'):
                return
            if words[0] == 'mkdir':
                directories.update(posixpath.normpath(w) for w in words[1:] if w.startswith('/'))
            elif words[0] in ('cp', 'mv'):
                args = [w for w in words[1:] if w not in ('--', '-a', '-p', '-f')]
                if len(args) != 2 or not all(w.startswith('/') for w in args):
                    continue
                previous, destination = map(posixpath.normpath, args)
                if previous not in files or not public(destination):
                    continue
                if args[1].endswith('/') or destination in directories:
                    destination = posixpath.join(destination, posixpath.basename(previous))
                content, previous_source = files[previous]
                files[destination] = (content, f'{previous_source} -> {words[0]} at {origin}')
                if words[0] == 'mv' and destination != previous:
                    del files[previous]
            elif words[0] == 'rm':
                args = [w for w in words[1:] if w not in ('--', '-f', '-r', '-rf', '-fr')]
                recursive = any(w in ('-r', '-rf', '-fr') for w in words[1:])
                for path in args:
                    if not path.startswith('/'):
                        continue
                    path = posixpath.normpath(path)
                    for existing in list(files):
                        if existing == path or (recursive and existing.startswith(path.rstrip('/') + '/')):
                            del files[existing]
            else:
                # Shell invocations with an explicit local script, or a direct
                # executable whose shebang establishes it is a shell script.
                script = words[1] if words[0] in ('bash', 'sh', 'dash', '/bin/bash', '/bin/sh') and len(words) >= 2 else words[0]
                if not script.startswith('/') or script not in files or script in active:
                    continue
                content, script_source = files[script]
                if not re.match(r'#!\s*(?:/bin/(?:ba|da)?sh|/usr/bin/env\s+(?:ba|da)?sh)\b', content):
                    continue
                read_setup_files(content, cwd, files, f'{script_source} -> invoked {script}', directories, (*active, script))


def supplied_files(task):
    """Index COPY payloads and statically recognizable setup writes."""
    files = {}
    directories = set()
    manifest = task / 'setup_files/operations.json'
    if not manifest.is_file():
        return files
    try:
        operations = json.loads(manifest.read_text())['operations']
    except (ValueError, KeyError):
        return files
    for operation in operations:
        if operation.get('op') == 'COPY' and operation.get('archive'):
            archive_name = operation['archive']
            if Path(archive_name).name != archive_name:
                continue
            archive = task / 'setup_files' / archive_name
            if not archive.is_file():
                continue
            destination = operation.get('destination', '')
            if not destination.startswith('/'):
                continue
            with tarfile.open(archive) as stream:
                for member in stream:
                    if not member.isfile():
                        continue
                    relative = member.name.removeprefix('payload').lstrip('/')
                    if '..' in Path(relative).parts:
                        continue
                    directory = operation.get('directory') or destination.endswith('/')
                    if directory:
                        directories.add(posixpath.normpath(destination))
                        path = posixpath.join(destination, relative or Path(operation.get('source', '')).name)
                    else:
                        path = posixpath.join(destination, relative) if relative else destination
                    if public(path):
                        content = stream.extractfile(member).read(LIMIT + 1) if member.size <= LIMIT else b''
                        files[path] = (text_payload(content), f'setup_files/{archive_name}:{member.name}')
        elif operation.get('op') == 'RUN':
            read_setup_files(operation.get('command', ''), operation.get('cwd', '/'), files,
                             f"setup_files/operations.json:line {operation.get('line', '?')}", directories)
    return files


def configured_label_references(instruction, path, content, proof):
    """Expand explicit label templates using a supplied label:glob configuration."""
    if not re.search(r'\blabel\s*:\s*glob_pattern\b', instruction):
        return {}
    if not path.endswith(('.conf', '.cfg', '.ini')):
        return {}
    templates = re.findall(r'([A-Za-z0-9_.-]*<label>[A-Za-z0-9_.-]*\.[A-Za-z0-9]+)', instruction)
    rows = [line.strip() for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    parsed = [re.fullmatch(r'([A-Za-z0-9_][A-Za-z0-9_-]*)\s*:\s*(\S.*)', line) for line in rows]
    if not rows or not all(parsed):
        return {}
    return {template.replace('<label>', row.group(1)):
            {**proof, 'instruction_pattern': template, 'configuration_label': row.group(1)}
            for template in templates for row in parsed}


def discoverable_references(task):
    """Inspect explicitly named supplied files/directories and follow file links.

    Return reference -> source evidence. Never consult tests or solve.sh.
    Directory inspection only exposes files actually present before solving.
    """
    if not (task / 'instruction.md').is_file():
        return {}
    instruction = (task / 'instruction.md').read_text(errors='replace')
    files = supplied_files(task)
    evidence = {}
    queue = []
    # A named supplied basename is unambiguous only when exactly one file fits.
    for match in NAMES.finditer(instruction):
        if match.start() and instruction[match.start() - 1] == '/':
            continue
        name = match.group(0)
        candidates = [path for path in files if posixpath.basename(path) == name]
        if len(candidates) == 1:
            queue.append((candidates[0], name))
    for match in PATHS.finditer(instruction):
        root = match.group(0).rstrip('.,')
        if not public(root):
            continue
        if root in files:
            queue.append((root, root))
        else:
            # Only explicit directory syntax permits directory enumeration.
            if root.endswith('/'):
                for path in files:
                    if path.startswith(root):
                        queue.append((path, root))
                        evidence[path] = {'entry_point': root, 'supplied_file': path, 'source': files[path][1]}
    seen = set()
    while queue:
        path, entry = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        content, source = files[path]
        proof = {'entry_point': entry, 'supplied_file': path, 'source': source}
        evidence.update(configured_label_references(instruction, path, content, proof))
        for match in PATHS.finditer(content):
            reference = match.group(0).rstrip('.,')
            if public(reference):
                evidence.setdefault(reference, proof)
                if reference in files:
                    queue.append((reference, entry))
        for match in NAMES.finditer(content):
            reference = match.group(0)
            evidence.setdefault(reference, proof)
            relative = posixpath.join(posixpath.dirname(path), reference)
            if relative in files:
                queue.append((relative, entry))
    return evidence
