"""Post-build Multi-SWE repairs learned from validation outcomes.

The upstream audit skips these with patch.py --no-repairs.
"""
import inspect
import re

TASK_MEMORY_MB = {'gohugoio__hugo-12703': 8192}


SPOTBUGS_VERIFIER_FIXTURES = {
    'spotbugs__spotbugs-2872': 'spotbugsTestCases/src/java/ghIssues/Issue453.java',
    'spotbugs__spotbugs-2887': 'spotbugsTestCases/src/java/ghIssues/Issue608.java',
    'spotbugs__spotbugs-3032': 'spotbugsTestCases/src/java/ghIssues/Issue2968.java',
}


def repair_spotbugs_verifier_fixtures(files, task_id):
    """Supply regression fixtures discarded by the upstream fix/test path split."""
    fixture = SPOTBUGS_VERIFIER_FIXTURES.get(task_id)
    target = 'setup_files/upstream/test.patch'
    if fixture is None or target not in files:
        return files
    header = f'diff --git a/{fixture} b/{fixture}\n'.encode()
    blocks = re.split(rb'(?m)(?=^diff --git )', files['solution/gold.patch'])
    matches = [block for block in blocks if block.startswith(header)]
    if len(matches) != 1 or b'\n--- /dev/null\n' not in matches[0]:
        raise ValueError(f'Expected one new regression fixture for {task_id}')
    test_patch = files[target]
    if header in test_patch:
        if matches[0].rstrip(b'\n') not in test_patch:
            raise ValueError(f'Conflicting regression fixture for {task_id}')
        if 'tests/upstream/test.patch' in files:
            return {**files, 'tests/upstream/test.patch': test_patch}
        return files
    # The fixture is a verifier input, not the production fix. Preserve the
    # original reference and assertions; apply the fixture after extraction
    # resets the repository, for both oracle and unchanged-baseline trials.
    updated = {**files, target: test_patch.rstrip(b'\n') + b'\n' + matches[0]}
    if 'tests/upstream/test.patch' in files:
        updated['tests/upstream/test.patch'] = updated[target]
    return updated


def normalize_hugo_output(output):
    """Separate Go test results appended to a warning without a newline."""
    import re
    return re.sub(
        r'(?m)(?<=[^\n])(?=--- (?:PASS|FAIL|SKIP): \S+ \([0-9]+(?:\.[0-9]+)?s\)[ \t]*$)',
        '\n', output)


def repair_hugo_result_lines(files, task_id):
    if task_id != 'gohugoio__hugo-13005':
        return files
    import inspect
    grade = files['tests/grade.py'].decode()
    if 'def normalize_hugo_output(' in grade:
        return files
    anchor = '    reward_path = Path(sys.argv[3])'
    if grade.count('def main():') != 1 or grade.count(anchor) != 1:
        raise ValueError('Unknown Hugo grader structure')
    grade = grade.replace('def main():', inspect.getsource(normalize_hugo_output) + '\n\ndef main():')
    grade = grade.replace(anchor, '    output = normalize_hugo_output(output)\n' + anchor)
    return {**files, 'tests/grade.py': grade.encode()}


def apply_task_memory(files, task_id):
    """Apply the operator-selected budget; expected no-op OOM is reviewed separately."""
    if task_id not in TASK_MEMORY_MB:
        return files
    updated = dict(files)
    updated['task.toml'], count = re.subn(
        rb'(?m)^memory_mb[ \t]*=[ \t]*\d+[ \t]*$',
        f'memory_mb = {TASK_MEMORY_MB[task_id]}'.encode(), files['task.toml'])
    if count != 1:
        raise ValueError('Expected one task memory setting')
    return updated


def expected_missing_source(row, output):
    """Recognize a baseline import failure for a source file supplied by the fix."""
    import posixpath
    import re
    baseline = row.get('test_patch_result', {})
    if any(baseline.get(k, 0) for k in ('passed_count', 'failed_count', 'skipped_count')):
        return False
    match = re.search(r"Cannot find module '([^']+)'\nRequire stack:\n- ([^\n]+)", output)
    if not match or not match[1].startswith('.'):
        return False
    missing = posixpath.normpath(posixpath.join(posixpath.dirname(match[2]), match[1]))
    added = re.findall(r'(?m)^--- /dev/null\n\+\+\+ b/([^\n]+)', row.get('fix_patch', ''))
    if any('/home/' + row.get('repo', '') + '/' + name == missing + suffix
           for name in added for suffix in ('', '.js', '.jsx', '.ts', '.tsx', '/index.js', '/index.ts')):
        return True
    # The fix can also remove a broken import rather than add its target.
    importer = match[2].removeprefix('/home/' + row.get('repo', '') + '/')
    for block in row.get('fix_patch', '').split('diff --git '):
        if '\n--- a/' + importer + '\n' not in block:
            continue
        for line in block.splitlines():
            if line.startswith('-') and re.search(r'''(?:from\s*|require\s*\(\s*|import\s*)['"]''' + re.escape(match[1]) + r'''['"]''', line):
                return True
    return False


def normalize_trpc_output(output):
    """The pinned tRPC parser requires ANSI color even for non-interactive logs."""
    import re
    lines = []
    for line in output.splitlines():
        plain = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        match = re.match(r'^(?:@trpc/tests:test-ci: )?\[0\]\s+([✓❯])\s+(?:\|tests\|\s+)?([^|\s]\S*)', plain)
        if match:
            color = '32' if match[1] == '✓' else '33'
            line = f'[0] \x1b[{color}m{match[1]}\x1b[39m \x1b[32m|tests|\x1b[39m {match[2]}'
        lines.append(line)
    return '\n'.join(lines)


def normalize_nuxt_output(output):
    """Map verbose Vitest results to upstream identifiers, with failures winning."""
    import re
    statuses = {}
    for line in output.splitlines():
        line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        match = re.match(r'^([✓×❯])\s+(\S+)(.*)', line)
        if not match:
            continue
        names = [match[2]]
        detail = re.match(r'^ > (\S+)', match[3])
        if detail and re.search(r'\.(test|spec)\.', match[2]):
            names.append(detail[1])
        for name in names:
            statuses[name] = statuses.get(name, True) and match[1] == '✓'
    return '\n'.join(('✓' if passed else '❯') + ' ' + name + ' result'
                     for name, passed in statuses.items())


def repository_files(files, repo):
    updated = dict(files)
    if repo in ('dayjs', 'darkreader', 'spotbugs', 'mockito', 'react-router', 'syncthing') and 'task.toml' in updated:
        updated['task.toml'] = updated['task.toml'].replace(b'memory_mb = 4096', b'memory_mb = 8192')
    if repo == 'nuxt':
        grade = updated['tests/grade.py'].decode()
        # The pinned parser uses the first token of each result line. Verbose
        # Vitest prefixes individual tests with their file, while the original
        # dataset also records suite names from default-reporter slow-test lines.
        import inspect
        grade = grade.replace('def main():', inspect.getsource(normalize_nuxt_output) + '\n\ndef main():')
        grade = grade.replace('    reward_path = Path(sys.argv[3])',
                              '    output = normalize_nuxt_output(output)\n    reward_path = Path(sys.argv[3])')
        updated['tests/grade.py'] = grade.encode()
    if repo == 'trpc':
        import inspect
        grade = updated['tests/grade.py'].decode()
        grade = grade.replace('def main():', inspect.getsource(normalize_trpc_output) + '\n\ndef main():')
        grade = grade.replace('    reward_path = Path(sys.argv[3])',
                              '    output = normalize_trpc_output(output)\n    reward_path = Path(sys.argv[3])')
        updated['tests/grade.py'] = grade.encode()
    return updated

def recipe_tests(recipe):
    run = recipe['files']['fix-run.sh']
    # git apply rejects an empty fix.patch, preventing no-op tests from running.
    def split_apply(match):
        command = match.group(0).replace(' /home/fix.patch', '')
        fix_command = command.replace('/home/test.patch', '/home/fix.patch')
        return command + '\nif [ -s /home/fix.patch ]; then ' + fix_command + '; fi'
    run = re.sub(r'(?m)^git apply[^\n]* /home/test\.patch /home/fix\.patch[ \t]*$', split_apply, run)
    if recipe['repo'] == 'darkreader':
        run = run.replace('npm run test:unit -- ', 'npm run test:unit -- --runInBand ')
    if recipe['repo'] == 'dayjs':
        # npm test runs Jest repeatedly for four time zones. Limit every invocation.
        limit = "node -e 'const fs=require(\"fs\"); const p=JSON.parse(fs.readFileSync(\"package.json\")); for(const k of Object.keys(p.scripts||{})) p.scripts[k]=p.scripts[k].replace(/\\bjest\\b/g, \"jest --runInBand\"); fs.writeFileSync(\"package.json\", JSON.stringify(p));'\n"
        run = run.replace('npm test ', limit + 'npm test ')
    if recipe['repo'] == 'nuxt':
        run = run.replace('pnpm test:unit -- --verbose', 'pnpm test:unit --reporter=verbose')
        run = run.replace('pnpm test:runtime  --no-watch', 'pnpm test:runtime --no-watch --reporter=verbose')
    if recipe['repo'] == 'axios':
        # Local proxy tests must control their own proxy environment. Node 20's
        # new family-autoselection default breaks this older custom lookup API.
        run = run.replace('npm test ', 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY npm_config_proxy npm_config_https_proxy npm_config_noproxy\n'
                          'if node --help | grep -q -- --network-family-autoselection; then\n'
                          '    export NODE_OPTIONS="${NODE_OPTIONS:-} --no-network-family-autoselection"\nfi\n'
                          'npm test ')
    recipe['files']['fix-run.sh'] = run
    return recipe


def grader(grade):
    error_line = '        print(f"Multi-SWE grader error: {type(exc).__name__}: {exc}", file=sys.stderr)'
    if grade.count(error_line) != 1:
        raise ValueError('Unexpected grader error handling')
    grade = grade.replace(error_line, error_line + '\n        reward_path.unlink(missing_ok=True)\n        raise')
    import inspect
    grade = grade.replace('def main():', inspect.getsource(expected_missing_source) + '\n\ndef main():')
    grade = grade.replace('        reward = float(report.valid',
                          '        if report.fix_patch_result.all_count == 0 and not expected_missing_source(row, output):\n'
                          '            raise RuntimeError("Verifier produced no parsed test results; see test_output.txt")\n'
                          '        reward = float(report.valid')
    grade = grade.replace('        print(report.error_msg or "valid report")',
                          '        missing = {field: sorted(set(getattr(dataset, field)) - set(getattr(report, field)))\n'
                          '                   for field in ("p2p_tests", "f2p_tests", "s2p_tests", "n2p_tests")}\n'
                          '        print(report.error_msg or ("Missing required test results: " + json.dumps(missing) if not reward else "valid report"))')
    return grade

def instruction_suffix(instruction, config):
    from validation.rules.static.instruction_suffix import suffix
    return suffix(instruction, config)


def grader_metadata(files):
    """Historical opt-in added after reviewing Multi-SWE execution evidence."""
    if 'task.toml' not in files:
        return files
    text = files['task.toml'].decode()
    if re.search(r'(?m)^verifier_reports_test_results\s*=', text):
        return files
    marker = '[metadata]\n'
    if marker in text:
        text = text.replace(marker, marker+'verifier_reports_test_results = true\n', 1)
    else:
        text = text.rstrip()+'\n[metadata]\nverifier_reports_test_results = true\n'
    return {**files, 'task.toml': text.encode()}
