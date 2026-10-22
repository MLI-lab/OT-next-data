"""Observe running environments; build success alone is not runtime parity."""
from __future__ import annotations

import posixpath
import shlex
import tomllib


def declared_workdir(dockerfile):
    """Resolve literal final-stage WORKDIRs; never guess inherited/variable paths."""
    if not dockerfile.is_file():
        return None
    cwd = None
    for line in dockerfile.read_text().replace('\\\n', ' ').splitlines():
        op, _, value = line.strip().partition(' ')
        if op.upper() == 'FROM':
            cwd = None
        elif op.upper() == 'WORKDIR':
            if '$' in value:
                cwd = None
            elif value.startswith('/'):
                cwd = posixpath.normpath(value)
            elif cwd:
                cwd = posixpath.normpath(posixpath.join(cwd, value))
    return cwd


async def inspect_environment(environment, task, context, label):
    checks = []
    async def check(name, command, expected=None):
        result = await environment.exec(command, timeout_sec=30)
        output = (result.stdout or '').strip()
        passed = result.return_code == 0 and (expected is None or output == expected)
        checks.append({'check': name, 'status': 'passed' if passed else 'failed',
            'command': command, 'exit_code': result.return_code,
            'stdout': output[:4000], 'stderr': (result.stderr or '')[:2000],
            **({'expected': expected} if expected is not None else {})})
        return output

    expected = declared_workdir(context / 'Dockerfile')
    if expected:
        # Resolve symlinks in the declared path while observing the actual default cwd.
        q = shlex.quote(expected)
        await check('declared-workdir', f'actual=$(pwd -P) && expected=$(cd {q} && pwd -P) && '
                    'printf "%s\\n" "$actual" && test "$actual" = "$expected"')
    else:
        observed = await check('working-directory-accessible', 'pwd -P')
        checks.append({'check': 'declared-workdir', 'status': 'not_checked',
                       'reason': 'No resolvable literal final-stage WORKDIR; inherited/variable paths need an explicit probe.',
                       'observed': observed})

    checks.append(await inspect_network(environment, declared_policy(task, label)))
    result = {'status': 'failed' if any(c['status'] in ('failed', 'error') for c in checks) else 'passed',
              'checks': checks}
    if label == 'agent':
        # Kept apart from the runtime checks: a leak is a task verdict, not a build to retry.
        result['leaks'] = await inspect_leaks(environment)
    return result


# Repository leaks the agent can read (Vals AI, "MiMo reward hacking", 2026-10-07): the
# fix as git objects or refs beyond HEAD, and the fix's files told apart by their mtimes.
REPOSITORY_ROOTS = ('/testbed', '/workspace/repo', '/workspace', '/app', '/repo', '/code')
# fsck --connectivity-only of a large repository takes minutes; a timeout is no verdict.
LEAK_TIMEOUT_SEC = 300
# Clone and checkout write files continuously; a pause this long means a later build step.
MTIME_GAP_SECONDS = 600
# A later group this small (share of clean tracked files) singles out files, not the tree.
MTIME_MINORITY = 0.2
EXAMPLES = 20
MTIME_EXAMPLES = 50
# Written by the automatic repair step (validation.checks.repository_repair) at build time.
REPAIR_REPORT = '/var/lib/ot-repository-repair.json'
AFTER_REPAIR = 'leak still present after automatic repair'
GIT = "git -c safe.directory='*'"


def repositories_command(roots=REPOSITORY_ROOTS):
    return ('have=0; command -v git >/dev/null 2>&1 && have=1; echo "git $have"; '
            'for d in "$(pwd -P)" ' + ' '.join(shlex.quote(r) for r in roots) + '; do '
            '[ -d "$d" ] || continue; '
            # Resolved like show-toplevel: MiMo's /testbed is a symlink to /workspace/repo.
            'if [ -e "$d/.git" ]; then echo "gitdir $(cd "$d" && pwd -P)"; fi; '
            'if [ $have = 1 ]; then t=$(cd "$d" && ' + GIT + ' rev-parse --show-toplevel 2>/dev/null) && echo "repo $t"; fi; '
            'done; true')


def history_command(repository):
    q = shlex.quote(repository)
    # Count inside the container: an unpruned remote can hold hundreds of thousands of objects.
    limit = ('| awk -v T="$T" -v P=%s \'{if ($3 > T) {n++; if (n <= %d) print} else {o++; if (o <= %d) print}} '
             'END{print P "_count", n+0, o+0}\'')
    return (
        f'cd {q} || exit 3; '
        # An unborn branch (no commit, files only staged; MiMo 000474) has no HEAD to compare
        # with: every commit in it is outside the agent's state, so T=0 counts each as newer.
        f'N="--not HEAD"; if {GIT} rev-parse -q --verify HEAD >/dev/null; then '
        f'T=$({GIT} log -1 --format=%ct HEAD) || exit 4; echo "head $({GIT} rev-parse HEAD) $T"; '
        f'else b=$({GIT} symbolic-ref -q HEAD) || exit 4; T=0; N=; echo "unborn $b"; fi; '
        f'out=$({GIT} fsck --unreachable --no-reflogs --connectivity-only 2>&1); st=$?; '
        f'if [ $st = 129 ]; then out=$({GIT} fsck --unreachable --no-reflogs 2>&1); st=$?; fi; '
        'echo "fsck_exit $st"; '
        # Without any ref (unborn branch) git labels the same objects "dangling".
        'printf "%s\\n" "$out" | awk \'$1=="unreachable" || $1=="dangling"{u[$2]++; next} {if (++e <= 20) print "fsck_output", $0} '
        'END{for (k in u) print "unreachable", k, u[k]}\'; '
        # git log --stdin with no input would log HEAD itself.
        'ids=$(printf "%s\\n" "$out" | awk \'($1=="unreachable" || $1=="dangling") && $2=="commit"{print $3}\'); '
        f'if [ -n "$ids" ]; then printf "%s\\n" $ids | {GIT} log --no-walk --stdin --format="uc %H %ct %s" '
        + limit % ('uc', EXAMPLES, EXAMPLES) + '; else echo "uc_count 0 0"; fi; '
        f'{GIT} log --all $N --format="na %H %ct %s" ' + limit % ('na', EXAMPLES, EXAMPLES) + '; '
        f'{GIT} log --reflog $N --format="rl %H %ct %s" 2>/dev/null ' + limit % ('rl', EXAMPLES, EXAMPLES) + '; true')


def mtime_command(repository):
    q = shlex.quote(repository)
    # Files whose content differs from HEAD were changed by a build step on purpose and
    # are newer for that reason; only clean files can betray which ones a patch touched.
    analyze = (
        "awk -v GAP=%d -v MAX=%d '"
        '$1=="N"{print; next} '
        '{k=$1; t=$2+0; n=substr($0, length($1)+length($2)+3); c[k]++; i=c[k]; tm[k,i]=t; nm[k,i]=n} '
        'END{for (k in c) { g=1; gs[k,1]=1; size[k,1]=1; '
        'for (i=2; i<=c[k]; i++) { if (tm[k,i]-tm[k,i-1] > GAP) { g++; gs[k,g]=i; size[k,g]=0 } size[k,g]++ } '
        'b=1; for (j=2; j<=g; j++) if (size[k,j] > size[k,b]) b=j; '
        'end=(b<g)?gs[k,b+1]-1:c[k]; later=c[k]-end; earlier=gs[k,b]-1; '
        'print "S", k, c[k], g, tm[k,1], tm[k,c[k]], tm[k,gs[k,b]], tm[k,end], earlier, later; '
        'shown=0; for (i=end+1; i<=c[k] && shown<MAX; i++) { print "L", k, tm[k,i], nm[k,i]; shown++ } } }\''
        % (MTIME_GAP_SECONDS, MTIME_EXAMPLES))
    return (
        f'cd {q} || exit 3; GIT_OPTIONAL_LOCKS=0; export GIT_OPTIONAL_LOCKS; '
        f'{{ {GIT} diff --name-only -z HEAD -- 2>/dev/null | tr "\\0" "\\n" | sed "s/^/D /"; '
        f'{GIT} ls-files -z | xargs -0 stat -c "F %Y %n" 2>/dev/null; '
        f'{GIT} ls-tree -r -d -z --name-only HEAD | xargs -0 stat -c "R %Y %n" 2>/dev/null; }} | '
        "awk '$1==\"D\"{d[substr($0,3)]=1; next} "
        '{n=substr($0, length($1)+length($2)+3)} $1=="F" && (n in d){dirty++; next} {print} '
        "END{print \"N\", dirty+0}' | sort -k2,2n | " + analyze)


def parse_history(output):
    repo = {'unreachable': {}}
    examples = {'uc': [], 'na': [], 'rl': []}
    counts, fsck_output = {}, []
    for line in output.splitlines():
        kind, _, rest = line.partition(' ')
        if kind == 'head':
            repo['head'], repo['head_time'] = rest.split()[0], int(rest.split()[1])
        elif kind == 'unborn':
            repo['head'], repo['head_time'], repo['unborn'] = None, 0, rest
        elif kind == 'fsck_exit':
            repo['fsck_exit'] = int(rest)
        elif kind == 'unreachable':
            object_type, number = rest.split()
            repo['unreachable'][object_type] = int(number)
        elif kind == 'fsck_output':
            fsck_output.append(rest)
        elif kind in examples:
            sha, seconds, *subject = rest.split(' ', 2)
            examples[kind].append({'commit': sha, 'time': int(seconds), 'subject': (subject or [''])[0][:200]})
        elif kind.endswith('_count') and kind[:-6] in examples:
            counts[kind[:-6]] = tuple(map(int, rest.split()))
    if 'head_time' not in repo:
        return repo
    newer = lambda kind: counts.get(kind, (0, 0))[0]
    older = lambda kind: counts.get(kind, (0, 0))[1]
    ordered = lambda kind: sorted(examples[kind], key=lambda c: c['time'] <= repo['head_time'])
    repo.update(
        unreachable_commits_newer_than_head=newer('uc'), unreachable_commits=ordered('uc')[:EXAMPLES],
        non_ancestor_commits_newer_than_head=newer('na'), non_ancestor_commits_older=older('na'),
        non_ancestor_commits=ordered('na')[:EXAMPLES],
        reflog_commits_outside_head=newer('rl') + older('rl'), reflog_commits=ordered('rl')[:EXAMPLES])
    if repo.get('fsck_exit'):
        repo['fsck_output'] = fsck_output
    return repo


def parse_mtimes(output):
    groups, later = {}, {'F': [], 'R': []}
    dirty = 0
    for line in output.splitlines():
        kind, _, rest = line.partition(' ')
        if kind == 'N':
            dirty = int(rest)
        elif kind == 'S':
            k, *values = rest.split()
            total, count, first, last, bulk_start, bulk_end, earlier, after = map(int, values)
            groups[k] = {'count': total, 'groups': count, 'spread_seconds': last - first,
                         'bulk': {'start': bulk_start, 'end': bulk_end, 'count': total - earlier - after},
                         'earlier': earlier, 'later': after}
        elif kind == 'L':
            k, seconds, path = rest.split(' ', 2)
            later[k].append({'path': path, 'mtime': int(seconds)})
    files = groups.get('F', {'count': 0, 'groups': 0, 'later': 0})
    files['later_examples'] = later['F']
    files['dirty_excluded'] = dirty
    directories = groups.get('R', {'count': 0, 'groups': 0, 'later': 0})
    directories['later_examples'] = later['R'][:EXAMPLES]
    return {'files': files, 'directories': directories}


def mtime_leak(files):
    return 0 < files.get('later', 0) <= MTIME_MINORITY * files['count']


async def inspect_leaks(environment, roots=REPOSITORY_ROOTS):
    """Look for the fix where the agent can read it; reading only, nothing is changed."""
    import json
    repair = None
    report = await environment.exec(f'cat {REPAIR_REPORT} 2>/dev/null', timeout_sec=30)
    if report.return_code == 0 and (report.stdout or '').strip():
        try:
            repair = json.loads(report.stdout)
        except ValueError:
            repair = {'unreadable': (report.stdout or '')[:1000]}
    repaired = bool((repair or {}).get('version'))
    found = await environment.exec(repositories_command(roots), timeout_sec=30)
    lines = [line.split(' ', 1) for line in (found.stdout or '').splitlines() if ' ' in line]
    have_git = ['git', '1'] in lines
    repositories = sorted({value for kind, value in lines if kind == 'repo' and value.startswith('/')})
    gitdirs = sorted({value for kind, value in lines if kind == 'gitdir'})
    uncovered = [d for d in gitdirs if not any(d == r or d.startswith(r.rstrip('/') + '/') for r in repositories)]
    history = {'check': 'repository-history-leak', 'repositories': {}}
    mtimes = {'check': 'repository-mtime-leak', 'repositories': {},
              'gap_seconds': MTIME_GAP_SECONDS, 'minority_fraction': MTIME_MINORITY}
    if found.return_code != 0:
        reason = 'repository discovery failed: ' + (found.stderr or '')[-500:]
        history.update(status='error', reason=reason)
        mtimes.update(status='error', reason=reason)
    elif not have_git:
        # Decided 2026-10-17: without git the checks are skipped, not an error.
        history.update(status='not_checked', reason='no git binary in the image', gitdirs=gitdirs)
        mtimes.update(status='not_checked', reason='no git binary in the image', gitdirs=gitdirs)
    elif uncovered:
        reason = (f'.git present but not a readable repository ({", ".join(uncovered)}); '
                  'cannot certify, an agent can still read the objects')
        history.update(status='error', reason=reason, uncovered=uncovered)
        mtimes.update(status='error', reason=reason, uncovered=uncovered)
    elif not repositories:
        history.update(status='not_checked', reason='no git repository at the working directory or the usual roots')
        mtimes.update(status='not_checked', reason=history['reason'])
    else:
        problems, errors = [], []
        for repository in repositories:
            result = await environment.exec(history_command(repository), timeout_sec=LEAK_TIMEOUT_SEC)
            repo = parse_history(result.stdout or '')
            history['repositories'][repository] = repo
            if result.return_code != 0 or 'head_time' not in repo:
                errors.append(f'{repository}: history not readable (exit {result.return_code}) '
                              + (result.stderr or '')[-300:])
                continue
            found_here = []
            if repo['unreachable'].get('commit'):
                found_here.append(f"{repo['unreachable']['commit']} unreachable commits "
                                  f"({repo['unreachable_commits_newer_than_head']} newer than HEAD)")
            if repo['unreachable'].get('blob'):
                found_here.append(f"{repo['unreachable']['blob']} unreachable blobs")
            if repo['non_ancestor_commits_newer_than_head']:
                found_here.append(f"{repo['non_ancestor_commits_newer_than_head']} commits on other refs newer than HEAD")
            if found_here:
                problems.append(f'{repository}: ' + ', '.join(found_here))
            elif repo.get('fsck_exit'):
                errors.append(f"{repository}: git fsck exited {repo['fsck_exit']}")
        history.update(
            status='failed' if problems else 'error' if errors else 'passed',
            reason=('; '.join(problems) if problems else '; '.join(errors) if errors else
                    'no unreachable commits or blobs and no newer commits outside HEAD'),
            **({'errors': errors} if errors and problems else {}))
        problems, errors = [], []
        for repository in repositories:
            if history['repositories'][repository].get('unborn'):
                # No commit to compare file times with; the history check covers this repository.
                mtimes['repositories'][repository] = {'status': 'not_checked', 'reason': 'unborn branch, no commit'}
                continue
            result = await environment.exec(mtime_command(repository), timeout_sec=LEAK_TIMEOUT_SEC)
            if result.return_code != 0:
                errors.append(f'{repository}: mtimes not readable (exit {result.return_code}) '
                              + (result.stderr or '')[-300:])
                continue
            repo = parse_mtimes(result.stdout or '')
            mtimes['repositories'][repository] = repo
            if mtime_leak(repo['files']):
                problems.append(f"{repository}: {repo['files']['later']} of {repo['files']['count']} clean tracked "
                                f"files are more than {MTIME_GAP_SECONDS} s newer than the rest")
        mtimes.update(
            status='failed' if problems else 'error' if errors else 'passed',
            reason=('; '.join(problems) if problems else '; '.join(errors) if errors else
                    'clean tracked files share one modification-time group'),
            **({'errors': errors} if errors and problems else {}))
        if repaired and mtimes['status'] != 'passed':
            # Decided 2026-10-17: the repair always normalizes mtimes and its report holds the
            # flagged-and-fixed count; afterwards this is an observation, never a verdict.
            mtimes.update(status='passed' if problems else 'not_checked', observation=mtimes['reason'],
                          reason='observation only after the automatic repair (mtimes normalized at build time): '
                                 + mtimes['reason'])
    checks = [history, mtimes]
    status = ('failed' if any(c['status'] == 'failed' for c in checks) else
              'error' if any(c['status'] == 'error' for c in checks) else
              'not_checked' if all(c['status'] == 'not_checked' for c in checks) else 'passed')
    result = {'status': status, 'checks': checks}
    if repair is not None:
        result['repair'] = repair
    return result


def leak_verdict(entry):
    """(status, reason) of an agent environment's leak checks, or None if they did not run."""
    leaks = entry.get('leaks')
    if not leaks or leaks.get('status') not in ('failed', 'error'):
        return None
    bad = [c for c in leaks['checks'] if c['status'] == leaks['status']]
    if leaks['status'] == 'failed':
        # Still archived: the agent could exploit it. Typical cause: task setup or the
        # healthcheck rewrites the repository at container start, after the build step.
        prefix = f'environment leak: {AFTER_REPAIR}; ' if (leaks.get('repair') or {}).get('version') else 'environment leak: '
        return 'failed', prefix + '; '.join(f"{c['check']}: {c['reason']}" for c in bad)
    return 'error', 'environment leak check could not run: ' + '; '.join(f"{c['check']}: {c['reason']}" for c in bad)


# Keep targets fixed and recorded in the implementation contract; no task content is sent.
TARGETS = ('https://example.com/', 'https://www.cloudflare.com/')


def declared_policy(task, label):
    raw = tomllib.loads(task.paths.config_path.read_text()) if hasattr(task.paths, 'config_path') else tomllib.loads((task.paths.task_dir / 'task.toml').read_text())
    env = raw.get('environment', {})
    if label != 'agent':
        env = raw.get('verifier', {}).get('environment', env)
        if label.startswith('verifier-'):
            step = next((s for s in raw.get('steps', []) if s.get('name') == label[len('verifier-'):]), {})
            env = step.get('verifier', {}).get('environment', env)
    value = env.get('allow_internet')
    if value is not None and not isinstance(value, bool):
        raise ValueError('allow_internet must be a boolean')
    return value


async def inspect_network(environment, expected):
    observations = []
    for direct in (False, True):
        urls = ('https://1.1.1.1/',) if direct else TARGETS
        for url in urls:
            # curl/wget exit zero even for HTTP error responses: connectivity is what matters.
            command = ('if command -v curl >/dev/null 2>&1; then '
                + ('HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= https_proxy= http_proxy= all_proxy= ' if direct else '')
                + 'curl -sS -o /dev/null --connect-timeout 3 --max-time 5 '
                + ('--noproxy "*" ' if direct else '') + shlex.quote(url)
                + '; elif command -v python3 >/dev/null 2>&1; then python3 -c ')
            script = ('import urllib.request,urllib.error,sys\n'
                + ('opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))\n' if direct else 'opener=urllib.request.build_opener()\n')
                + 'try:\n opener.open(' + repr(url) + ',timeout=5).close()\n'
                + 'except urllib.error.HTTPError:\n pass\nexcept Exception as e:\n print(type(e).__name__);sys.exit(1)\n')
            command += shlex.quote(script) + '; else exit 125; fi'
            result = await environment.exec(command, timeout_sec=15)
            observations.append({'url': url, 'route': 'direct' if direct else 'configured-proxy-or-direct',
                'exit_code': result.return_code, 'reachable': result.return_code == 0,
                'client_available': result.return_code != 125,
                'stderr': (result.stderr or '')[:1000]})
    reachable = any(o['reachable'] for o in observations)
    capable = all(o['client_available'] for o in observations)
    status = 'not_checked' if expected is None else ('error' if not capable else ('passed' if reachable == expected else 'failed'))
    return {'check': 'declared-network-egress', 'status': status, 'expected_allow_internet': expected,
        'observed_any_reachable': reachable, 'observations': observations,
        'reason': 'No explicit allow_internet declaration; observations only.' if expected is None else
                  'Requires curl or python3; no tools are installed by validation.' if not capable else
                  'Compare observed reachability with declared policy; endpoint outages can cause false negatives.'}
