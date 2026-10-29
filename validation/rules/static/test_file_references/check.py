"""Our patch to Terminal-Bench check-test-file-references.sh: fix the upstream extractor and
filter references that are not files the instruction must name."""
import re
from validation.rules.static.task_absolute_path.check import without_urls

CHECK = 'check-test-file-references.sh'
ADAPTATION = (
    'masks complete non-file URLs, XML hostnames and excluded system paths; fixes full-token extraction '
    'and shell redirections, preserving basenames without inventing /app paths; accepts conventional '
    'boot artifacts, explicit numeric X filename ranges, explicit label filename templates resolved from '
    'supplied label:glob configurations, and references discovered through named supplied '
    'files/directories; reads literal writes and copies/moves in explicitly invoked local setup shell '
    'scripts without executing them, with source evidence')


# Specific conventional interfaces, not an exemption for everything under /boot.
CONVENTIONAL_REFERENCE_PATHS = (
    r'/boot/grub/grub\.cfg',
    r'/boot/initrd\.img-[0-9][A-Za-z0-9_.+-]*',
)


# Called by the adapted check for each candidate before it is reported; exit 0 = not a missing file.
# The upstream extractor treats any quoted "/..." string that appears in both the tests and the
# solution as an output file. Reviewed false positives (FACET full run 2026-10-17, 14 of 20 sampled):
# URL routes ("/healthz"), regular expressions ("/([^/]+)/?$"), PDF name objects ("/Annots"), the
# task's own scripts (solve.sh), and names the instruction does contain but with trailing
# punctuation glued on ("...transcript.txt."). Second pass (63 remaining): members read inside a
# zip/ODF/OOXML archive (zf.open('content.xml')), escaped newlines glued to a name, and paths used
# only as an environment variable's default (os.environ.get("HOME", "/root")). Three reviewed
# FACET word tokens are listed below. Real outputs with an extension still fail.
REFERENCE_FILTER = r'''
import pathlib, re, sys
ref, test, solution, instruction, toml = sys.argv[1:6]
read = lambda p: pathlib.Path(p).read_text(errors='replace') if p and pathlib.Path(p).is_file() else ''
task = read(instruction) + '\n' + read(toml)
# 0. Reviewed tokens that are words, not files (FACET full run 2026-10-17): a docstring example
#    ('a.b.c'), a word from an error message ('still'), part of test function names ('exists'), and an
#    ODT member listed in a plain list of required archive members ('content.xml').
REVIEWED = {('task_001311', 'a.b.c'), ('task_001458', 'still'), ('task_001913', 'exists'),
            ('task_004394', 'content.xml')}
if (pathlib.Path(test).parents[1].name, ref) in REVIEWED:
    sys.exit(0)
# 1. Mentioned once tokenization noise is removed (escaped newlines, trailing punctuation, glued code).
cleaned = re.split(r'[\[(]', re.sub(r'(\\[ntr])+$', '', ref))[0].rstrip('.,;:')
if cleaned and cleaned != ref and (cleaned in task or cleaned.rsplit('/', 1)[-1] in task.split()
                                   or re.search(r'(?<![\w.-])' + re.escape(cleaned.rsplit('/', 1)[-1]) + r'(?![\w-])', task)):
    sys.exit(0)
# 2. Regular expressions, not paths.
if re.search(r'\\[wdsWDS.\-/]|\[\^?[^\]]*\][+*?]|\(\?|\)\?|\$$|^\^|\.\*|\.\+|\|', ref):
    sys.exit(0)
name = ref.rstrip('/').rsplit('/', 1)[-1]
quoted = [l for t in (read(test), read(solution)) for l in t.splitlines()
          if "'" + ref + "'" in l or '"' + ref + '"' in l]
# 2b. Members read or written inside an archive (zf.open('content.xml')), never files on disk.
member = re.compile(r'\b\w*(?:zf|zip|zh|archive|odt|ods|docx|xlsx|pptx|tf|tar)\w*\.(?:open|read|writestr|getinfo'
                    r'|extractfile|getmember)\(|namelist\(|\bin\s+(?:names|members|namelist)\b|full-path=')
if quoted and all(member.search(l) for l in quoted):
    sys.exit(0)
# 2c. Only the default value of an environment variable (HOME -> "/root").
default = re.compile(r'(?:environ\.get|getenv)\([^)]*,\s*["\']' + re.escape(ref) + r'["\']|\$\{\w+:-' + re.escape(ref) + r'\}')
users = [l for t in (read(test), read(solution)) for l in t.splitlines() if ref in l]
if users and all(default.search(l) for l in users):
    sys.exit(0)
# 3. The task's own harness scripts.
if name in ('solve.sh', 'test.sh', 'setup.sh') or re.fullmatch(r'test_[\w.-]*\.py', name):
    sys.exit(0)
# 4. Without an extension, count it only where the code uses it as a file or directory
#    (URL routes and PDF name objects are never opened, listed or written).
if '.' not in name:
    use = re.compile(r'open\(|Path\(|os\.path|exists\(|is_file|is_dir|read_text|read_bytes|write_text|write_bytes'
                     r'|mkdir|makedirs|listdir|scandir|stat\(|glob\(|shutil\.|chmod|\b(?:cat|cp|mv|touch|ls|rm|tee'
                     r'|head|tail|find|tar|zip|unzip)\b')
    lines = [l for t in (read(test), read(solution)) for l in t.splitlines() if ref in l]
    redirect = any(re.search(r'>>?\s*["\']?' + re.escape(ref), l) for l in lines)
    if lines and not redirect and not any(use.search(l) for l in lines):
        sys.exit(0)
sys.exit(1)
'''


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
    source = ''.join(lines)
    anchor = '        # Check if the filename or full path is mentioned in instruction.md or task.toml\n'
    if source.count(anchor) != 1:
        raise ValueError('upstream file-reference comparison changed')
    hook = ('        if python3 - "$file_ref" "$test_file" "$solution_sh" "$instruction_md" "$task_toml" '
            "<<'OT_REFERENCE_FILTER'" + REFERENCE_FILTER + 'OT_REFERENCE_FILTER\n'
            '        then\n            continue\n        fi\n\n')
    return source.replace(anchor, hook + anchor)


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
