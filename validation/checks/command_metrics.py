"""Lexical command profiles for sampling, never execution or activity inference.

Adapted from tb-science-discovery/analysis/traces/{shell_commands,trajectory_stats}.py.
Counts submitted command heads, not successful executions. Terminal keystrokes
may target an interactive program, so their interpretation remains heuristic.
"""
from collections import Counter
import json
from pathlib import PurePosixPath
import re
import shlex

from validation.checks.shell_commands import split_commands

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
