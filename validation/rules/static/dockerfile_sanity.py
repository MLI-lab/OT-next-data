"""Our patch to Terminal-Bench check-dockerfile-sanity.sh: give upstream only apt arguments."""
import re
import shlex
from validation.rules.static.dockerfile_references import dockerfile_without_comments

CHECK = 'check-dockerfile-sanity.sh'
ADAPTATION = (
    'Inspect apt install arguments within RUN commands, including continuations and quoted pins; do not '
    'scan unrelated ENV assignments or embedded source following apt.')


def dockerfile_sanity_text(text):
    """Give upstream only apt command arguments, not the next 20 source lines.

    Shell tokenization preserves quoted pins and joins continued RUN commands.
    Unparseable shell is kept for conservative upstream checking.
    """
    text = dockerfile_without_comments(text)
    logical = re.sub(r'\\\n', ' ', text)
    commands = []
    for line in logical.splitlines():
        if not re.match(r'^\s*RUN\s', line, re.I):
            continue
        if re.search(r'\bapt(?:-get)?\b', line) and ('<<' in line or re.match(r'^\s*RUN\s*\[', line, re.I) or re.search(r'\b(?:sh|bash)\s+-c', line)):
            return text  # Preserve conservative checking of unsupported shell forms.
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=';&|()<>' )
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            return text
        for i, token in enumerate(tokens):
            if token.rsplit('/', 1)[-1] not in ('apt', 'apt-get'):
                continue
            args = []
            for arg in tokens[i + 1:]:
                if arg and all(c in ';&|()<>' for c in arg):
                    break
                args.append(arg)
            # Keep update/cleanup advisory behavior, but expose version pins
            # only in install arguments. Epochs/integer pins count too.
            if 'install' in args:
                if (i and tokens[i - 1] not in ('RUN', 'run', '&&', '||', ';', '|', 'sudo', 'env', 'then', 'do', '{', '(', '!')
                        and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', tokens[i - 1])):
                    return text  # Do not silently exempt unknown command wrappers.
                packages = args[args.index('install') + 1:]
                pinned = [a for a in packages if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9+.:_-]*=[^=\s]+', a)]
                commands.append('RUN apt install ' + ('pinned-package=1.0' if pinned else 'packages'))
            elif 'update' in args:
                commands.append('RUN apt update')
    if 'rm -rf /var/lib/apt/lists' in text:
        commands.append('RUN rm -rf /var/lib/apt/lists/*')
    return '\n'.join(commands) + '\n'
