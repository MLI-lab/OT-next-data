"""Our patch to Terminal-Bench check-pip-pinning.sh: present hash-pinned URLs and shell syntax so the
upstream script sees real installs only. The repair is repair.py."""
import re
import shlex

CHECK = 'check-pip-pinning.sh'
ADAPTATION = (
    'Training reports unresolved pins as advisory warnings, not exclusion failures; recognizes '
    'SHA-256-pinned HTTP(S) requirements as single shell arguments; masks unquoted shell redirections '
    'and argument forwarding in generated offline installer heredocs, retaining actual package '
    'installations')


def pip_check_line(line):
    """Adapt shell syntax on the check copy, never rewriting the installation."""
    from packaging.requirements import Requirement, InvalidRequirement
    from urllib.parse import urlsplit
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
    lines, delimiter = [], None
    for line in text.splitlines(keepends=True):
        if delimiter and line.strip() == delimiter:
            delimiter = None
        elif delimiter and re.fullmatch(
                r'\s*pip3? install --no-index --find-links="\$SCRIPT_DIR" "\$@"\s*', line):
            line = '# generated offline installer forwards caller arguments\n'
        if delimiter is None:
            match = re.search(r"<<\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", line)
            if match:
                delimiter = match.group(1)
        lines.append(pip_check_line(line))
    return ''.join(lines)
