"""Our patch to Terminal-Bench check-task-absolute-path.sh: mask text that is not a relative file
reference before the pinned upstream script reads the instruction. The repair is repair.py."""
import re
import shlex
import shutil
from pathlib import Path


PATH_CHECK = 'check-task-absolute-path.sh'


SHELL_FENCES = {'', 'bash', 'sh', 'shell', 'zsh', 'console', 'text', 'txt'}


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


def path_check_script(source):
    """Keep upstream's unquoted-filename pattern on a temporary copy of pinned upstream.

    Upstream appends those names to relative_files inside `echo ... | while read`, which
    bash runs in a subshell, so the additions were lost and the pattern never reported."""
    old = 'echo "$pattern2_files" | while IFS= read -r file; do'
    end = '$file"\n        fi\n    done\n'
    if source.count(old) != 1 or source.count(end) != 1:
        raise ValueError('upstream relative-path extractor changed')
    source = source.replace(old, 'while IFS= read -r file; do')
    return source.replace(end, '$file"\n        fi\n    done <<< "$pattern2_files"\n')


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
            # A JSON path ("$.paths./items.get") or a Markdown heading example
            # ("## src/auth.py") names a location in a document, not a file to use.
            if re.fullmatch(r'''["']?\$[.\[][^\n]*''', value):
                return '`[JSON path]`'
            if re.fullmatch(r'#{1,6} [^\n]+', value):
                return '`[Markdown heading]`'
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


CHECK = PATH_CHECK
ADAPTATION = (
    'Relative-path findings are advisory warnings and do not fail stage 4. Runs on a copy of the task '
    'whose instruction.md has its source-code blocks removed (fenced blocks tagged with a programming '
    'language); masks complete absolute paths, inline JSON paths and inline Markdown heading examples '
    'and JavaScript console calls/sed substitutions so their substrings are not mistaken for paths; '
    'masks complete non-file URLs, quoted absolute paths with spaces, simple inline commands rooted by '
    'cd /absolute/path &&, and literal relative symlink targets with explicit absolute link locations; '
    'skips all Markdown fenced blocks, recognizes $HOME/${HOME}/~/ paths, and accepts adjacent literal '
    'cd and command lines; accepts explicit named-file-in-directory clauses, pure parent-navigation '
    'commands, and relative command tokens for uniquely absolute-named executables; unresolved relative '
    'file references outside fenced blocks are otherwise kept; unquoted relative file names are reported '
    'too (upstream collected them in a subshell and lost them)')
