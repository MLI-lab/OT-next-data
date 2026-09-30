"""Conservative lexical splitting of submitted shell lists; never executes input.

Adapted from tb-science-discovery/analysis/traces/shell_commands.py.

Simple lists/pipelines are split. Quoted strings and heredoc bodies are opaque.
Unsupported compound shell grammar is preserved as one flagged submission.
"""
import re
import shlex


def split_commands(text):
    parts, buf, pending = [], [], []
    quote = None
    depth = 0
    complex_syntax = False
    i = 0

    def flush():
        value = ''.join(buf).strip()
        if value:
            parts.append(value)
        buf.clear()

    while i < len(text):
        c = text[i]
        if c == '\\' and quote != "'" and i + 1 < len(text):
            buf.append(text[i:i+2])
            i += 2
            continue
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == '#' and (i == 0 or text[i-1].isspace()):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith('<<<', i):
            buf.append('<<<')
            i += 3
            continue
        if text.startswith('<<', i):
            m = re.match(r"<<(-?)[ \t]*('([^']*)'|\"([^\"]*)\"|([A-Za-z_][\w]*))", text[i:])
            if not m:
                return [text.strip()], True
            pending.append((m[3] or m[4] or m[5], bool(m[1])))
            buf.append(m[0])
            i += len(m[0])
            continue
        if c in '({':
            depth += 1
            complex_syntax = True
        elif c in ')}':
            depth -= 1
        if c == '\n' and pending and depth == 0:
            flush()
            i += 1
            for delimiter, tabs in pending:
                while True:
                    end = text.find('\n', i)
                    line = text[i:] if end < 0 else text[i:end]
                    i = len(text) if end < 0 else end + 1
                    if (line.lstrip('\t') if tabs else line) == delimiter:
                        break
                    if end < 0:
                        return [text.strip()], True
            pending.clear()
            continue
        redirect_amp = c == '&' and ((i > 0 and text[i-1] in '<>') or text[i:i+2] == '&>')
        if depth == 0 and c in ';|&\n' and not redirect_amp:
            flush()
            i += 1
            if c in '|&' and i < len(text) and text[i] == c:
                i += 1
            continue
        buf.append(c)
        i += 1
    flush()
    if quote or depth or pending or complex_syntax:
        return [text.strip()], True
    # These need an AST to distinguish shell syntax from executable commands.
    for part in parts:
        try:
            first = shlex.split(part)[0]
        except (ValueError, IndexError):
            return [text.strip()], True
        if first in ('if', 'then', 'elif', 'else', 'fi', 'for', 'while', 'until', 'do', 'done', 'case', 'esac', 'select', 'function', '!', 'time', '[[', ']]'):
            return [text.strip()], True
    return parts, False
