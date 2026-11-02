"""NL2Bash repairs: what we learned from running tasks and checks, not from attempting builds.

patch.py alone gives tasks that build and start; `patch.py --no-repairs` stops there, so the
validation pipeline can be evaluated on the plain conversion. Every item below changes only
instruction.md and came from a rule result or the path resolver, and is applied on top.

Fixture wording  Reference review: seeds are in place, and the verifier reads no helper files.
Output path      Stage-4 path warning: task_267 named a capture file the verifier does not read.
Path edits       Stage-1 path resolver, exact replacements as published, archived tasks included.
Suffix           Timeout and anti-cheat sentence from the instruction-suffix rule.
"""
from validation.rules.static.instruction_suffix import suffix

FIXTURE_LABEL = 'repair:instruction-fixtures.py'
OUTPUT_LABEL = 'repair:instruction-output-path.py'
PATHS_LABEL = 'repair:instruction-absolute-paths.py'
SUFFIX_LABEL = 'repair:instruction-suffix.py'
REASONS = {
    FIXTURE_LABEL: ('The fixtures line now says the input files are already in /workspace, and the claim '
                    'that the verifier inspects helper files is removed (it reads only '
                    '/output/command_capture.txt).'),
    OUTPUT_LABEL: ('The goal named the capture file as $PWD/command_capture.txt. The agent runs from '
                   '/workspace, so $PWD expands to /workspace, but the verifier reads only '
                   '/output/command_capture.txt (as the requirements say). The goal now names '
                   '/output/command_capture.txt; nothing else changes.'),
    PATHS_LABEL: 'The path resolver made file paths in the goal absolute, as observed in the reference run.',
    SUFFIX_LABEL: 'Added the sentence with the agent timeout and the request not to cheat.',
}
INPUT_LINES = (
    b'- From `/workspace`, assemble any missing fixtures using standard shell utilities '
    b'before running your command.\n',
    b'- The workspace already contains pre-seeded fixtures copied to `/workspace`; '
    b'run the command from that directory.\n',
)
INPUT_LINE = (b'- The input files for this task are already in `/workspace`; '
              b'run the command from that directory.\n')
HELPER_LINE = b'- Do not delete any helper files generated during execution; the verifier inspects them.\n'
# Reviewed 2026-10-17 (stage-4 path warnings): goal sentences that name a capture file other than
# the one the verifier reads. Exact one-occurrence replacement, per task.
OUTPUT_PATH_FIXES = {
    'task_267': (b'capturing output in $PWD/command_capture.txt.',
                 b'capturing output in /output/command_capture.txt.'),
}
# Resolver edits from the published release: task -> (old, new), each old unique in its instruction.
PATH_EDITS = {
    'task_53': ((b"m file 'file2.tx", b"m file '/workspace/file2.tx"), (b"n file 'file1.tx", b"n file '/workspace/file1.tx")),
    'task_83': ((b' named "non_secr', b' named "/workspace/non_secr'), (b'ing to "secret_f', b'ing to "/workspace/secret_f')),
    'task_188': ((b"n file 'access-l", b"n file '/workspace/access-l"),),
    'task_237': ((b"nts of 'foo.txt'", b"nts of '/workspace/foo.txt'"),),
    'task_271': ((b'tation (docs/REA', b'tation (/workspace/docs/REA'),),
    'task_382': ((b'nes in "input_da', b'nes in "/workspace/input_da'),),
    'task_490': ((b' named "data.txt', b' named "/workspace/data.txt'),),
    'task_797': ((b' named "database', b' named "/workspace/database'),),
    'task_840': ((b" named 'save_pid", b" named '/workspace/save_pid"),),
    'task_879': ((b" files 'ignore.t", b" files '/workspace/ignore.t"), (b"t' and 'input_a.", b"t' and '/workspace/input_a.")),
    'task_1107': ((b"s from 'file1.tx", b"s from '/workspace/file1.tx"), (b"ent in 'file2.tx", b"ent in '/workspace/file2.tx")),
    'task_1159': ((b' named "loglist.', b' named "/workspace/loglist.'),),
    'task_1242': ((b"e file 'demo.txt", b"e file '/workspace/demo.txt"),),
    'task_1421': ((b"ne) in 'allDirs.", b"ne) in '/workspace/allDirs."),),
    'task_1810': ((b' named "ignore_s', b' named "/workspace/ignore_s'), (b't" and "input_so', b't" and "/workspace/input_so')),
    'task_1845': ((b'sted in ./exclude', b'sted in /workspace/exclude'),),
    'task_2045': ((b'e file "a.csv" b', b'e file "/workspace/a.csv" b'),),
    'task_2156': ((b"h of a 'meow.wav", b"h of a '/workspace/meow.wav"),),
    'task_2255': ((b'V file "a.csv" b', b'V file "/workspace/a.csv" b'),),
    'task_2315': ((b'ear in "second.t', b'ear in "/workspace/second.t'), (b'not in "first.tx', b'not in "/workspace/first.tx')),
    'task_2506': ((b'ated at ./example', b'ated at /workspace/example'),),
    'task_2752': ((b'\nSplit "hugefile', b'\nSplit "/workspace/hugefile'),),
    'task_3440': ((b"m file 'sample.t", b"m file '/workspace/sample.t"),),
    'task_3461': ((b'ine of "data.csv', b'ine of "/workspace/data.csv'),),
    'task_3566': ((b'xactly "mutant_i', b'xactly "/workspace/mutant_i'),),
    'task_3831': ((b' named "name1.tx', b' named "/workspace/name1.tx'), (b'xt" or "name2.tx', b'xt" or "/workspace/name2.tx')),
    'task_4268': ((b's from "sample.t', b's from "/workspace/sample.t'),),
    'task_4293': ((b' named "out.txt"', b' named "/workspace/out.txt"'),),
    'task_4479': ((b' named "dummy.bi', b' named "/workspace/dummy.bi'),),
    'task_4761': ((b'atches "file9.tx', b'atches "/workspace/c/file9.tx'),),
    'task_4895': ((b"e path './example", b"e path '/workspace/example"),),
    'task_4902': ((b'le from src/subd', b'le from /workspace/src/subd'),),
    'task_5194': ((b's with "rc.conf"', b's with "/workspace/rc.conf"'),),
    'task_5214': ((b' named "sample.t', b' named "/workspace/sample.t'),),
    'task_5333': ((b'ines in ./sample_', b'ines in /workspace/sample_'),),
    'task_5898': ((b' named "rc.conf"', b' named "/workspace/a/rc.conf"'),),
    'task_6050': ((b'ted in "hosts.tx', b'ted in "/workspace/hosts.tx'),),
    'task_6095': ((b" named 'a.csv' b", b" named '/workspace/a.csv' b"),),
    'task_6273': ((b' named "addresse', b' named "/workspace/addresse'),),
    'task_6501': ((b' named "status.t', b' named "/workspace/demo/B/status.t'),),
    'task_6652': ((b' named "command_', b' named "/workspace/command_'),),
    'task_6716': ((b" named 'article.", b" named '/workspace/testdir/a/article."),),
    'task_6979': ((b'o file "file_lis', b'o file "/workspace/file_lis'),),
    'task_6998': ((b" named 'my_infil", b" named '/workspace/my_infil"),),
    'task_7007': ((b'ine in "ip_addre', b'ine in "/workspace/ip_addre'),),
    'task_7300': ((b'he file data/foo', b'he file /workspace/data/foo'),),
    'task_7306': ((b' named "emptyfil', b' named "/workspace/emptyfil'),),
    'task_7320': ((b' named "emptyfil', b' named "/workspace/emptyfil'),),
    'task_7324': ((b"nes in 'temp.txt", b"nes in '/workspace/temp.txt"),),
    'task_7455': ((b' named "bigfile.', b' named "/workspace/bigfile.'),),
    'task_7609': ((b't file (scripts/', b't file (/workspace/scripts/'),),
    'task_7632': ((b'he file project/', b'he file /workspace/project/'),),
    'task_7902': ((b'e file "logs.txt', b'e file "/workspace/logs.txt'), (b'ted in "patterns', b'ted in "/workspace/patterns')),
    'task_7961': ((b'ted in "hosts.tx', b'ted in "/workspace/hosts.tx'),),
    'task_8233': ((b"e file 'foobar.t", b"e file '/workspace/foobar.t"), (b" named 'mutated_", b" named '/workspace/mutated_")),
    'task_8442': ((b' named "bla.txt"', b' named "/workspace/test_env/subdir/bla.txt"'),),
    'task_8576': ((b'nks for ./relativ', b'nks for /workspace/relativ'),),
    'task_8684': ((b'e file "www1.htm', b'e file "/workspace/www1.htm'),),
    'task_8706': ((b'\nSplit "sample.t', b'\nSplit "/workspace/sample.t'),),
    'task_8745': ((b"m file 'domains.", b"m file '/workspace/domains."),),
    'task_8774': ((b"e file 'target.t", b"e file '/workspace/target.t"), (b"ted in 'badwords", b"ted in '/workspace/badwords")),
    'task_8872': ((b'he path ./file/go', b'he path /workspace/file/go'),),
    'task_9076': ((b' named "sample_i', b' named "/workspace/sample_i'),),
    'task_9238': ((b"nes of 'sample.t", b"nes of '/workspace/sample.t"),),
    'task_9295': ((b'ine in "ports.tx', b'ine in "/workspace/ports.tx'),),
    'task_9355': ((b'e file "addresse', b'e file "/workspace/addresse'),),
    'task_9377': ((b' named "ip_addre', b' named "/workspace/ip_addre'),),
    'task_9636': ((b"s from 'data.txt", b"s from '/workspace/data.txt"),),
}


def repair_instruction(task_id, body, config):
    """Return the repaired instruction and its labels; already repaired text stays as it is."""
    labels = []
    if sum(body.count(line) for line in INPUT_LINES) == 1 and body.count(HELPER_LINE) == 1:
        for line in INPUT_LINES:
            body = body.replace(line, INPUT_LINE)
        body = body.replace(HELPER_LINE, b'')
        labels.append(FIXTURE_LABEL)
    elif body.count(INPUT_LINE) != 1 and task_id:
        raise ValueError('instruction.md lacks the expected fixtures and helper-files lines')
    if task_id in OUTPUT_PATH_FIXES:
        old, new = OUTPUT_PATH_FIXES[task_id]
        if body.count(old) == 1:
            body = body.replace(old, new)
            labels.append(OUTPUT_LABEL)
        elif not (body.count(old) == 0 and body.count(new) == 1):
            raise ValueError(f'{task_id}: expected exactly one {old!r} in instruction.md')
    edits = PATH_EDITS.get(task_id, ())
    for old, new in edits:
        if body.count(old) == 1:
            body = body.replace(old, new)
            labels.append(PATHS_LABEL)
        elif body.count(new) != 1:
            raise ValueError(f'{task_id}: expected exactly one {old!r} in instruction.md')
    fixed = suffix(body, config)
    if fixed != body:
        labels.append(SUFFIX_LABEL)
    return fixed, list(dict.fromkeys(labels))
