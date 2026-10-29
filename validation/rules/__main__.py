"""Run validation rules on a dataset: all of them by default, or only static, dynamic or named rules.

  python -m validation.rules TASKS --out DIR                          all rules
  python -m validation.rules TASKS --out DIR --rules static            static rules, here and now
  python -m validation.rules TASKS --out DIR --rules nproc,no_behavioural_tests
  python -m validation.rules TASKS --out DIR --rules dynamic -- --submit helma --cpus 48

TASKS is a task directory, a directory of tasks or a TaskTrove Parquet. Static rules only read
task files and run right away without containers. Dynamic rules need images and containers, so
they run as a normal validation run of the container stages (build, reference, no-op) through
validation/run.py; arguments after `--` go to it (cluster, resources, contract). The results are
the same as in a full pipeline run.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from validation.rules.registry import RULES  # noqa: E402


def check_name(rule):
    """The static runner's check name for a static rule."""
    from validation.rules.static.terminal_bench import LOCAL_CHECKS
    local = f'check-{rule.name.replace("_", "-")}.py'
    return local if local in LOCAL_CHECKS else f'check-{rule.name}.sh'


def selection(value):
    static = [r for r in RULES if r.kind == 'static' and r.status == 'active']
    dynamic = [r for r in RULES if r.kind == 'dynamic']
    if value == 'all':
        return static, dynamic
    if value in ('static', 'dynamic'):
        return (static, []) if value == 'static' else ([], dynamic)
    names = [n.strip() for n in value.split(',') if n.strip()]
    known = {r.name: r for r in static + dynamic}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit(f'unknown rule(s): {", ".join(unknown)}; see validation/rules/registry.py')
    chosen = [known[n] for n in names]
    return [r for r in chosen if r.kind == 'static'], [r for r in chosen if r.kind == 'dynamic']


def run_static(tasks, rules, out, profile):
    from validation.data.materialize import materialize, parquet_files
    from validation.data.selection import discover_tasks
    from validation.rules.static.terminal_bench import load_checks, run_checks
    _, selected, _ = load_checks(profile)
    wanted = {check_name(r) for r in rules}
    exclude = [f'{name}=not selected with --rules' for name in selected if name not in wanted]
    with tempfile.TemporaryDirectory(prefix='rules-') as scratch:
        if parquet_files(tasks):
            materialize(tasks, Path(scratch) / 'tasks')
            tasks = Path(scratch)
        return run_checks(discover_tasks(tasks), out / 'static', profile=profile, exclude=exclude)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('tasks', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--rules', default='all', help="'all' (default), 'static', 'dynamic' or rule names from registry.py")
    ap.add_argument('--static-profile', default='training')
    argv = sys.argv[1:]
    # Arguments after `--` are for validation/run.py (dynamic rules).
    run_args = argv[argv.index('--') + 1:] if '--' in argv else []
    a = ap.parse_args(argv[:argv.index('--')] if '--' in argv else argv)
    static, dynamic = selection(a.rules)
    code = 0
    if static:
        print(f'Static rules: {", ".join(r.name for r in static)}', flush=True)
        code = run_static(a.tasks, static, a.out, a.static_profile)
    if dynamic:
        from validation.stages.layout import NOP, REFERENCE
        stages = ','.join(str(n) for n in sorted({1, REFERENCE, NOP}))
        extra = list(run_args)
        names = {r.name for r in dynamic}
        if 'do_not_modify_dynamic' not in names:
            extra.append('--no-dynamic-do-not-modify')
        command = [sys.executable, str(ROOT / 'validation/run.py'), str(a.tasks), '--stages', stages,
                   '--out', str(a.out / 'dynamic'), *extra]
        print('Dynamic rules: ' + ', '.join(sorted(names)) + '\n' + ' '.join(command), flush=True)
        code = subprocess.call(command) or code
    return code


if __name__ == '__main__':
    sys.exit(main())
