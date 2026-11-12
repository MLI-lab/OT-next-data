"""Conservative shell source handling for the verifier's static path inventory."""
import re
import shlex
from pathlib import PurePosixPath

HEREDOC = re.compile(r'<<(-?)\s*[\x27\"]?(\w+)')


def python_heredoc_command(line):
    """Recognize the command receiving the heredoc, not words in its arguments."""
    prefix = line.split('<<', 1)[0]
    # A heredoc belongs to the final simple command before its redirection.
    try:
        lexer = shlex.shlex(prefix, posix=True, punctuation_chars=';&|')
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return False
    for i, word in enumerate(words):
        if word in (';', '&&', '||', '|'):
            words = words[i + 1:]
            return python_heredoc_command(shlex.join(words) + ' <<EOF')
    while words and (words[0] in ('if', 'then', 'elif', 'while', 'until', '!', 'command', 'exec')
                     or re.match(r'^[A-Za-z_]\w*=', words[0])):
        words.pop(0)
    if words and PurePosixPath(words[0]).name == 'env':
        words.pop(0)
        while words and re.match(r'^[A-Za-z_]\w*=', words[0]):
            words.pop(0)
    return bool(words and re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', PurePosixPath(words[0]).name))


def unused_function_lines(lines):
    """Skip only simple, unreferenced function definitions; uncertain shell stays visible.

    No call-graph inference: any mention of the name keeps the body. Dynamic
    dispatch, sourcing, traps and exported functions disable this optimization.
    Heredoc payloads cannot supply shell braces or function declarations.
    """
    shell = list(lines)
    cursor = 0
    while cursor < len(lines):
        match = HEREDOC.search(lines[cursor])
        if match:
            end = cursor + 1
            while end < len(lines) and lines[end].strip() != match[2]:
                shell[end] = ''
                end += 1
            if end < len(lines):
                shell[end] = ''
            cursor = end
        cursor += 1
    text = '\n'.join(shell)
    if (re.search(r'\b(eval|source|trap)\b|(?:^|[;\n])\s*\.\s|export\s+-f|\b(?:ba)?sh\s+-c', text)
            or re.search(r'(?:^|[;\n&|]|\bthen|\bdo|\bif|\belif|\bwhile|\buntil|!)\s*["\x27]?\$|\$\(|`', text)):
        return set()
    skipped = set()
    for start, line in enumerate(shell):
        match = re.fullmatch(r'\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\s*\)\s*\{\s*(?:#.*)?', line)
        if not match:
            continue
        end = start + 1
        while end < len(shell) and shell[end].strip() != '}':
            end += 1
        if end == len(shell):
            continue
        # Nested shell groups/definitions require a real shell parser.
        if any(re.search(r'(?:^|\s)[{}](?:\s|$)', s) for s in shell[start + 1:end]):
            continue
        elsewhere = '\n'.join(shell[:start] + shell[start + 1:])
        if not re.search(r'\b' + re.escape(match[1]) + r'\b', elsewhere):
            skipped.update(range(start, end + 1))
    return skipped
