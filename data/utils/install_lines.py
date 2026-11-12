"""Canonical form of shell lines that only install apt/pip packages (moved from the retired TMax v1 patcher)."""
import re
import shlex


APT_FLAGS = {"-y", "-q", "-qq", "-yq", "-qy", "-yqq", "-qqy", "--yes", "--assume-yes",
             "--no-install-recommends", "--fix-missing"}
APT_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.:=~-]*")
PIP_FLAGS = {"--no-cache-dir", "--break-system-packages", "-q", "--quiet", "-U", "--upgrade",
             "--no-input", "--prefer-binary", "--ignore-installed", "--force-reinstall",
             "--disable-pip-version-check", "--root-user-action=ignore"}
PIP_TIMEOUT = re.compile(r"--default-timeout=\d+")
PIP_INDEX_FLAGS = {"--index-url", "-i", "--extra-index-url"}
PIP_INDEXES = {"https://download.pytorch.org/whl/cpu"}
_SPEC = r"(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!-]+"
PIP_REQUIREMENT = re.compile(
    rf"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?(?:{_SPEC}(?:,{_SPEC})*)?")


def _canonical(head, flags, packages):
    return " ".join([*head, *sorted(set(flags)), *(shlex.quote(p) for p in sorted(set(packages)))])


def _install_command(tokens):
    """Canonical text of one allowed install command, or None."""
    head = []
    if tokens[:1] == ["DEBIAN_FRONTEND=noninteractive"]:
        head, tokens = tokens[:1], tokens[1:]
    if not tokens:
        return None
    if not head and tokens == ["export", "DEBIAN_FRONTEND=noninteractive"]:
        return " ".join(tokens)
    if not head and tokens == ["rm", "-rf", "/var/lib/apt/lists/*"]:
        return " ".join(tokens)
    if tokens[0] in ("apt-get", "apt"):
        rest = tokens[1:]
        action = next((t for t in rest if not t.startswith("-")), None)
        if action not in ("update", "install", "clean"):
            return None
        rest.remove(action)
        flags = [t for t in rest if t.startswith("-")]
        packages = [t for t in rest if not t.startswith("-")]
        if set(flags) - APT_FLAGS or any(not APT_PACKAGE.fullmatch(p) for p in packages):
            return None
        if (action == "install") != bool(packages):
            return None
        return _canonical([*head, tokens[0], action], flags, packages)
    if tokens[:3] in (["python3", "-m", "pip"], ["python", "-m", "pip"]):
        tool, rest = tokens[:3], tokens[3:]
    elif tokens[0] in ("pip", "pip3"):
        tool, rest = tokens[:1], tokens[1:]
    else:
        return None
    if head or rest[:1] != ["install"]:
        return None
    flags, packages, rest = [], [], rest[1:]
    while rest:
        token = rest.pop(0)
        name, _, value = token.partition("=")
        if name in PIP_INDEX_FLAGS and (value or rest):
            value = value or rest.pop(0)
            if value not in PIP_INDEXES:
                return None
            flags.append(f"{name} {value}")
        elif token.startswith("-"):
            if token not in PIP_FLAGS and not PIP_TIMEOUT.fullmatch(token):
                return None
            flags.append(token)
        elif PIP_REQUIREMENT.fullmatch(token):
            packages.append(token)
        else:
            return None
    if not packages:
        return None
    return _canonical([*tool, "install"], flags, packages)


def install_line(command):
    """Canonical form of a line that only installs packages; "" for blank/comment; else None."""
    text = command.strip()
    if not text or text.startswith("#"):
        return ""
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    parts, current = [], []
    for token in tokens:
        if token in ("&&", ";"):
            parts += [current, token]
            current = []
        elif token and not set(token) - set("();<>|&"):
            return None
        else:
            current.append(token)
    if current:
        parts.append(current)
    result = []
    for part in parts:
        if isinstance(part, str):
            result.append(" &&" if part == "&&" else ";")
            continue
        canonical = _install_command(part) if part else None
        if canonical is None:
            return None
        result.append((" " if result else "") + canonical)
    return "".join(result).rstrip(";") if result and result[-1] != " &&" else None
