"""Profile submitted shell commands for diverse, reproducible pilot sampling."""

# Adapted from tb-science-discovery/analysis/traces/{shell_commands,trajectory_stats}.py.
from collections import Counter
import json
from pathlib import PurePosixPath
import re
import shlex

VERSION = 1


def command_name(text):
    try:
        words = shlex.split(text, comments=True)
    except ValueError:
        return '[unknown]'
    while words and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[0]):
        words.pop(0)
    # Simple wrappers only; option-bearing wrappers need a real shell parser.
    while words and words[0] in ('env', 'sudo', 'command', 'exec', 'nohup'):
        words.pop(0)
        while words and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[0]):
            words.pop(0)
    if not words:
        return '[assignment]'
    head = words[0]
    if head.startswith(('-', '<', '>')) or any(c in head for c in '$`*?[]'):
        return '[unknown]'
    name = PurePosixPath(head).name
    # Treat versioned interpreters alike, but preserve python -m pytest, etc.
    if re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', name):
        name = 'python'
        if len(words) > 2 and words[1] == '-m':
            name += '-m:' + words[2]
    return name or '[unknown]'


def profile(steps):
    if steps is None:
        return None
    counts = Counter()
    submissions = polls = controls = fallback = 0
    for step in steps:
        if step.get('source') != 'agent':
            continue
        for call in step.get('tool_calls') or []:
            name = call.get('function_name', 'unknown')
            if name == 'mark_task_complete':
                continue
            arguments = call.get('arguments') or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = None
            if not isinstance(arguments, dict):
                counts['[unknown]'] += 1
                continue
            command = next((arguments[k] for k in ('keystrokes', 'command', 'cmd')
                            if isinstance(arguments.get(k), str)), None)
            if command is None:
                # Native Read/Edit tools are still meaningful behavioural evidence.
                label = '[unknown]' if name in ('Bash', 'bash_command', 'exec_command', 'shell_command') else 'tool:' + name
                counts[label] += 1
                continue
            if not command.strip():
                polls += 1
                continue
            if command.strip() in ('C-c', 'C-d', 'C-z', '\x03', '\x04', '\x1a'):
                controls += 1
                continue
            submissions += 1
            parts, unsupported = split_commands(command)
            fallback += unsupported
            if unsupported:
                counts['[unknown]'] += 1
            else:
                counts.update(command_name(part) for part in parts)
    return {'version': VERSION, 'counts': dict(sorted(counts.items())),
            'submissions': submissions, 'polls': polls, 'controls': controls,
            'unsupported_submissions': fallback}


def normalized(counts):
    total = sum(counts.values())
    return {k: v / total for k, v in sorted(counts.items()) if v > 0} if total else {}


def sampling_distribution(command_profile):
    """Keep missing/entirely unparsed evidence out of distance selection."""
    if command_profile is None:
        return None
    counts = command_profile['counts']
    known = {k: v for k, v in counts.items() if k != '[unknown]'}
    if not known:
        return None if counts else {'[no_commands]': 1.0}
    return normalized(counts)


def mean_distribution(profiles):
    distributions = [d for p in profiles if (d := sampling_distribution(p)) is not None]
    if not distributions:
        return None
    total = Counter()
    for distribution in distributions:
        total.update(distribution)
    return {k: v / len(distributions) for k, v in sorted(total.items())}


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
