"""Stage 3: tools in the agent's container that grading would have run.

In shared mode the agent is root in the container that grades it, so any program
grading runs from that container could have been replaced. After a no-op agent phase
this rule wraps common tools in the container, in one no-op run per task; the others
stay a clean baseline. A wrapper records that it ran and then
hands over to the real tool, so grading behaves as usual. A recorded tool is one an
agent could have used to fake the reward; a task on a clean runtime records nothing.

The rule records instead of writing a reward: the verifier may overwrite a planted
reward, and which reward file it reads differs by task. bash, sh and coreutils are
not wrapped, because grading starts through them on every task (known limit).
"""
import json
from contextvars import ContextVar
import shlex

HITS = 'verifier-tamper-hits.txt'           # under /logs/verifier, collected with the trial
EVIDENCE = 'verifier-tamper.json'
active = ContextVar('verifier_tamper', default=None)   # the tasks already wrapped, or None when disabled
TOOLS = ('python', 'python2', 'python3', 'python3.*', 'pypy3', 'pip', 'pip3', 'pytest', 'py.test',
         'uv', 'uvx', 'tox', 'git', 'make', 'cmake', 'ctest', 'gcc', 'g++', 'node', 'npm', 'npx', 'yarn', 'pnpm',
         'go', 'cargo', 'rustc', 'java', 'javac', 'mvn', 'gradle', 'perl', 'ruby', 'php', 'R', 'Rscript', 'activate')
ROOTS = ('/usr/local/bin', '/usr/bin', '/opt/*/bin', '/opt/*/envs/*/bin', '/root/.local/bin', '/root/.cargo/bin',
         '/go/bin', '/usr/local/go/bin', '/usr/local/cargo/bin')


def wrap_command(tools=TOOLS, roots=ROOTS, hits='/logs/verifier/' + HITS):
    """Shell that wraps every matching tool, in place, and prints each wrapped path."""
    names = ' '.join(shlex.quote(name) for name in tools)
    # Roots stay unquoted so that the shell expands their globs; $PATH adds any others.
    return f'''
hits={shlex.quote(hits)}
dirs=$(printf '%s\\n' {' '.join(roots)} $(printf '%s' "$PATH" | tr ':' ' ') | sort -u)
for dir in $dirs; do
  [ -d "$dir" ] || continue
  for pattern in {names}; do
    for tool in "$dir"/$pattern; do
      [ -f "$tool" ] || continue
      name=${{tool##*/}}
      case "$name" in *.ot-real) continue;; esac
      real="$tool.ot-real"
      [ -e "$real" ] && continue
      mv "$tool" "$real"
      case "$name" in
        activate) {{ printf 'mkdir -p %s; echo %s >> %s\\n' "${{hits%/*}}" "$tool" "$hits"; cat "$real"; }} > "$tool";;
        *) printf '#!/bin/sh\\nmkdir -p %s\\necho %s >> %s\\nexec %s "$@"\\n' "${{hits%/*}}" "$tool" "$hits" "$real" > "$tool"
           chmod +x "$tool";;
      esac
      echo "$tool"
    done
  done
done
'''


def claim(task):
    """True for the first trial of a task in this no-op job, the one to wrap."""
    wrapped = active.get()
    if wrapped is None or task in wrapped:
        return False
    wrapped.add(task)
    return True


def install():
    """Wrap the tools after one no-op agent phase per task, before the verifier."""
    from harbor.trial.trial import Trial
    if getattr(Trial, '_verifier_tamper', False):
        return
    original = Trial._run_agent_phase

    async def agent(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if claim(self.task.paths.task_dir.name):
            record = dict(status='incomplete')
            try:
                observed = await self.agent_environment.exec(wrap_command(), timeout_sec=120, user='root')
                record.update(return_code=observed.return_code, wrapped=(observed.stdout or '').split(),
                              stderr=observed.stderr or '')
                record['status'] = 'applied' if observed.return_code == 0 else 'incomplete'
            except Exception as exc:
                record['error'] = f'{type(exc).__name__}: {exc}'
            (self.paths.trial_dir / EVIDENCE).write_text(json.dumps(record, indent=2) + '\n')
        return result

    Trial._run_agent_phase = agent
    Trial._verifier_tamper = True


def verdict(evidence, hits, reward, baseline_rewards):
    """Flag tools grading ran. Without an applied wrap or the usual reward nothing is proven."""
    if evidence is None or evidence.get('status') != 'applied':
        return dict(status='incomplete', reason='the tools were not wrapped', tools=[])
    if not evidence.get('wrapped'):
        return dict(status='incomplete', reason='none of the watched tools was found', tools=[])
    if reward is None or reward not in baseline_rewards:
        return dict(status='incomplete', reason=f'reward {reward} differs from the no-op runs', tools=[])
    tools = sorted(set(hits))
    if tools:
        return dict(status='flagged', reason='grading ran tools the agent could have replaced', tools=tools)
    return dict(status='passed', reason='grading ran none of the wrapped tools', tools=[])


def assess(trial_dir, reward, baseline_rewards):
    """The verdict for the wrapped trial."""
    evidence = trial_dir / EVIDENCE
    hits = trial_dir / 'verifier' / HITS
    return verdict(json.loads(evidence.read_text()) if evidence.is_file() else None,
                   hits.read_text().split() if hits.is_file() else [], reward, baseline_rewards)
