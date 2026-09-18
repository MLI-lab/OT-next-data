"""Grade gold, re-indented gold, empty, garbage, lone }/;/{, `return null;` and a one-name-changed
near miss for every task in an extracted selection, with each task's own tests/cceval_verifier.py.
Run inside a task image (python 3.10, as in production):
  apptainer exec --no-home --bind DIR:DIR images/build_crosscodeeval-python-*.sif python3 verify/verifier_discrimination.py DIR
where DIR contains tasks/ (extracted tasks/selection1000.tar.gz). Prints reward-1 counts per variant."""
import json, re, sys, tempfile, importlib.util, contextlib, io
from pathlib import Path
root = Path(sys.argv[1]) / 'tasks'
results = {}
for task in sorted(root.iterdir()):
    lang = re.search(r'crosscodeeval-(\w+)-', task.name).group(1)
    spec = importlib.util.spec_from_file_location('v', task / 'tests/cceval_verifier.py'); v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
    gold_file = task / 'tests' / ('expected.txt' if lang == 'java' else 'solution.txt')
    gold = gold_file.read_text()
    ids = v.extract_identifiers(gold, lang)
    near = gold.replace(ids[0], ids[0] + 'Wrong', 1) if ids else gold + ' wrong'
    variants = {'gold': gold, 'gold_reindented': '\n'.join('    ' + l.strip() for l in gold.splitlines()) + '\n',
                'empty': '', 'garbage': '__PILOT_WRONG_ANSWER__();', 'close_brace': '}', 'semicolon': ';',
                'open_brace': '{', 'first_line_of_snippet_task': 'return null;', 'one_name_changed': near}
    prompt = task / 'tests/prompt.txt'
    for name, text in variants.items():
        with tempfile.TemporaryDirectory() as d:
            pred = Path(d) / 'solution.txt'; pred.write_text(text)
            args = ['--language', lang, '--prediction', str(pred), '--reference', str(gold_file), '--out', d + '/out']
            if prompt.exists(): args += ['--prompt', str(prompt)]
            with contextlib.redirect_stdout(io.StringIO()):
                v.main(args)
            r = json.loads((Path(d) / 'out/reward.json').read_text())['reward']
        results.setdefault(name, {}).setdefault(lang, []).append((task.name, r))
summary = {}
for name, per in results.items():
    summary[name] = {lang: sum(r for _, r in lst) for lang, lst in per.items()}
    summary[name]['total'] = sum(summary[name].values())
    summary[name]['reward1_tasks'] = [t for lst in per.values() for t, r in lst if r == 1][:8]
print(json.dumps(summary, indent=1))
