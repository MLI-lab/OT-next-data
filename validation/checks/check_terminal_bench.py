#!/usr/bin/env python3
"""Run pinned Terminal-Bench checks with training-task defaults and optional GPTZero."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.data.selection import discover_tasks
from validation.checks.supplied_references import discoverable_references

TERMINAL_BENCH = Path(__file__).resolve().parents[1] / 'upstream/terminal_bench'
# These are policy decisions in our wrapper; upstream files remain unmodified.
POLICY = {
    'check-canary.sh': 'Terminal-Bench-specific canary GUID',
    'check-task-fields.sh': 'benchmark author metadata, taxonomy and README sections',
    'check-task-timeout.sh': 'benchmark timeout caps',
    'check-instruction-suffix.sh': 'benchmark-specific instruction suffix',
    'check-instruction-headings.sh': 'benchmark prose-only instruction style',
    'check-gpu-types.sh': 'Modal-specific GPU allowlist',
    'check-resource-sizes.sh': 'benchmark CPU/memory size allowlist',
    'check-allow-internet.sh': 'benchmark requires default internet access',
    'check-no-allow-internet-true.sh': 'benchmark forbids explicit internet opt-in',
    'check-task-slug.sh': 'benchmark limit of three slug tokens',
    'check-task-package-name.sh': 'requires terminal-bench/<task> package namespace',
    'check-separate-verifier.sh': 'requires separate verifier mode for every task',
    'check-pytest-version.sh': 'benchmark-wide exact pytest/CTRF versions',
}
PR_ONLY = {'check-task-changelog.sh': 'training tasks do not require Terminal-Bench PR changelogs'}
TRAINING_EXCLUSIONS = {
    'check-test-sh-sanity.sh': 'training permits shared system Python and image-installed verifier dependencies; uv is not required, and stages 4/5 check execution',
    'check-separate-verifier.sh': 'training permits grading in the agent environment, including installed packages and changed system state; separate verification is optional',
    'check-allow-internet.sh': 'network policy is declared by the task and checked at runtime',
    'check-no-allow-internet-true.sh': 'explicit task network policies are allowed',
    'check-pytest-version.sh': 'training tasks may use other pinned pytest/CTRF versions',
    'check-gpu-types.sh': 'Modal GPU naming conventions do not apply to the Helma execution profile',
    'check-canary.sh': 'training tasks do not require a benchmark canary string',
    'check-instruction-headings.sh': 'Markdown headings are allowed in training instructions',
    'check-task-fields.sh': 'training tasks do not require TB author GitHub metadata, taxonomy or explanation sections',
    'check-task-package-name.sh': 'training task package names need not use the terminal-bench namespace',
    'check-task-slug.sh': 'training task names may contain more than three hyphen-separated tokens',
}
CHECKER_UNIT_TESTS = ('test-author-github.py', 'test-instruction-headings.sh',
                      'test-resource-sizes.sh', 'test-task-changelog.sh')
AI_CHECK = 'check_ai_detection.py'



def load_checks(profile, upstream=None, exclude=()):
    manifest = json.loads((TERMINAL_BENCH / 'UPSTREAM.json').read_text())
    # Detect accidental edits or incomplete copies before executing anything.
    for name, digest in manifest['files'].items():
        if hashlib.sha256(((upstream or TERMINAL_BENCH) / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'upstream file differs from pinned copy: {name}')
    names = sorted(Path(p).name for p in manifest['files'] if p.startswith('scripts/checks/'))
    excluded = {**PR_ONLY, **(POLICY if profile == 'portable' else TRAINING_EXCLUSIONS if profile == 'training' else {}),
                'rubric_review.py': 'LLM rubric review is opt-in via stage 2, never a default static check'}
    inactive = {'rubric_review.py', *CHECKER_UNIT_TESTS}
    for value in exclude:
        # NAME=reason records why; a reason may contain commas, so it is one item.
        value, _, reason = value.partition('=')
        for requested in value.split(','):
            requested = requested.strip()
            aliases = {'ai-detection': 'check_ai_detection.py', 'rubric-review': 'rubric_review.py'}
            name = aliases.get(requested, requested)
            if name not in inactive and name != AI_CHECK:
                name = name if name.endswith('.sh') else name + '.sh'
                name = name if name.startswith('check-') else 'check-' + name
            if name not in names and name not in inactive:
                raise ValueError(f'unknown static check: {requested}; use --list')
            excluded[name] = (reason.strip() or 'explicitly excluded' if name in names else
                'upstream checker unit test, not dataset validation' if name in CHECKER_UNIT_TESTS else
                'not part of the static suite (LLM review is opt-in)')
    return manifest, [n for n in names if n not in excluded], excluded


def validate_input(task):
    import tomllib
    for name in ('instruction.md', 'task.toml', 'tests/test.sh'):
        if not (task / name).is_file():
            raise ValueError(f'missing {name}')
    if not (task / 'environment').is_dir():
        raise ValueError('missing environment/')
    with (task / 'task.toml').open('rb') as stream:
        tomllib.load(stream)
    if any(c.isspace() or c in '*?[]' for c in str(task)):
        raise ValueError(f'task directory path must not contain whitespace or glob characters: {task}')


UNSAFE = re.compile(r'[\s*?\[\]]')


def safe_copy(task, scratch):
    """Several upstream scripts split paths as shell words and expand globs, so a file
    named `Clase 4/[id].ts` would be skipped or misread and the check could pass wrongly.
    Such tasks are checked on a copy whose offending names have those characters
    replaced by '_'. The task itself is unchanged. Returns (copy, renamed) or (task, [])."""
    renamed = sorted(str(p.relative_to(task)) for p in task.rglob('*') if UNSAFE.search(str(p.relative_to(task))))
    if not renamed:
        return task, []
    copy = Path(scratch) / 'renamed' / task.name
    if copy.exists():
        shutil.rmtree(copy)
    for path in task.rglob('*'):
        target = copy / UNSAFE.sub('_', str(path.relative_to(task)))
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    return copy, renamed


PATH_CHECK = 'check-task-absolute-path.sh'
SHELL_FENCES = {'', 'bash', 'sh', 'shell', 'zsh', 'console', 'text', 'txt'}
ADAPTATIONS = {PATH_CHECK: 'Relative-path findings are advisory warnings and do not fail stage 1. Runs on a copy of the task whose instruction.md has its source-code blocks removed '
                           '(fenced blocks tagged with a programming language); masks complete absolute paths '
                           'and JavaScript console calls/sed substitutions so their substrings are not mistaken for paths; '
                           'masks complete non-file URLs, quoted absolute paths with spaces, simple inline commands rooted by cd /absolute/path &&, '
                           'and literal relative symlink targets with explicit absolute link locations; '
                           'skips all Markdown fenced blocks, recognizes $HOME/${HOME}/~/ paths, and accepts adjacent literal cd and command lines; '
                           'accepts explicit named-file-in-directory clauses, pure parent-navigation commands, and relative command tokens for uniquely absolute-named executables; '
                           'unresolved relative file references outside fenced blocks are otherwise kept',
               'check-test-file-references.sh': 'masks complete non-file URLs, XML hostnames and excluded system paths; fixes full-token extraction and shell redirections, preserving basenames without inventing /app paths; accepts conventional boot artifacts, explicit numeric X filename ranges, explicit label filename templates resolved from supplied label:glob configurations, and references discovered through named supplied files/directories; reads literal writes and copies/moves in explicitly invoked local setup shell scripts without executing them, with source evidence',
               'check-pip-pinning.sh': 'recognizes SHA-256-pinned HTTP(S) requirements as single shell arguments; masks unquoted shell redirections and argument forwarding in generated offline installer heredocs, retaining actual package installations',
               'check-nproc.sh': 'training permits measurement-only variables and the exact audited InferredBugs Python 2.7.18 Dockerfile build on Maven Java 8/11/17 (Helma Slurm affinity probes 934209/934274); also permits four content-pinned SETA solutions tested with 1/2 CPUs (Helma probe 948213); other worker selection remains checked; not a Docker quota-only portability guarantee',
               'file names': 'a task with whitespace or *?[] in a file name is checked on a copy where those characters '
                             'are replaced by _, because the upstream scripts split paths at spaces and expand globs; '
                             'the renamed files are listed per task'}


def without_source_code(instruction):
    """Drop fenced blocks tagged with a programming language: file names inside code
    to read or complete are not paths the task tells the agent to use.

    Source code can itself contain Markdown fences and '#' lines, so the first bare
    fence is not always the end of the block. The block ends at the first bare fence
    that is followed by a heading or by the end of the text; if there is none before
    the next tagged block, it ends at the first bare fence."""
    lines, kept, i = instruction.split('\n'), [], 0
    fence = lambda line: re.fullmatch(r'\s*```\s*([A-Za-z0-9_+#.-]*)\s*', line)
    def before_heading(j):
        following = next((l for l in lines[j + 1:] if l.strip()), None)
        return following is None or re.match(r'#{1,6} ', following) is not None
    while i < len(lines):
        match = fence(lines[i])
        if not match or match.group(1).lower() in SHELL_FENCES:
            kept.append(lines[i])
            i += 1
            continue
        bare = []
        for j in range(i + 1, len(lines)):
            inner = fence(lines[j])
            if inner and inner.group(1):
                break                # the next tagged block starts here
            if inner:
                bare.append(j)
        closing = next((j for j in bare if before_heading(j)), bare[0] if bare else None)
        if closing is None:          # unterminated block: leave the text for the check to see
            kept.append(lines[i])
            i += 1
            continue
        kept.append(f'[{match.group(1)} source code removed for the path check]')
        i = closing + 1
    return '\n'.join(kept)


def without_urls(text):
    # Mask whole tokens before upstream extracts filename-like suffixes.
    text = re.sub(r'\b(?![Ff][Ii][Ll][Ee]://)[A-Za-z][A-Za-z0-9+.-]*://[^\s`\"\'<>]+', '[URL]', text)
    # Bare host/path form, e.g. mirrors.ubuntu.com/mirrors.txt.
    return re.sub(r'\b(?:[A-Za-z0-9-]+\.)+(?:com|org|net|edu|gov|io)/[^\s`\"\'<>]+', '[URL]', text)


def without_fenced_blocks(text):
    """Skip Markdown fences of any language, including untagged and shell blocks."""
    kept, opening = [], None
    for line in text.splitlines(keepends=True):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\n'))
        if opening is None:
            if match:
                opening = match[1]
                kept.append('[fenced block omitted from path check]\n')
            else:
                kept.append(line)
        elif (match and match[1][0] == opening[0]
              and len(match[1]) >= len(opening) and not match[2].strip()):
            opening = None
    return ''.join(kept)


# Specific conventional interfaces, not an exemption for everything under /boot.
CONVENTIONAL_REFERENCE_PATHS = (
    r'/boot/grub/grub\.cfg',
    r'/boot/initrd\.img-[0-9][A-Za-z0-9_.+-]*',
)


def reference_check_script(source):
    """Correct lexical extraction on a temporary copy of pinned upstream."""
    old = "grep -E '^/(app|home|tests|tmp|usr|var|etc)/'"
    if source.count(old) != 1 or source.count('/[a-zA-Z0-9_./]+\\.') != 1:
        raise ValueError('upstream file-reference extractor changed')
    source = source.replace(old, "grep -E '^/'")
    # A quoted basename supplies no working directory. Keep it as a basename.
    source = source.replace("sed 's|^|/app/|'", 'cat')
    # Upstream uses optional quote markers (?) with basic sed regex syntax.
    # Extended regex syntax is required to strip the redirection / -o prefix.
    source = source.replace("sed 's/^>\\s*", "sed -E 's/^>\\s*")
    source = source.replace("sed 's/^-o\\s*", "sed -E 's/^-o\\s*")
    # Consume the full basename, including suffixes after .img (kernel versions).
    lines = source.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if '/[a-zA-Z0-9_./]+\\.' in line:
            line = line.replace('/[a-zA-Z0-9_./]+\\.', '/[a-zA-Z0-9_./-]+\\.')
            line = line.replace("grep -oE '/", "grep -oE '(^|[[:space:]=])/")
            lines[i] = line.replace("wad)'", "wad)[a-zA-Z0-9_.+-]*'").rstrip('\n') + " | sed 's|^[^/]||'\n"
    return ''.join(lines)


def reference_check_text(text):
    text = without_urls(text)
    text = re.sub(r'file://(?=/)', '', text)
    text = re.sub(r'<host>[^<\n]+</host>', '<host>[hostname]</host>', text)
    # Upstream deliberately excludes these system roots, but its second regex
    # can truncate a hyphenated path into e.g. /xsettings.xml and flag it again.
    text = re.sub(r'/(?:tests|tmp|usr|var|etc)/[^\s`\"\'<>;]+', '[system path]', text)
    for path in CONVENTIONAL_REFERENCE_PATHS:
        text = re.sub(r'(?<![\w/])' + path + r'(?![\w./+-])', '[conventional boot artifact]', text)
    return text


def numeric_reference_patterns(instruction, sources):
    """Accept an X filename template only with an explicit numeric X range."""
    bounds = re.search(r'\b(?:where\s+)?X\s+is\s+(\d+)\s*[-–]\s*(\d+)\b', instruction)
    if not bounds:
        return {}
    low, high = map(int, bounds.groups())
    result = {}
    for match in re.finditer(r'\b([A-Za-z0-9_.-]*_X(?:_[A-Za-z0-9_.-]+)?\.[A-Za-z0-9]+)\b', instruction):
        template = match.group(1)
        expression = re.escape(template).replace('_X', r'_(\d+)')
        for candidate in re.finditer(r'(?<![\w.-])' + expression + r'(?![\w.-])', sources):
            if low <= int(candidate.group(1)) <= high:
                result[candidate.group(0)] = {'instruction_pattern': template, 'numeric_range': [low, high]}
    return result


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


def nproc_check_text(text, *, dockerfile=False):
    """Conservatively recognize measurement-only variables; not a shell parser.

    Also recognize the exact Python 2 build audited under Slurm CPU affinity.
    Other build flags and unknown uses retain the original check.
    """
    if dockerfile and re.search(r'^FROM maven:3\.9-eclipse-temurin-(?:8|11|17)$', text, re.M):
        # Match the build context and complete command, not make/nproc generally.
        # Mask only on the check's scratch copy; the actual task is unchanged.
        reviewed = (
            ' && tar -xzf "$d/python2.tgz" -C "$d" && cd "$d/Python-2.7.18" \\\n'
            ' && ./configure --prefix=/opt/python2 --disable-shared --without-ensurepip >/dev/null '
            '&& make -j"$(nproc)" >/dev/null && make install >/dev/null \\\n'
            ' && cd / && rm -rf "$d" && /opt/python2/bin/python2.7 -c "import zlib"'
        )
        text = text.replace(reviewed, reviewed.replace('$(nproc)', 'VERIFIED_CPU_AFFINITY'))
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        assignment = re.fullmatch(r'\s*([A-Za-z_]\w*)="?\$\(nproc\)"?\s*', line)
        if not assignment:
            continue
        var = assignment.group(1)
        refs = re.compile(r'\$(?:' + re.escape(var) + r'\b|\{' + re.escape(var) + r'\})')
        uses = [s.strip() for s in lines if refs.search(s) and not s.lstrip().startswith('#')]
        def measurement(s):
            # JSON fields: no command substitutions, backticks or shell chains.
            if s.startswith(('{"', '"')) and not re.search(r'\$\(|`|[;&|]', s):
                return True
            if re.fullmatch(r'if \(\( \$\(echo "\$\w+ > \$' + re.escape(var) + r'" \| bc -l\) \)\); then', s):
                return True
            return bool(re.fullmatch(r'alerts(?:\+)?=\(?"[^\n]*"\)?', s)) and not re.search(r'\$\(|`|[;&|]', s)
        if uses and all(measurement(s) for s in uses):
            lines[i] = line.replace('$(nproc)', 'CPU_MEASUREMENT')
    return ''.join(lines)


def adapted_check_copy(task, scratch, name, profile):
    transforms = {'check-test-file-references.sh': reference_check_text,
                  'check-pip-pinning.sh': pip_check_text}
    if profile == 'training':
        transforms['check-nproc.sh'] = nproc_check_text
    if name not in transforms:
        return task
    copy = Path(scratch) / name / task.name
    # Only files consumed by these checks. Avoid copying potentially large setup
    # archives for each adaptation.
    paths = ['instruction.md', 'task.toml', 'environment/Dockerfile',
             'tests/Dockerfile', 'tests/test.sh', 'solution/solve.sh']
    paths.extend(str(p.relative_to(task)) for p in (task / 'tests').rglob('test_*.py'))
    for relative in paths:
        source = task / relative
        if source.is_file():
            target = copy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            text = source.read_text(errors='replace')
            if name == 'check-nproc.sh' and relative not in ('instruction.md', 'task.toml'):
                if profile == 'training' and relative == 'solution/solve.sh':
                    from data.seta.patch import reviewed_nproc_text
                    dockerfile = task / 'environment/Dockerfile'
                    text = reviewed_nproc_text(text, task.name,
                                               dockerfile.read_bytes() if dockerfile.is_file() else b'')
                text = nproc_check_text(text, dockerfile=relative == 'environment/Dockerfile')
            elif relative not in ('instruction.md', 'task.toml'):
                text = transforms[name](text)
            target.write_text(text)
    if name == 'check-test-file-references.sh':
        evidence = discoverable_references(task)
        if (copy / 'instruction.md').is_file():
            sources = '\n'.join((copy / p).read_text(errors='replace') for p in paths
                                if p not in ('instruction.md', 'task.toml') and (copy / p).is_file())
            evidence.update(numeric_reference_patterns((copy / 'instruction.md').read_text(), sources))
        (copy / 'discovered-references.json').write_text(json.dumps(evidence, indent=2))
        if (copy / 'instruction.md').is_file():
            with (copy / 'instruction.md').open('a') as stream:
                stream.write('\nSupplied entry points reveal these references:\n' + '\n'.join(evidence) + '\n')
    return copy


def named_path_examples(text):
    """Recognize explicit location clauses and reuse unique executable locations."""
    locations = {}
    def remember(path):
        if not any(c in path for c in '$*?[]{}'):
            locations.setdefault(path.rsplit('/', 1)[-1], set()).add(path)
    for match in re.finditer(r'''(?<![\w./])/(?!/)[A-Za-z0-9_.+@/-]+(?=$|[\s`"'<>|;,:!?)])''', text):
        remember(match[0].rstrip('.,:'))

    # Join only this explicit grammatical construction, never nearby paths.
    clause = re.compile(
        r'''\b(?:file|script|executable|utility|tool)\s+(?:named|called)\s+'''
        r'''(?P<quote>[`"']?)(?P<name>[A-Za-z0-9_-][A-Za-z0-9_.+-]*)(?P=quote)'''
        r'''\s+in\s+(?P<dirquote>[`"']?)(?P<directory>/[A-Za-z0-9_.+@/-]+)(?P=dirquote)(?=$|[\s.,:;!?])''', re.I)
    def specified(match):
        directory = match['directory'].rstrip('.,:')
        remember(directory.rstrip('/') + '/' + match['name'])
        start, end = match.span('name')
        start, end = start - match.start(), end - match.start()
        return match[0][:start] + '[explicitly located filename]' + match[0][end:]
    text = clause.sub(specified, text)

    def invocation(match):
        value = match[1]
        # Mask only ./NAME at command position. Arguments remain checked.
        token = re.match(r'\./(?P<name>[A-Za-z0-9_-][A-Za-z0-9_.+-]*)(?=\s|$)', value)
        if token and len(locations.get(token['name'], set())) == 1:
            return '`[explicitly located executable]' + value[token.end():] + '`'
        return match[0]
    return re.sub(r'`([^`\n]+)`', invocation, text)


def explicit_path_examples(text):
    """Mask locally specified command/link examples, without inferring global cwd."""
    def literal(value):
        return bool(value) and not any(c in value for c in '$`*?[]{}<>\n')

    def inline(match):
        value = match.group(1)
        try:
            tokens = list(shlex.shlex(value, posix=True, punctuation_chars=';&|<>'))
        except ValueError:
            return match.group(0)
        if re.fullmatch(r'cd\s+(?:--\s+)?\.\.(?:/\.\.)*/?', value.strip()):
            return '[parent navigation]'
        # Only a single command after &&. Extra shell operators, dynamic
        # expressions and another directory change remain review findings.
        args = tokens[1:] if tokens[:1] == ['cd'] else []
        if args[:1] == ['--']:
            args = args[1:]
        if (len(args) >= 3 and args[0].startswith('/') and args[1] == '&&'
                and args[2] not in ('cd', 'pushd', 'popd', 'eval')
                and '-c' not in args[2:]
                and all(literal(t) and not any(c in t for c in ';&|()') for t in [args[0], *args[2:]])):
            return '[command with explicit directory]'
        # ln -s TARGET /absolute/LINK defines the relative target's base.
        if (len(tokens) == 4 and tokens[:2] == ['ln', '-s']
                and tokens[2].startswith(('./', '../')) and tokens[3].startswith('/')
                and all(literal(t) for t in tokens[2:])):
            return '[symlink with explicit location]'
        return match.group(0)

    # Two adjacent command lines can establish the same local base as &&.
    # Do not carry that directory across paragraphs or unrelated prose.
    def adjacent(match):
        commands = [line.strip().strip('`') for line in match[0].splitlines()]
        combined = '`' + commands[0] + ' && ' + commands[1] + '`'
        result = inline(re.fullmatch(r'`([^`\n]+)`', combined))
        return result if result != combined else match[0]
    text = re.sub(r'(?m)^ {0,3}`?cd [^\n`]+`?\n {0,3}(?:`[^`\n]+`|[^`\n]+)$', adjacent, text)
    text = re.sub(r'`([^`\n]+)`', inline, text)
    absolute = r'''(?:`/[^`\n]+`|"/[^"\n]+"|'/[^'\n]+'|/[^\s`"'<>|;]+)'''
    relative = r'''(?:`\.{1,2}/[^`\n]+`|"\.{1,2}/[^"\n]+"|'\.{1,2}/[^'\n]+'|\.{1,2}/[^\s`"'<>|;]+)'''
    mapping = re.compile(r'(?P<link>' + absolute + r')\s*->\s*(?P<target>' + relative + r')')
    paragraphs = re.split(r'(\n\s*\n)', text)
    for i, paragraph in enumerate(paragraphs):
        if not re.search(r'\b(?:symlink|symbolic\s+link)s?\b', paragraph, re.I):
            continue
        established = set()
        def link(match):
            location = match['link'].strip('`"\'')
            target = match['target'].strip('`"\'')
            if literal(location) and literal(target):
                established.update((target, target.rsplit('/', 1)[0] + '/'))
                return match['link'] + ' -> [relative symlink target]'
            return match.group(0)
        paragraph = mapping.sub(link, paragraph)
        # The same paragraph may reiterate the literal target or its directory.
        # Only these exact inline strings inherit the established link location.
        paragraphs[i] = re.sub(r'`([^`\n]+)`',
                               lambda m: '[specified symlink reference]' if m[1] in established else m[0],
                               paragraph)
    return ''.join(paragraphs)


def path_check_text(instruction):
    """Adapt known lexical false positives without changing pinned upstream code."""
    text = without_fenced_blocks(without_source_code(instruction))
    text = without_urls(text)
    text = named_path_examples(text)
    text = explicit_path_examples(text)
    # HOME is case-sensitive in shells. Unknown variables remain unresolved.
    text = re.sub(r'''(?<![\w$])(?:\$HOME|\$\{HOME\}|~)/[^\s`"'<>|;]+''',
                  '[home-anchored path]', text)
    # A relative command with an explicit working directory on the same line
    # is unambiguous. Do not infer a global cwd from unrelated absolute paths.
    text = re.sub(r'`(\./[^`\n]+)`(?= from `/[^`\n]+`)', '[rooted command]', text)
    # Quoted absolute paths can contain spaces; consume before bare tokens.
    text = re.sub(r'`/[^`\n]+`', '`[absolute path]`', text)
    # Consume the WHOLE absolute token, including @ and hyphens in systemd units.
    # Replacing tokens individually also prevents upstream's global substring
    # filter from excusing a relative path merely because an absolute one exists.
    text = re.sub(r'''(?<![\w./])/(?!/)[^\s`"'<>|;]+''', '[absolute path]', text)
    lines = []
    method = r'console\.(?:log|warn|error|info|debug|trace|table)'
    for line in text.splitlines(keepends=True):
        code_context = re.search(r'\b(?:JavaScript|regex|calls?|references?|strings?|literals?|statements?)\b', line, re.I)
        def inline(match):
            value = match.group(1)
            # A sed substitution is an expression, not a directory reference.
            if re.fullmatch(r's/[^\n]*/[^\n]*/[gip0-9]*', value):
                return '`[sed expression]`'
            if re.search(method + r'\s*\(', value) or (code_context and re.fullmatch(method, value)):
                return '`' + re.sub(method, '[JavaScript method]', value) + '`'
            return match.group(0)
        line = re.sub(r'`([^`\n]+)`', inline, line)
        if code_context:
            line = re.sub(r'(["\'])(' + method + r')\1', '[JavaScript method]', line)
        lines.append(line)
    return ''.join(lines)


def path_check_copy(task, scratch):
    copy = Path(scratch) / 'path-check' / task.name
    (copy / 'environment').mkdir(parents=True, exist_ok=True)
    (copy / 'instruction.md').write_text(path_check_text((task / 'instruction.md').read_text(errors='replace')))
    for name in ('task.toml', 'environment/Dockerfile'):
        if (task / name).is_file():
            shutil.copyfile(task / name, copy / name)
    return copy


def rebuild_summary(out):
    """Reconstruct completed outcomes; ignore only an unfinished final journal line."""
    out = Path(out)
    summary = out / 'summary.json'
    report = json.loads(summary.read_text())
    expected = [Path(path).name for path in report['selected_tasks']]
    expected_names = set(expected)
    entries = {}
    incomplete_tail = False
    with (out / 'outcomes.jsonl').open() as stream:
        for line in stream:
            if not line.endswith('\n'):
                incomplete_tail = True
                break
            entry = json.loads(line)
            name = entry['task']
            if name not in expected_names or name in entries:
                raise ValueError(f'unknown or duplicate journal task: {name}')
            entries[name] = entry
    report['tasks'] = [entries[name] for name in expected if name in entries]
    report['complete'] = len(entries) == len(expected) and not incomplete_tail
    report['passed'] = report['complete'] and all(t['status'] == 'passed' for t in report['tasks'])
    report['incomplete_journal_tail'] = incomplete_tail
    pending = summary.with_suffix('.json.tmp')
    pending.write_text(json.dumps(report, indent=2) + '\n')
    pending.replace(summary)
    return report


def run_checks(tasks, out, profile='training', timeout=300, upstream=None, exclude=(), concurrency=1,
               resume=None, resume_record=None, accept_previous_path_check=False):
    if concurrency < 1:
        raise ValueError('concurrency must be positive')
    if sys.version_info < (3, 11):
        raise ValueError('Terminal-Bench checks require Python 3.11+; activate your prep environment')
    manifest, checks, excluded = load_checks(profile, upstream, exclude)
    if not checks:
        raise ValueError('no static checks selected')
    for command in ('bash', 'grep', 'sed', 'awk', 'find', 'sort', 'uniq', 'mktemp', 'dirname', 'basename', 'cat', 'tr', 'rm'):
        if not shutil.which(command):
            raise ValueError(f'missing required executable: {command}')
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'outcomes.jsonl').exists():
        raise ValueError('output already has task outcomes; use a new output directory or --rebuild-summary')
    report = {'upstream': manifest['repository'], 'commit': manifest['commit'],
              'concurrency': concurrency, 'profile': profile, 'selected_tasks': [str(t) for t in tasks],
              'checks': checks, 'excluded_checks': excluded, 'tasks': [],
              'adaptations': {name: text for name, text in ADAPTATIONS.items() if name in checks or name == 'file names'},
              'checker_unit_tests_not_run': list(CHECKER_UNIT_TESTS),
              'conditional_checks': {AI_CHECK: 'runs only when GPTZERO_API_KEY is configured; missing key is a non-failing skip'},
              'complete': False, 'passed': False}
    imported = {}
    if resume:
        from validation.checkpoints.static_resume import load
        imported, report['resumed_from'] = load(resume, tasks, manifest, checks, profile, resume_record,
                                               accept_previous_path_check)
        print(f"Static checkpoint: importing {report['resumed_from']['imported_checks']} checks; "
              f"rerunning changed checks {report['resumed_from']['rerun_changed_checks']}", flush=True)
    for name, reason in excluded.items():
        print(f'SKIP {name}: {reason}', flush=True)
    if AI_CHECK in checks and not os.environ.get('GPTZERO_API_KEY', '').strip():
        print('SKIP check_ai_detection.py: no GPTZERO_API_KEY configured (not a failure)', flush=True)
    summary = out / 'summary.json'
    summary.write_text(json.dumps(report, indent=2) + '\n')
    log = out / 'checks.log'
    log_lock = threading.Lock()
    static_root = os.environ.get('ZIH_STATIC_TMPDIR')
    with tempfile.TemporaryDirectory(prefix='tb-checks-', dir=static_root or '/tmp') as scratch, \
            log.open('w') as combined_log, (out / 'outcomes.jsonl').open('x') as journal:
        # Upstream invokes python3. Use this interpreter, not an unrelated
        # system Python that may lack tomllib. No dependency installation needed.
        bindir = Path(scratch) / 'bin'
        bindir.mkdir()
        check_python = os.environ.get('ZIH_STATIC_PYTHON', sys.executable)
        (bindir / 'python3').symlink_to(check_python)
        scripts = (upstream or TERMINAL_BENCH) / 'scripts/checks'
        reference_script = Path(scratch) / 'adapted-scripts/check-test-file-references.sh'
        if reference_script.name in checks:
            reference_script.parent.mkdir()
            reference_script.write_text(reference_check_script((scripts / reference_script.name).read_text()))
        if static_root:
            local_scripts = Path(scratch) / 'scripts'
            shutil.copytree(scripts, local_scripts)
            scripts = local_scripts
        env = {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ.get('PATH', '')}
        for key in ('FIX_DIRS', 'BASE_DIR', 'BASH_ENV', 'ENV'):
            env.pop(key, None)
        def check_task_body(task, scratch):
            entry = {'task': task.name, 'path': str(task), 'checks': []}
            try:
                validate_input(task)
            except (ValueError, OSError) as exc:
                entry.update(status='error', error=str(exc))
            else:
                pending_checks = set(checks) - set(imported.get(task.name, {}))
                if not pending_checks or (pending_checks <= {AI_CHECK} and not os.environ.get('GPTZERO_API_KEY', '').strip()):
                    # Complete checkpointed tasks need no staging copy or subprocesses.
                    # The optional AI check is still handled below using current credentials.
                    renamed = []
                else:
                    if static_root:
                        staged = Path(scratch) / 'input' / task.name
                        shutil.copytree(task, staged)
                        task = staged
                    task, renamed = safe_copy(task, scratch)
                if renamed:
                    entry['renamed_for_checks'] = renamed
                for name in checks:
                    if name in imported.get(task.name, {}):
                        entry['checks'].append(imported[task.name][name])
                        continue
                    if name == AI_CHECK and not os.environ.get('GPTZERO_API_KEY', '').strip():
                        entry['checks'].append({'check': name, 'status': 'skipped', 'optional': True,
                            'reason': 'no GPTZERO_API_KEY configured', 'exit_code': None, 'log': None})
                        continue
                    started = time.monotonic()
                    target = path_check_copy(task, scratch) if name == PATH_CHECK else adapted_check_copy(task, scratch, name, profile)
                    if name == 'check-test-file-references.sh':
                        evidence = json.loads((target / 'discovered-references.json').read_text())
                        if evidence:
                            entry['discoverable_reference_evidence'] = evidence
                    # Buffer each check separately so parallel outputs never interleave.
                    with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace', dir=scratch) as stream:
                        # The scripts take under a second; a timeout means the node or the
                        # file system stalled (job 916252), so one more attempt is made.
                        for attempt in (1, 2):
                            try:
                                script = reference_script if name == reference_script.name else scripts / name
                                result = subprocess.run([check_python if name.endswith('.py') else 'bash', str(script), str(target)],
                                    cwd=scratch, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
                                status = 'passed' if result.returncode == 0 else 'failed'
                                rc = result.returncode
                                break
                            except subprocess.TimeoutExpired:
                                status, rc = 'error', None
                                stream.write(f'\nTimed out after {timeout} seconds (attempt {attempt})\n')
                                stream.flush()
                        stream.seek(0)
                        if name == PATH_CHECK and rc == 1:
                            if re.search(r'relative paths used \(should be absolute under /[^)]*\): ', stream.read()):
                                status = 'warning'
                            stream.seek(0)
                        if name == AI_CHECK and rc == 0:
                            text = stream.read()
                            if 'skipped' in text.lower() or 'No files to check' in text:
                                status = 'skipped'
                            stream.seek(0)
                        with log_lock:
                            combined_log.write(f'\n=== {entry["task"]} / {name} ===\n')
                            shutil.copyfileobj(stream, combined_log)
                            combined_log.write(f'\n=== result: {status}, exit_code: {rc} ===\n')
                            combined_log.flush()
                    entry['checks'].append({'check': name, 'status': status, 'optional': name == AI_CHECK, 'exit_code': rc,
                                            'log': str(log.resolve()), 'duration_seconds': round(time.monotonic() - started, 3)})
                    if status != 'passed':
                        print(f'{task.name}: {name}: {status} (see {log})', flush=True)
                entry['warnings'] = [{'tag': 'relative-instruction-path', 'check': c['check'], 'log': c.get('log')} for c in entry['checks'] if c['status'] == 'warning']
                entry['status'] = 'passed' if all(c['status'] in ('passed', 'warning') or (c['status'] == 'skipped' and c.get('optional')) for c in entry['checks']) else 'failed'
            return entry
        def check_task(task):
            if not static_root:
                return check_task_body(task, scratch)
            # Bound live task copies in RAM; oversized tasks use durable scratch.
            # Allow for staging plus renamed/path-check copies per concurrent task.
            parent = scratch
            try:
                validate_input(task)
                size = sum(p.stat().st_size for p in task.rglob('*') if p.is_file())
                limit = int(os.environ.get('ZIH_STATIC_MAX_BYTES', '1073741824')) // (3 * concurrency)
                if size > limit:
                    parent = os.environ['TMPDIR']
            except (ValueError, OSError) as exc:
                return {'task': task.name, 'path': str(task), 'checks': [], 'status': 'error', 'error': str(exc)}
            # Delete only this task's temporary copies immediately after its checks.
            with tempfile.TemporaryDirectory(prefix='static-task-', dir=parent) as task_scratch:
                return check_task_body(task, task_scratch)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(check_task, task) for task in tasks]
            for future in as_completed(futures):
                entry = future.result()
                journal.write(json.dumps(entry) + '\n')
                journal.flush()
                print(f"{entry['task']}: {entry['status']}", flush=True)
    report = rebuild_summary(out)
    print(f'{len(checks)} upstream checks per task; {len(excluded)} explicitly excluded. Report: {summary}')
    return 0 if report['passed'] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root', nargs='?', type=Path)
    ap.add_argument('--profile', choices=('training', 'portable', 'terminal-bench'), default='training')
    ap.add_argument('--out', type=Path, default=Path('verify-out/terminal-bench'))
    ap.add_argument('--limit', type=int)
    ap.add_argument('--concurrency', type=int, default=1, help='parallel static tasks')
    ap.add_argument('--timeout', type=int, default=120, help='seconds per static script per task')
    ap.add_argument('--list', action='store_true', help='list included and excluded checks')
    ap.add_argument('--exclude', action='append', default=[], help='check ID or filename, optionally NAME=reason; repeat or comma-separate')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--rebuild-summary', action='store_true', help='reconstruct --out/summary.json from saved task outcomes without rerunning checks')
    a = ap.parse_args()
    try:
        if a.rebuild_summary:
            report = rebuild_summary(a.out)
            print(f"Recovered {len(report['tasks'])} task outcomes; complete={report['complete']}")
            return 0 if report['passed'] else 1
        if a.timeout <= 0 or (a.limit is not None and a.limit <= 0):
            raise ValueError('--limit and --timeout must be positive')
        manifest, checks, excluded = load_checks(a.profile, exclude=a.exclude)
        if a.list or a.dry_run:
            print(f"Terminal-Bench {manifest['commit']} ({a.profile})")
            for name in checks:
                print(('SKIP ' + name + ': no GPTZERO_API_KEY configured (not a failure)')
                      if name == AI_CHECK and not os.environ.get('GPTZERO_API_KEY', '').strip()
                      else f"{'CONDITIONAL' if name == AI_CHECK else 'RUN '} {name}")
            for name, reason in excluded.items():
                print(f'SKIP {name}: {reason}')
        if a.list:
            return 0
        if a.root is None:
            raise ValueError('provide a task or task directory')
        tasks = discover_tasks(a.root)[:a.limit]
        if a.dry_run:
            for task in tasks:
                print(f'Would check {task}')
            return 0
        return run_checks(tasks, a.out, a.profile, a.timeout, exclude=a.exclude, concurrency=a.concurrency)
    except (ValueError, OSError) as exc:
        ap.exit(1, f'{exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
