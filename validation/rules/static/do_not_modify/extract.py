"""do_not_modify step 1: extract the files an instruction says must not be modified.

Only an explicitly governed filename/location is eligible for enforcement.
Unresolved clauses are diagnostics, never verifier failures or passes. No task
code is executed here. File patterns require a separate scoped resolver.
"""
import re
from pathlib import PurePosixPath


def instruction_sentence_spans(text):
    """Conservatively bound matches by punctuation outside quoted literals.

    Keep existing newline boundaries: this change only narrows the search,
    rather than also adding matches across wrapped lines. Dots inside filenames
    or quoted paths do not end a sentence. This is a lexical approximation,
    not a natural-language sentence parser.
    """
    start, quote = 0, None
    for i, char in enumerate(text):
        if char == "\n":
            yield start, i
            start, quote = i + 1, None
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ('`', '"') or (char == "'" and (i == 0 or not text[i-1].isalnum())):
            quote = char
            continue
        if char in '.!?' and (i + 1 == len(text) or text[i+1].isspace()):
            # Common inline abbreviations are not sentence boundaries.
            token = text[start:i+1].split()[-1].lower() if text[start:i+1].split() else ''
            if token in {'e.g.', 'i.e.', 'mr.', 'mrs.', 'dr.', 'vs.'}:
                continue
            yield start, i + 1
            start = i + 1
    if start < len(text):
        yield start, len(text)


def mask_instruction_examples(text):
    """Exclude fenced examples and explicit comments, preserving line numbers.

    Fence matching follows task_absolute_path.check.without_fenced_blocks; masking
    instead of deleting lines preserves evidence positions. Ordinary Markdown
    headings and inline backtick paths remain eligible.
    """
    blank = lambda value: re.sub(r'[^\n]', ' ', value)
    kept, opening = [], None
    for line in text.splitlines(keepends=True):
        match = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line.rstrip('\n'))
        if opening is None:
            if match:
                opening = match[1]
                kept.append(blank(line))
            else:
                kept.append(line)
        else:
            kept.append(blank(line))
            if (match and match[1][0] == opening[0]
                    and len(match[1]) >= len(opening) and not match[2].strip()):
                opening = None
    text = ''.join(kept)
    # Match inline literals first so comment-like text inside paths is retained.
    pattern = r'`[^`\n]*`|"[^"\n]*"|<!--.*?-->|/\*.*?\*/|<pre\b[^>]*>.*?</pre>|(?m:^[ \t]*//[^\n]*)'
    return re.sub(pattern, lambda m: m[0] if m[0].startswith(('`', '"')) else blank(m[0]), text, flags=re.S | re.I)


def instruction_prose_only(text):
    """Mask literal values before interpreting conditional English wording."""
    text = re.sub(r"`[^`\n]*`|\"[^\"\n]*\"|'[^'\n]*'", lambda m: ' ' * len(m[0]), text)
    return re.sub(r"(?<![\w])(?:/|\./|\.\./)[^\s`\"'<>;,()]+", lambda m: ' ' * len(m[0]), text)



PREFIX = re.compile(
    r"\b(?:do not modify|must not modify|leave unchanged|don't change|must not edit|"
    r"leave intact|do not touch|without modifying|do not alter|do not remove|do not edit)\b".replace(' ', r'\s+'), re.I)
POSTFIX = re.compile(r"\b(?:must\s+remain\s+(?:unchanged|intact)|must\s+not\s+be\s+(?:modified|edited|changed)|(?:is|are)\s+read-only)\b", re.I)
READ_ONLY = re.compile(r'\btreat\s+(?P<object>[^;\n]+?)\s+as\s+read-only\b', re.I)
TOKEN = re.compile(r'`[^`\n]+`|"[^"\n]+"|[^\s`,;():—–]+|[,;():—–]')
ARTICLES = {'the', 'any', 'all', 'an', 'a', 'original', 'existing'}
MODIFIERS = {'source', 'input', 'test', 'data', 'raw', 'configuration', 'config',
             'shared', 'actual', 'application', 'service', 'python', 'c', 'c++',
             'go', 'rust', 'html', 'cli', 'system-wide', 'main', 'dataset', 'backend',
             'generated', 'artifact'}
OBJECTS = {'file', 'files', 'directory', 'directories', 'folder', 'folders',
           'script', 'scripts', 'binary', 'binaries', 'library', 'libraries',
           'notebook', 'code', 'data', 'database', 'contents', 'installations',
           'runner', 'tests', 'anything'}
LOCATIONS = {'in', 'under', 'inside', 'within', 'at', 'of'}
ACTIONS = {'modify', 'edit', 'change', 'alter', 'remove', 'delete', 'move',
           'overwrite', 'recompile', 'increment', 'modifying', 'recompiling', 'regenerate'}
REGIONS = {'function', 'method', 'class', 'flags', 'signature', 'signatures',
           'behavior', 'behaviour', 'logic', 'format', 'formats', 'lines'}
LANGUAGE_GLOBS = {'python': '*.py', 'c': '*.c', 'c++': '*.cpp', 'go': '*.go',
                  'rust': '*.rs', 'html': '*.html'}


def tokens(text):
    return [(m[0], m.start(), m.end()) for m in TOKEN.finditer(text)]


def value(token):
    quoted = token.startswith(('`', '"')) and token[-1:] == token[:1]
    return token[1:-1] if quoted else token.rstrip('.!?'), quoted


def path_kind(token, explicit_object=False):
    v, quoted = value(token)
    if not v or re.search(r'[\x00\n<>|=]', v) or '://' in v:
        return None
    if re.search(r'[*?\[\]{}]', v) or re.fullmatch(r'\.[A-Za-z][A-Za-z0-9]*', v):
        return 'pattern'
    if v.startswith(('/', './', '../')) and v not in {'/', '.', '..'}:
        return 'path'
    if re.fullmatch(r'[A-Za-z0-9_+@.-]+(?:/[A-Za-z0-9_+@.-]+)+/?', v):
        return 'path'
    if re.fullmatch(r'[A-Za-z0-9_+@-][A-Za-z0-9_+@.-]*\.[A-Za-z][A-Za-z0-9_-]*', v):
        return 'path'
    if v in {'Makefile', 'Dockerfile', 'README', 'LICENSE', 'CMakeLists.txt'}:
        return 'path'
    if quoted and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]+', v) and (explicit_object or not v.isupper()):
        return 'path'
    return None


def noun_phrase(words):
    """A finite vocabulary, not a guess that any English word is a noun."""
    low = [w.lower() for w in words]
    return bool(low) and any(w in OBJECTS for w in low) and all(
        w in ARTICLES | MODIFIERS | OBJECTS for w in low)


def parse_group(ts, start=0):
    """Consume an adjacent PATH or a whitelisted object/location expression."""
    i = start
    while i < len(ts) and ts[i][0].lower() in ARTICLES:
        i += 1
    pre = []
    while i < len(ts) and ts[i][0].lower() in MODIFIERS | OBJECTS:
        pre.append(ts[i][0].lower()); i += 1
    # 'the data in PATH', 'source code of PATH', 'test files (PATH)'.
    parenthesis = False
    if i < len(ts) and ts[i][0].lower() in LOCATIONS:
        if not noun_phrase(pre):
            return [], start, 'unsupported_target_phrase'
        i += 1
    elif i < len(ts) and ts[i][0] == '(' and noun_phrase(pre):
        parenthesis = True; i += 1
        if [t[0].lower() for t in ts[i:i+3]] == ['no', 'changes', 'to']:
            i += 3
    if i >= len(ts):
        return [], start, 'no_concrete_filename_found'
    # A bare stem is accepted only with an explicit file/binary noun, before or after.
    following = value(ts[i+1][0])[0].lower() if i+1 < len(ts) else ''
    kind = path_kind(ts[i][0], noun_phrase(pre) or following in OBJECTS)
    if not kind:
        if any(value(t[0])[0].lower() in REGIONS for t in ts[start:i+5]):
            return [], start, 'source_region_not_whole_artifact'
        return [], start, 'no_concrete_filename_found'
    if following in REGIONS:
        return [], start, 'source_region_not_whole_artifact'
    if kind == 'pattern':
        return [], start, 'file_pattern_requires_scoped_resolution'
    if pre and not noun_phrase(pre) and not noun_phrase(pre+[following]):
        return [], start, 'unsupported_target_phrase'
    p, _ = value(ts[i][0]); end = i+1
    scope = 'files_under' if any(w in pre for w in ('files', 'directories', 'folders', 'installations')) else 'artifact'
    filters = [LANGUAGE_GLOBS[w] for w in pre if w in LANGUAGE_GLOBS]
    match = dict(path=p, scope=scope, file_patterns=filters,
                 pattern='object_location' if pre else 'direct_path')
    if end < len(ts) and value(ts[end][0])[0].lower() in OBJECTS:
        end += 1
    if parenthesis and end < len(ts) and ts[end][0] == ')':
        end += 1
    return [match], end, None


def parse_list(text):
    ts = tokens(text); found = []; pos = 0; reason = None
    # Coordinated prohibited verbs: 'do not modify or overwrite PATH'.
    verbs = []
    while pos < len(ts) and ts[pos][0].lower() in {'or', 'and', ','}:
        next_pos = pos+1
        if next_pos < len(ts) and ts[next_pos][0].lower() in {'or', 'and'}:
            next_pos += 1
        if next_pos >= len(ts) or ts[next_pos][0].lower() not in ACTIONS:
            break
        verbs.append(ts[next_pos][0].lower()); pos = next_pos+1
    while pos < len(ts):
        group, end, reason = parse_group(ts, pos)
        if not group:
            # An unnamed object may precede a separately named one: 'the tests
            # or main.rs'. Only cross a connector after a recognized object
            # phrase, never arbitrary prose or an allowed-work clause.
            j = pos
            while j < len(ts) and ts[j][0].lower() in ARTICLES | MODIFIERS | OBJECTS | LOCATIONS | {'structure'}:
                j += 1
            if (j > pos and any(t[0].lower() in OBJECTS for t in ts[pos:j])
                    and j < len(ts) and ts[j][0].lower() in {'or', 'and'}):
                pos = j+1; continue
            break
        found.extend(group); pos = end
        if pos < len(ts) and ts[pos][0] == ',':
            pos += 1
            if pos < len(ts) and ts[pos][0].lower() in {'or', 'and'}:
                pos += 1
        elif pos < len(ts) and ts[pos][0].lower() in {'or', 'and'}:
            pos += 1
        else:
            break
    return found, reason, verbs


def previous_target(text):
    """PATH immediately before a postfix, allowing only a file noun after it."""
    ts = tokens(text.rstrip()); end = len(ts)
    while end and ts[end-1][0].lower() in OBJECTS:
        end -= 1
    if end and path_kind(ts[end-1][0], end < len(ts)) == 'path':
        p, _ = value(ts[end-1][0])
        return [dict(path=p, scope='artifact', file_patterns=[], pattern='target_before_keyword')]
    return []


def caption_target(text):
    """A file followed by a descriptive dash and a parenthetical prohibition."""
    parts = re.split(r'\s+[—–]\s+', text, maxsplit=1)
    if len(parts) != 2:
        return []
    found = previous_target(parts[0])
    # No second concrete path in the caption, or ambiguous clause boundary.
    if not found or ';' in parts[1] or any(path_kind(t[0]) for t in tokens(parts[1])):
        return []
    return found


def extract(instruction, *, prefix_pattern=PREFIX, postfix_pattern=POSTFIX,
            readonly_pattern=READ_ONLY, apply_exceptions=True):
    masked = mask_instruction_examples(instruction)
    # Keep line offsets; remove only emphasis markup, not wildcard/path content.
    masked = re.sub(r'`[^`\n]*`|"[^"\n]*"|\*\*',
                    lambda m: m[0] if m[0].startswith(('`', '"')) else ' ' * len(m[0]), masked)
    matches, unresolved = [], []
    for start, end in instruction_sentence_spans(masked):
        sentence = masked[start:end]
        clauses = list(prefix_pattern.finditer(sentence)) + list(postfix_pattern.finditer(sentence))
        for keyword in sorted(clauses, key=lambda m: m.start()):
            before, after = sentence[:keyword.start()], sentence[keyword.end():]
            prose = instruction_prose_only(sentence)
            reason = None; found = []; verbs = []
            if apply_exceptions and re.search(r'\b(?:unless|git)\b', prose, re.I):
                reason = 'excluded_sentence'
            elif apply_exceptions and re.search(r'\b(?:except|other than|outside)\b', instruction_prose_only(after), re.I):
                # Keep an independent first target before an exceptional second
                # object group; don't claim that an excepted directory is intact.
                local = instruction_prose_only(after)
                exception = re.search(r'\b(?:except|other than|outside)\b', local, re.I)
                separate = re.search(r'\bor\s+(?:the\s+)?(?:source\s+files|any\s+files|files)\b', local[:exception.start()], re.I)
                if separate:
                    found, reason, verbs = parse_list(after[:separate.start()])
                reason = 'exception_scope_unresolved'
            elif postfix_pattern.fullmatch(keyword[0]):
                found = previous_target(before)
                reason = 'no_concrete_filename_found'
            else:
                found, reason, verbs = parse_list(after)
                # '/file (do not modify)' and 'Input file (do not modify): /file'.
                if not found and re.match(r'\s*(?:(?:this|the)\s+file\s*)?(?:\)|;)', after, re.I):
                    prior = before.rstrip()
                    if prior.endswith('('):
                        found = previous_target(prior[:-1])
                        if not found:
                            found = caption_target(prior[:-1])
                    tail = re.sub(r'^\s*(?:(?:this|the)\s+file\s*)?\)\s*:\s*', '', after, flags=re.I)
                    if not found and tail != after:
                        found, reason, verbs = parse_list(tail)
                    for f in found:
                        f['pattern'] = 'parenthetical_prohibition'
            line = instruction.count('\n', 0, start+keyword.start())+1
            evidence = dict(keyword=keyword[0], line=line,
                            instruction_line=instruction.splitlines()[line-1])
            for f in found:
                f.update(evidence)
                f['protection'] = 'existence' if ' '.join(keyword[0].lower().split()) == 'do not remove' and not any(v not in {'delete', 'remove'} for v in verbs) else 'content'
                matches.append(f)
            if not found:
                unresolved.append(dict(**evidence, reason=reason or 'no_concrete_filename_found',
                                       enforcement_status='skipped_unresolved_target'))
        for readonly in readonly_pattern.finditer(sentence):
            if apply_exceptions and re.search(r'\b(?:unless|git)\b', instruction_prose_only(sentence), re.I):
                continue
            found, reason, _ = parse_list(readonly['object'])
            line = instruction.count('\n', 0, start+readonly.start())+1
            evidence = dict(keyword=readonly[0], line=line,
                            instruction_line=instruction.splitlines()[line-1])
            for f in found:
                f.update(evidence, protection='content', pattern='treat_as_read_only')
                matches.append(f)
            if not found:
                unresolved.append(dict(**evidence, reason=reason or 'no_concrete_filename_found',
                                       enforcement_status='skipped_unresolved_target'))
    return dict(matches=matches, unresolved=unresolved,
                enforcement_eligible=bool(matches),
                enforcement_status='requires_original_environment_evidence' if matches else
                                   'skipped_unresolved_target' if unresolved else 'not_applicable',
                status='targets_found' if matches else 'unresolved_target' if unresolved else 'not_applicable')


def protected_artifact_matches(instruction):
    return extract(instruction)['matches']


def covers(match, actual_path, resolved_path=None):
    """Match a reviewed concrete target; does not resolve against the host FS."""
    base = (resolved_path or match['path']).rstrip('/')
    actual = actual_path.rstrip('/')
    if actual != base and not actual.startswith(base+'/'):
        return False
    patterns = match.get('file_patterns', [])
    return not patterns or any(PurePosixPath(actual).match(p) for p in patterns)
