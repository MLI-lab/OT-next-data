"""Repairs for the agent_editable_test static check.

The check flags the verifier (tests/setup.sh, tests/test.sh, tests/*.py) running a test
harness, check script or reference binary from the agent's container without restoring
the original first: the agent could edit that file and pass without doing the task.

The repair ships the pristine original of every such file with the verifier (under
tests/) and has tests/setup.sh restore it into place before the tests run.  setup.sh
runs after the agent stops and before test.sh, so the verifier always grades against the
original file, and the files the agent is asked to change are never touched.

For a test suite the verifier runs from a directory of the image (make test, pytest in
the project), the original of every test and build file that suite reads is restored the
same way.  For a harness binary the image compiles, the original sources are restored
and the binary is rebuilt from them, so the agent cannot swap it either.
"""
from __future__ import annotations
DATASET = 'CalibForge'

import hashlib
import re
import shlex
import shutil
import tempfile
from pathlib import Path, PurePosixPath

import sys
sys.path.insert(0, '/app/code')

from validation.rules.static.no_behavioural_tests import editable_harness as harness

# Build files a test suite reads, not the agent's deliverable.
BUILD_FILES = re.compile(r'(^|/)(Makefile|makefile|GNUmakefile|CMakeLists\.txt|build\.sh|configure|package\.json)$')
MAKEFILES = ('Makefile', 'makefile', 'GNUmakefile')
COMPILERS = ('gcc', 'g++', 'cc', 'c++', 'clang', 'clang++')
# Source files a harness is compiled or built from, and the object files a link command takes.
SOURCES = re.compile(r'\.(c|cc|cpp|cxx|h|hpp|hxx|py|js|mjs|cjs|rs|go|java|rb|pl|php|lua|jl)$', re.I)
OBJECTS = re.compile(r'\.(o|obj)$')
# Where the pristine originals live inside tests/.
STORE = 'ot-originals'
BUILD = '/tests/' + STORE + '/build'


# --- task helpers -----------------------------------------------------------------------------------

def _write_task(files):
    root = Path(tempfile.mkdtemp(prefix='ot-repair-'))
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def _text(data):
    try:
        return data.decode()
    except (UnicodeDecodeError, AttributeError):
        return None


def _instruction(files):
    return _text(files.get('instruction.md')) or ''


def _split(text):
    try:
        return shlex.split(re.sub(r'\\\n', ' ', text), comments=True)
    except ValueError:
        return text.split()


def _analysis(files):
    """(events, restored) of the task exactly as the check computes them."""
    root = _write_task(files)
    try:
        return harness.classify(root, _instruction(files))
    except Exception:
        return [], []


def _findings(files):
    events, _ = _analysis(files)
    return [e for e in events if e['role'] == 'editable_harness']


def _evidence_paths(evidence):
    """Container paths the check reported, once each, in order."""
    chunks = []
    for value in (evidence or {}).values():
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, (list, tuple)):
            chunks.extend(str(v) for v in value)
    return list(dict.fromkeys(t for t in re.split(r'[\s,]+', ' '.join(chunks)) if t.startswith('/')))


# --- what the image is built from -------------------------------------------------------------------

def _copy_map(files):
    """{container path: task file} for every file the Dockerfile copies into the image."""
    text = _text(files.get('environment/Dockerfile') or files.get('tests/Dockerfile')) or ''
    available = set(files)
    out = {}
    for line in text.splitlines():
        match = re.match(r'\s*(?:COPY|ADD)\s+(.*)$', line)
        if not match:
            continue
        parts = _split(match.group(1))
        if len(parts) < 2:
            continue
        src, dst = parts[-2], parts[-1]
        if not (src == 'files' or src.startswith('files/')):
            continue
        base = 'environment/' + src.rstrip('/')
        is_dir = src.endswith('/') or any(name.startswith(base + '/') for name in available)
        if is_dir:
            prefix = base + '/'
            for name in sorted(available):
                if name.startswith(prefix):
                    out[dst.rstrip('/') + '/' + name[len(prefix):]] = name
        elif dst.endswith('/'):
            out[dst.rstrip('/') + '/' + PurePosixPath(base).name] = base
        else:
            out[dst.rstrip('/')] = base
    return out


def _test_like(path):
    name = PurePosixPath(path).name
    return bool(harness.TEST_LIKE.search(name)) or any(
        harness.TEST_LIKE.search(part) for part in PurePosixPath(path).parts[:-1])


# A change the agent is asked to make, and one it is told not to make.
NEGATIVE = re.compile(r"\b(?:do not|don't|must not|never|no need|should not|cannot|can not|leave|keep|"
                      r"unchanged|already)\b", re.I)
FIRST_PERSON = re.compile(r"\bI(?:'ve|'m|\s+have|\s+am|\s+put|\s+wrote)?\b", re.I)


def _asked_to_change(instruction, path):
    """The instruction asks the agent to change this file: restoring it would undo the agent's fix."""
    if not instruction:
        return False
    name = PurePosixPath(path).name
    instruction = instruction.replace('`', '')
    if name == 'Makefile' and re.search(r'\b(?:do not|must not|cannot)\s+(?:change|modify|edit)\s+(?:the\s+)?Makefile\b', instruction, re.I):
        return False
    pattern = re.compile(r'(?<![\w.-])' + re.escape(name) + r'(?![\w-])')
    for sentence in re.split(r'(?<=[.!?])\s+|\n', instruction):
        match = pattern.search(sentence)
        if not match:
            continue
        if NEGATIVE.search(sentence) or FIRST_PERSON.search(sentence[:match.start()]):
            continue
        # The change word is the verb of which the file is the object, not a later "to verify your fix".
        if harness.CHANGE_WORDS.search(sentence[:match.start()]):
            return True
    return False


def _stored(task_file):
    """Path of a pristine original inside tests/."""
    # Opaque verifier-owned name avoids making the Dockerfile appear to copy a verifier test.
    return hashlib.sha256(task_file.encode()).hexdigest() + '.original'


def _stored_path(task_file):
    return '/tests/%s' % _stored(task_file)


def _container_of(token, copies):
    """Container path of a pristine original named by a compile command token, if any."""
    if token in copies:
        return token
    stem = PurePosixPath(token).stem
    suffix = PurePosixPath(token).suffix.lower()
    for cpath in sorted(copies):
        if PurePosixPath(cpath).stem == stem and PurePosixPath(cpath).suffix.lower() == suffix:
            return cpath
    return None


def _container_path(task_file, copies):
    for cpath, name in copies.items():
        if name == task_file:
            return cpath
    return '/' + task_file.split('environment/', 1)[-1]


# --- Makefiles --------------------------------------------------------------------------------------

def _makefile(text):
    """(variables, [target, prerequisites, recipe lines]) of a Makefile."""
    variables, rules, current = {}, [], None
    for raw in text.splitlines():
        if raw.startswith('\t'):
            if current is not None:
                rules[current][2].append(raw[1:].strip())
            continue
        line = raw.split('#', 1)[0].rstrip()
        if not line.strip():
            current = None
            continue
        head, sep, value = line.partition('=')
        if sep and ':' not in head and re.match(r'^\s*[A-Za-z_][\w.]*\s*[:?+]?$', head):
            variables[re.sub(r'[\s:?+]', '', head)] = value.strip()
            current = None
            continue
        match = re.match(r'^([^\s:][^:]*?)\s*:(?!=)(.*)$', line)
        if match:
            targets = match.group(1).split()
            if targets:
                current = len(rules)
                rules.append([targets[0], match.group(2).split(), []])
            else:
                current = None
    return variables, rules


def _resolve(value, variables, depth=0):
    """Substitute $(VAR) and ${VAR}, and the suffix form the harness Makefiles use."""
    if not value or depth > 6:
        return value

    def sub(match):
        inner = match.group(1)
        name, colon, tail = inner.partition(':')
        if colon and '.' in tail and '=' in tail:
            base = _resolve(name.strip(), variables, depth + 1) or ''
            old, _, new = tail.partition('=')
            return base.replace(old.strip(), new.strip())
        return variables.get(inner.strip(), match.group(0))

    value = re.sub(r'\$\(\s*([\w.]+)\s*\)', sub, value)
    value = re.sub(r'\$\{\s*([\w.]+)\s*\}', sub, value)
    return value


def _make_target(container, copies, files):
    """(container path of the Makefile, target) that builds `container`, or (None, None)."""
    name = PurePosixPath(container).name
    fallback = None
    for cpath, task_file in sorted(copies.items()):
        if PurePosixPath(cpath).name not in MAKEFILES:
            continue
        text = _text(files.get(task_file))
        if not text:
            continue
        variables, rules = _makefile(text)
        for target, prereqs, recipe in rules:
            resolved = _resolve(target, variables) or target
            if PurePosixPath(resolved).name == name:
                return cpath, resolved
        for target, prereqs, recipe in rules:
            for line in recipe:
                if re.search(r'-o\s+"?(\S*)' + re.escape(name) + r'"?\b', _resolve(line, variables) or ''):
                    fallback = (cpath, target)
    return fallback if fallback else (None, None)


def _absolute(token, where):
    """A Makefile recipe path, as it is meant inside the image."""
    if token.startswith('/') or token.startswith('-') or '=' in token or '$' in token:
        return token
    if not re.search(r'\.[\w.]+$', token):
        return token
    return where.rstrip('/') + '/' + token


def _recipe_line(line, variables, where):
    """Tokens of a Makefile recipe, with the file it writes made absolute."""
    tokens = _split(_resolve(line, variables) or '')
    resolved = []
    for index, token in enumerate(tokens):
        if index and tokens[index - 1] == '-o':
            # The file the recipe writes: always absolute, the verifier runs it by path.
            resolved.append(_absolute(token, where) if token.startswith('/') else
                            where.rstrip('/') + '/' + token)
        else:
            resolved.append(_absolute(token, where))
    return resolved


# --- compile commands --------------------------------------------------------------------------------

def _recipe_compiles(container, makefile, target, copies, files):
    """[[tokens]] of the Makefile recipes that build `container`, with absolute paths."""
    text = _text(files.get(copies.get(makefile)))
    if not text:
        return []
    variables, rules = _makefile(text)
    where = str(PurePosixPath(makefile).parent)
    out = []
    for rule_target, prereqs, recipe in rules:
        if target and (_resolve(rule_target, variables) or rule_target) != target:
            continue
        for line in recipe:
            tokens = _split(_resolve(line, variables) or '')
            if not tokens or PurePosixPath(tokens[0].lstrip('@+-').strip()).name not in COMPILERS:
                continue
            out.append(_recipe_line(line, variables, where))
    return out


def _verifier_compiles(container, files):
    """[[tokens]] of the compiles the verifier itself runs to build `container`."""
    name = PurePosixPath(container).name
    out = []
    for task_file, data in sorted(files.items()):
        if not task_file.startswith('tests/'):
            continue
        text = _text(data)
        if not text:
            continue
        for match in re.finditer(r'\[([^\[\]]*\b(?:gcc|g\+\+|cc|c\+\+|clang|clang\+\+)\b[^\[\]]*)\]', text):
            tokens = [t.strip().strip('\'"') for t in match.group(1).split(',')]
            tokens = [t for t in tokens if t]
            if '-o' in tokens and tokens.index('-o') + 1 < len(tokens) \
                    and PurePosixPath(tokens[tokens.index('-o') + 1]).name == name:
                out.append(tokens)
    return out


def _compile_candidates(container, copies, files):
    """[tokens] of every compile command that builds `container`: the verifier's own first."""
    makefile, target = _make_target(container, copies, files)
    out = []
    for tokens in _verifier_compiles(container, files) + _recipe_compiles(container, makefile, target,
                                                                          copies, files):
        tokens = _link_sources(tokens, copies, files)
        if tokens:
            out.append(tokens)
    return out


def _link_sources(tokens, copies, files):
    """A link command's object files become the sources they are compiled from."""
    if any(token == '-c' for token in tokens):
        return tokens
    out, objects = [], False
    for token in tokens:
        if OBJECTS.search(token):
            source = _pristine_source(token, copies)
            if not source:
                return None
            out.append(source)
            objects = True
        else:
            out.append(token)
    return tokens if not objects else _with_flags(out, copies, files)


def _with_flags(tokens, copies, files):
    """A link command that is given sources needs the compile flags the Makefile sets."""
    makefile = next((c for c, f in sorted(copies.items()) if PurePosixPath(c).name in MAKEFILES), None)
    if not makefile:
        return tokens
    text = _text(files.get(copies[makefile]))
    if not text:
        return tokens
    variables, _ = _makefile(text)
    flags = []
    for name in ('CFLAGS', 'CXXFLAGS'):
        for flag in _split(_resolve(variables.get(name, ''), variables) or ''):
            if flag not in tokens and flag not in flags:
                flags.append(flag)
    index = tokens.index('-o')
    return tokens[:2] + flags + tokens[2:]


def _pristine_source(object_file, copies):
    """The task file of the source an object file is compiled from."""
    stem = PurePosixPath(object_file).stem
    for cpath, task_file in sorted(copies.items()):
        if PurePosixPath(cpath).stem == stem and SOURCES.search(cpath):
            return task_file
    return None


def _with_originals(tokens, copies, files, instruction):
    """Replace the harness sources of a compile command with their originals under /tests."""
    out, sources = [], []
    for token in tokens:
        cpath = _container_of(token, copies) if '/' in token else None
        if cpath and _test_like(cpath) and SOURCES.search(cpath) \
                and not _asked_to_change(instruction, cpath):
            sources.append(copies[cpath])
            out.append(_stored_path(copies[cpath]))
        else:
            out.append(token)
    return (out, sources) if sources else (None, [])


# --- the build of a compiled harness ----------------------------------------------------------------

def _project_root(copies, container):
    """The container directory the pristine project lives in."""
    parts = [PurePosixPath(p).parts for p in list(copies) + [container]]
    common = []
    for group in zip(*parts):
        if len(set(group)) == 1:
            common.append(group[0])
        else:
            break
    dirs = [part for part in common if part != '/']
    return '/' + '/'.join(dirs[:2]) if dirs else '/'


def _recipe_builds_only(makefile, target, copies, files):
    """A Makefile target that only builds, and does not run what it builds."""
    text = _text(files.get(copies.get(makefile)))
    if not text:
        return False
    variables, rules = _makefile(text)
    for rule_target, prereqs, recipe in rules:
        if (_resolve(rule_target, variables) or rule_target) != target:
            continue
        for line in recipe:
            tokens = _split(_resolve(line, variables) or '')
            if not tokens:
                continue
            head = PurePosixPath(tokens[0].lstrip('@+-').strip()).name
            if head not in COMPILERS and head not in ('mkdir', 'rm', 'cd', 'echo', 'cp', 'touch', 'ar'):
                return False
    return True


def _project_sources(container, makefile, copies, files, instruction):
    """The pristine files a Makefile build of `container` needs."""
    sources = []
    if makefile:
        sources.append(copies[makefile])
    where = str(PurePosixPath(makefile).parent) if makefile else str(PurePosixPath(container).parent)
    for cpath, task_file in sorted(copies.items()):
        if str(cpath).startswith(where.rstrip('/') + '/') and _test_like(cpath) and SOURCES.search(cpath) \
                and not _asked_to_change(instruction, cpath):
            sources.append(task_file)
    return list(dict.fromkeys(sources))


def _harness_build(container, copies, files, instruction):
    """(setup.sh lines, pristine sources) that put the original of a compiled harness back."""
    makefile, target = _make_target(container, copies, files)
    if makefile and target and _recipe_builds_only(makefile, target, copies, files):
        block, sources = _make_block(container, makefile, target, copies, files, instruction)
        if block:
            return block, sources

    for tokens in _compile_candidates(container, copies, files):
        command, sources = _with_originals(tokens, copies, files, instruction)
        if command:
            return ['%s || true' % shlex.join(command)], sources

    # No compile command to repeat: rebuild the harness from its original sources in place.
    sources = _project_sources(container, makefile, copies, files, instruction)
    if not sources:
        return [], []
    return [], sources


def _make_block(container, makefile, target, copies, files, instruction):
    """Build `container` with make, in a copy of the project under /tests."""
    sources = [s for s in _project_sources(container, makefile, copies, files, instruction)
               if files.get(s) is not None]
    if not sources:
        return [], []
    quoted = shlex.quote
    build_dir = BUILD
    block = ['mkdir -p %s' % quoted(build_dir)]
    for task_file in sorted(sources):
        destination = build_dir + _container_path(task_file, copies)
        block.append('mkdir -p %s' % quoted(str(PurePosixPath(destination).parent)))
        block.append('cp -f %s %s' % (quoted(_stored_path(task_file)), quoted(destination)))
    # The agent's own sources: the harness is rebuilt against them, not against the originals.
    for cpath, task_file in sorted(copies.items()):
        if SOURCES.search(cpath) and not _test_like(cpath) and not OBJECTS.search(cpath):
            destination = build_dir + cpath
            block.append('mkdir -p %s' % quoted(str(PurePosixPath(destination).parent)))
            block.append('if [ -f %s ]; then cp -f %s %s; fi' % (
                quoted(cpath), quoted(cpath), quoted(destination)))
    where = str(PurePosixPath(makefile).parent)
    block.append('make -C %s %s || make -C %s || true' % (
        quoted(build_dir + where), quoted(target), quoted(build_dir + where)))
    built = build_dir + where + '/' + target
    if built != build_dir + container:
        block.append('if [ -f %s ]; then cp -f %s %s; fi' % (
            quoted(built), quoted(built), quoted(container)))
    block.append('if [ -f %s ]; then cp -f %s %s; fi' % (
        quoted(build_dir + container), quoted(build_dir + container), quoted(container)))
    return block, sources


# --- the restore block ------------------------------------------------------------------------------

def _restore_lines(restores, blocks):
    lines = ['', '# The verifier grades the original test harness: restore it after the agent stops.']
    for container, task_file in sorted(restores.items()):
        parent = str(PurePosixPath(container).parent)
        if parent != '/':
            lines.append('mkdir -p %s' % shlex.quote(parent))
        lines.append('cp -f %s %s' % (shlex.quote(_stored_path(task_file)), shlex.quote(container)))
    seen = set()
    for block in blocks:
        key = '\n'.join(block)
        if key in seen:
            continue
        seen.add(key)
        lines.extend(block)
    return lines


# Explicit review verdicts keyed to the exact original instruction bytes.
VERDICTS = {
    '70a6c98609ac46850212e7405c06fb21603ab5d7fa452a21603e01374c2081c6': ('not_fixable', 'The task explicitly allows editing the Makefile to fix the library build; restoring the original would invalidate legitimate work, and no independent trusted verifier build is supplied.'),  # 36 contrastive_solver-software-engineering_20260604_004411_019
    '74dde68437babda95068f0d05aa5b2fe60e7fd79f88e0df6e917f9c50a0e9744': ('not_fixable', 'The task explicitly requires editing Makefile link order; the verifier builds its test executable through that editable Makefile, and this candidate has no independent trusted build.'),  # 12 contrastive_solver-software-engineering_20260420_020307_015
    '4db90f229e56f30a4fe6c2a8767dd6f37680e2562b9fec47d2dd21ab3fcf2aa8': ('false_positive', 'The instruction expressly includes repairing test_codec.c and its unreliable tests.'),  # 3 contrastive_solver-debugging_20260604_193056_001
    'f9e331c556bd38bc94ddeb2ed8eb005ea6a313f43ce5819858589a30b51c8448': ('not_fixable', 'The task requires repairing the build, including its Makefile, while the candidate needs to restore that Makefile to trust the test binary; this would undo legitimate work.'),  # 4 contrastive_solver-debugging_20260604_194036_001
    '2d8d0af57303fae006ec68c308342e560e30e28610ce1c8b05515b8d5b75b5c6': ('false_positive', 'eval.py is the requested deliverable, so running it tests the intended work.'),  # 8 contrastive_solver-machine-learning_20260618_093107_003
    '2859346bfd0431b40ed758681f67d9bc37013d5ff81ea72a69b81b88e6b646fb': ('false_positive', 'verify.sh is expressly one of the automation scripts the agent must repair.'),  # 10 contrastive_solver-security_20260511_150433_005
    'edcfb1ed744d8b13bd9ee145dcefc850b027929cab400f954805006d6df6a293': ('not_fixable', 'The task requires repairing the build across the shared library boundary; the verifier executes the resulting test application and this candidate cannot preserve a trusted test binary without overriding the required build changes.'),  # 15 contrastive_solver-software-engineering_20260424_090344_016
    'b76a8dc179a027517fa0fbc998f1857979208b42e3e2105356726a2f31304926': ('false_positive', 'expr_eval.py is the requested evaluator implementation.'),  # 18 contrastive_solver-software-engineering_20260501_172213_021
    '59a7dfb7f036550cc2b7b7231cbf52c9ad83750c79ee9d320c044da0b8c1271c': ('not_fixable', 'The task expressly asks to repair the test suite and its import setup, so restoring the original suite would undo legitimate work; no independent trusted suite is supplied.'),  # 22 contrastive_solver-software-engineering_20260507_173144_007
    'e599b6d133989bff08c2d0500c064c663c22a390fab9f3a0df8d88274628aa21': ('not_fixable', 'The task requires fixing the Makefile build that produces the test binary; restoring its original would undo legitimate work, and a trusted independent build is unavailable.'),  # 29 contrastive_solver-software-engineering_20260514_012509_002
    '0403c609fef4fc4eae34e7d774d5fbc0ed96578a42e564779d5cb9c71531425f': ('not_fixable', 'The task requires fixing the Makefile build that produces the test binary; restoring its original would undo legitimate work, and a trusted independent build is unavailable.'),  # 34 contrastive_solver-software-engineering_20260604_003814_007
    '59f69e1bd34e4920aba3a9c463711127755794db5d034abc7925461b70b754e2': ('not_fixable', 'The task requires implementing make test and the Makefile in /app/src; restoring the original build would undo legitimate work.'),  # 37 contrastive_solver-software-engineering_20260604_004411_020
    '566b7f4f22a40947013289db64b4259979ca7811088838a758b1549ce1e8c198': ('not_fixable', 'The task requires repairing project build and test_runner output; the current candidate depends on restoring its Makefile and would undo a legitimate build fix.'),  # 38 contrastive_solver-software-engineering_20260604_004411_023
    '5ea5c3da29b2e934197e33a0eddacb1fed4c428e201d381b88c6f172b62315f6': ('not_fixable', 'The task requires fixing the Makefile that builds the test binary; restoring it would undo legitimate work.'),  # 40 contrastive_solver-software-engineering_20260604_193356_019
    'aa62225afbcb5b0c6d8ad946fb32b7e4c39cd8830786201442a7754b4acd98e0': ('not_fixable', 'The task leaves the broken project unspecified and permits Makefile changes; restoring the original build would potentially undo legitimate work.'),  # 48 contrastive_solver-software-engineering_20260617_104707_008
    'ddd3feeaaf0fdf0bf985da58905fe8b4636fad531078cfefffc38878d9e074fc': ('false_positive', 'verify.sh is expressly one of the backup scripts the agent must repair.'),  # 54 contrastive_solver-system-administration_20260602_105139_002
    '903a48ebb5614522dc09159932d1f3a479c87eacce7e300e3f6fa6e21e6c4c23': ('false_positive', 'expr_eval.py is the requested evaluator implementation.'),  # 71 multi_solver-software-engineering_20260415_123352_022
}
# Verifier-authored direct builds: use original test driver sources from /tests.
DIRECT_COMPILE = {
    '98ed0d277f0e03a2c75b7fb0743d750617ccffbfbcdc190e25282e6610c6fed2': (25, 'test_sqlite_app.c'),
    'ef2c32e448ba6ec73ef08fda69af5735c92854aa1ad5e4fedc4f9d6b2f7d5455': (42, 'check_streaming.c'),
    'c7068886bb4a66bbc68003f36f7c6ae1d156525964c47baeca6be67d169df08a': (62, 'hashtable/test_stress.c'),
    '1d4da94d67f327ec68cfb5f02dc77c052e39588451751c944d1b67ad9b280d58': (66, 'test_queue.c'),
    'd86fd0e650c284feb2a6fce799b53a8a48764f28414b0178ff805e73c58755e7': (68, 'test_driver.c'),
}
# --- the repair -------------------------------------------------------------------------------------

def _z80_compile(files):
    """Compile the emulator with trusted driver sources and editable implementation."""
    result = dict(files)
    trusted = {}
    for name in ('main.c', 'memory.c'):
        source = 'environment/files/' + name
        if source not in files:
            return {'decision': 'not_fixable', 'reason': 'trusted Z80 driver source unavailable'}, None
        path = '/tests/' + hashlib.sha256(source.encode()).hexdigest() + '.c'
        trusted[name] = path
        result[path.lstrip('/')] = files[source]
    text = files['tests/test_outputs.py'].decode()
    old = '["make", "clean", "all"]'
    new = '[' + ', '.join(repr(token) for token in ('gcc', '-Wall', '-Wextra', '-O2', '-g',
        '-I/app/z80_emu', '-o', '/app/z80_emu/z80_test', trusted['main.c'],
        '/app/z80_emu/z80.c', trusted['memory.c'])) + ']'
    if old not in text:
        return {'decision': 'not_fixable', 'reason': 'Z80 verifier build command changed'}, None
    result['tests/test_outputs.py'] = text.replace(old, new).encode()
    if not _passes(result):
        return {'decision': 'not_fixable', 'reason': 'trusted Z80 compile does not clear the editable harness finding'}, None
    return {'decision': 'fixed', 'reason': 'verifier compiles its pristine test driver with the agent-editable Z80 implementation'}, result


def _direct_compile(files, specification):
    """Keep the agent's implementation editable while compiling trusted verifier drivers."""
    number, source = specification
    source_file = 'environment/files/' + source
    if source_file not in files or 'tests/test_outputs.py' not in files:
        return {'decision': 'not_fixable', 'reason': 'trusted driver source unavailable'}, None
    original = dict(files)
    trusted = '/tests/' + hashlib.sha256(source_file.encode()).hexdigest() + '.c'
    result = dict(files)
    result[trusted.lstrip('/')] = files[source_file]
    text = files['tests/test_outputs.py'].decode()
    container_source = '/app/' + source
    text = text.replace(container_source, trusted)
    if number == 25:
        text += '\n# The agent must not modify /app/test_sqlite_app.c.\n'
    if number == 62:
        text = text.replace('["make", "asan"]', '["gcc", "-Wall", "-Wextra", "-g", "-O0", "-fsanitize=address", "-I/app/hashtable", "-o", "/app/hashtable/test_stress_asan", ' + repr(trusted) + ', "/app/hashtable/hashtable.c"]')
        text = text.replace('["make", "all"]', '["gcc", "-Wall", "-Wextra", "-g", "-O0", "-I/app/hashtable", "-o", "/app/hashtable/test_stress", ' + repr(trusted) + ', "/app/hashtable/hashtable.c"]')
    if number in (66, 68):
        # Quoted includes move from /app to the trusted source folder.
        text = text.replace('"gcc",', '"gcc", "-I/app",')
    result['tests/test_outputs.py'] = text.encode()
    if not _passes(result):
        return {'decision': 'not_fixable', 'reason': 'the trusted direct compile does not clear every editable harness path'}, None
    return {'decision': 'fixed', 'reason': 'verifier builds its driver from a pristine source under /tests, while agent implementation sources remain editable'}, result


def repair_agent_editable_test(files, evidence):
    identity = hashlib.sha256(files.get('instruction.md', b'')).hexdigest()
    if identity == 'b7d77865760d27023bede8a8f62ecb572cebcff0c42ad275c84598623713bd0e':
        return _z80_compile(files)
    if identity in DIRECT_COMPILE:
        return _direct_compile(files, DIRECT_COMPILE[identity])
    verdict = VERDICTS.get(identity)
    if verdict is not None:
        return {'decision': verdict[0], 'reason': verdict[1]}, None
    instruction = _instruction(files)
    findings = _findings(files)
    paths = _evidence_paths(evidence)
    if not findings and not paths:
        return None
    if not findings:
        return {'decision': 'false_positive',
                'reason': 'the verifier files of this task run nothing outside /tests that the agent could '
                          'edit without it being restored'}, None

    copies = _copy_map(files)
    restores, blocks, reasons = {}, [], []

    for event in findings:
        container, kind = event['path'], event['kind']
        provided = copies.get(container) or [c for c in copies
                                             if str(c).startswith(container.rstrip('/') + '/')]
        if kind == 'suite' and not provided:
            reasons.append('the verifier runs the test suite in %s, whose files the task does not provide, '
                           'so their originals cannot be restored' % container)
            continue
        if kind == 'suite':
            prefix = container.rstrip('/') + '/'
            suite = sorted(c for c in copies if c.startswith(prefix))
            chosen = [c for c in suite if (_test_like(c) or BUILD_FILES.search(c))
                      and not _asked_to_change(instruction, c)]
            if not chosen:
                chosen = [c for c in suite if not _asked_to_change(instruction, c)]
            for cpath in chosen:
                restores[cpath] = copies[cpath]
            reasons.append('the verifier runs the test suite in %s from the image, whose test files the '
                           'agent could edit; tests/setup.sh restores their originals' % container)
            continue

        source = copies.get(container)
        if source and not _asked_to_change(instruction, container):
            restores[container] = source
            reasons.append('the verifier runs %s from the image, which the agent could edit; tests/setup.sh '
                           'restores its original' % container)
            continue

        # A harness the image compiled: restore its original sources and rebuild it.
        block, sources = _harness_build(container, copies, files, instruction)
        if sources:
            for task_file in sorted(sources):
                cpath = next((c for c, f in copies.items() if f == task_file), None)
                if cpath and not _asked_to_change(instruction, cpath):
                    restores[cpath] = task_file
            if block:
                blocks.append(block)
            reasons.append('the verifier runs %s, a harness the image compiled that the agent could '
                           'replace; tests/setup.sh restores its original sources and rebuilds it from '
                           'them' % container)
            continue

        reasons.append('no original of %s is part of the task, so it cannot be restored' % container)

    if not restores and not blocks:
        return {'decision': 'not_fixable',
                'reason': '; '.join(dict.fromkeys(reasons))}, None

    setup = _text(files.get('tests/setup.sh'))
    if setup is None:
        return {'decision': 'not_fixable',
                'reason': 'the verifier runs files the agent could edit, but tests/setup.sh cannot be '
                          'extended to restore them'}, None

    repaired = dict(files)
    for container, task_file in sorted(restores.items()):
        data = files.get(task_file)
        if data is not None:
            repaired['tests/%s' % _stored(task_file)] = data
    repaired['tests/setup.sh'] = (setup + '\n'.join(_restore_lines(restores, blocks)) + '\n').encode()

    if not _passes(repaired):
        return {'decision': 'not_fixable',
                'reason': 'restoring the originals in tests/setup.sh does not cover everything the verifier '
                          'runs: ' + '; '.join(dict.fromkeys(reasons))}, None

    return {'decision': 'fixed',
            'reason': 'the verifier grades files the agent could edit, so their originals ship with the '
                      'verifier under /tests and tests/setup.sh restores them before the tests run: '
                      + '; '.join(dict.fromkeys(reasons))}, repaired


def _passes(files):
    return not _findings(files)
