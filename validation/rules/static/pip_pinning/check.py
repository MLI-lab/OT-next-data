"""Our patch to Terminal-Bench check-pip-pinning.sh: present hash-pinned URLs and shell syntax so the
upstream script sees real installs only. The repair is repair.py."""
import re
import shlex

CHECK = 'check-pip-pinning.sh'
ADAPTATION = (
    'Training reports unresolved pins as advisory warnings, not exclusion failures; recognizes '
    'SHA-256-pinned HTTP(S) requirements as single shell arguments; masks unquoted shell redirections '
    'and argument forwarding in generated offline installer heredocs, retaining actual package '
    'installations'
    '; separates shell commands even without whitespace before semicolons, including literal shell -c payloads, so later commands are not package arguments and later unpinned installations remain checked; allows a variable-based local package subdirectory only with literal absolute directory assignments')


def pip_check_line(line, local_directories=()):
    """Adapt shell syntax on the check copy, never rewriting the installation."""
    from packaging.requirements import Requirement, InvalidRequirement
    from urllib.parse import urlsplit
    # Upstream's separator regex requires whitespace before ';'. A literal
    # shell -c payload can otherwise consume its next command as package names.
    # Split at real shell operators, including inside a known
    # shell -c payload; never reinterpret arbitrary quoted package arguments.
    shell_payload = re.search(r'''\b(?:bash|sh|dash)\s+-c\s+(['"])(.*)\1\s*$''', line)
    if shell_payload:
        start, end = shell_payload.span(2)
        line = line[:start] + pip_check_line(shell_payload[2], local_directories) + line[end:]
    # Keep quotes intact so quoted '>' and spaces inside requirements are not
    # mistaken for shell operators or separate package arguments.
    pattern = r'''(?:\d+)?(?:>>?|<<?)&?|&&|\|\||[;|&]|(?:[^\s'"\\<>;&|]+|\\[^\n]|'[^']*'|"(?:\\.|[^"\\])*")+'''
    tokens = list(re.finditer(pattern, line))
    cursor = 0
    for token in tokens:
        if line[cursor:token.start()].strip():
            return line  # Unsupported/malformed shell syntax: keep the finding.
        cursor = token.end()
    if line[cursor:].strip():
        return line
    edits, i = [], 0
    while i < len(tokens):
        token = tokens[i]
        raw = token.group()
        if raw in (';', '&&', '||', '|', '&'):
            # Upstream scans only the first install per line. Separate commands
            # so a later unpinned installation cannot hide behind a pinned one.
            edits.append((token.start(), token.end(), '\n'))
        if raw.startswith('#'):
            break
        if re.fullmatch(r'(?:\d+)?(?:>>?|<)&?', raw) and i + 1 < len(tokens):
            destination = tokens[i + 1]
            if destination.group() not in ('&&', '||', ';', '|', '&'):
                edits.append((token.start(), destination.end(), ' '))
                i += 2
                continue
        try:
            value, = shlex.split(raw)
        except ValueError:
            i += 1
            continue
        local = re.fullmatch(r'\$([A-Za-z_]\w*)/[A-Za-z0-9_./-]+', value)
        if local and local[1] in local_directories:
            # Same exemption as an explicit /local/path, with a proven literal
            # absolute assignment. Unknown variables and URLs remain findings.
            edits.append((token.start(), token.end(), '/local-package-source'))
            i += 1
            continue
        url = value
        name = 'hash-pinned-artifact'
        try:
            requirement = Requirement(value)
            if requirement.url and not requirement.marker:
                url, name = requirement.url, requirement.name
        except InvalidRequirement:
            pass
        try:
            parsed = urlsplit(url)
        except ValueError:
            i += 1
            continue
        if (parsed.scheme in ('http', 'https') and parsed.netloc
                and not any(c in url for c in '$`\\')
                and re.fullmatch(r'sha256=[0-9a-fA-F]{64}', parsed.fragment)):
            # Upstream understands == pins, but splits direct references at
            # spaces. This placeholder exists only in its scratch input.
            edits.append((token.start(), token.end(), name + '==0'))
        i += 1
    for start, end, replacement in reversed(edits):
        line = line[:start] + replacement + line[end:]
    return line


def pip_check_text(text):
    # Generated offline installers are tools accepting user-specified packages,
    # not an unpinned install into the task environment. Only exempt forwarding
    # inside a quoted heredoc; retain concrete installs inside those heredocs.
    assignments = {}
    for match in re.finditer(r'(?m)^\s*([A-Za-z_]\w*)=([^\n]*)$', text):
        assignments.setdefault(match[1], []).append(match[2])
    local_directories = {name for name, values in assignments.items()
                         if all(re.fullmatch(r'/[A-Za-z0-9_./-]+', value) for value in values)}
    lines, delimiter = [], None
    for line in text.splitlines(keepends=True):
        header = re.match(r'''\s*(?:/(?:usr/)?bin/)?(?:bash|sh|dash)(?:\s+-[a-zA-Z]+)*\s+-c\s+(['"])(.*)''', line)
        if header:
            try:
                shlex.split(line)
            except ValueError:
                # A literal -c argument can span lines (often a heredoc). The
                # opening quote is shell packaging, not part of a requirement.
                # Normalize its first physical command line on the check copy.
                line = header[2] + '\n'
        if delimiter and line.strip() == delimiter:
            delimiter = None
        elif delimiter and re.fullmatch(
                r'\s*pip3? install --no-index --find-links="\$SCRIPT_DIR" "\$@"\s*', line):
            line = '# generated offline installer forwards caller arguments\n'
        if delimiter is None:
            match = re.search(r"<<\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", line)
            if match:
                delimiter = match.group(1)
        lines.append(pip_check_line(line, local_directories))
    return ''.join(lines)
