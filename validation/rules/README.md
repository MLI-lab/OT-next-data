# Validation rules

A rule flags a task with a concrete problem. **Static** rules read only the task files.
**Dynamic** rules need an image, a container or a run. `registry.py` lists every rule with its kind,
origin (`TB`, `TB patched`, `ours`), whether it fails a task or only warns, and where its code lives.
A rule is one file, or a folder when it has several parts. An automatic repair sits next to the rule
that triggers it.

```
rules/
  registry.py
  static/
    terminal_bench.py        runs the pinned Terminal-Bench checks (validation/upstream/terminal_bench)
    task_absolute_path/      check.py (our patch), repair.py, instruction_paths.py, path_cache.py
    pip_pinning/             check.py (our patch), repair.py
    test_file_references/    check.py (our patch), supplied_references.py
    nproc.py  dockerfile_references.py  dockerfile_sanity.py      our patches
    instruction_suffix.py    repair for the instruction-suffix check
    task_toml_schema.py  do_not_modify/  no_behavioural_tests/  python_missing_imports.py
    artifact_efficiency.py   our rules
  dynamic/
    do_not_modify/           stage.py (stage 2), check.py, protected_paths.py, mutations.py
    reference_leaks.py
```

## Terminal-Bench rules

For what each upstream check does, see the
[Terminal-Bench task review automation](../upstream/terminal_bench/docs/TASK_REVIEW_AUTOMATION.md). We run them unchanged, except
for the checks below, where our patch changes only what the upstream script sees, never the task:

- **task-absolute-path** (warning): ignores code blocks, URLs and paths whose location the
  instruction states. The repair searches the running container for the referenced file and writes its
  absolute path into the instruction only when exactly one file matches.
- **pip-pinning** (warning): accepts hash-pinned URLs and ignores generated offline installers. The
  repair pins each flagged package to the version a successful reference-solution run installed.
- **test-file-references**: fixes the upstream extractor and ignores URLs, regular expressions,
  archive members and files the task's own setup scripts create.
- **nproc**: allows `nproc` used only for measurement, plus audited cases listed in the file.
- **dockerfile-references**, **dockerfile-sanity**: ignore comments; look only at apt arguments.

Excluded checks and the reasons are in `TRAINING_EXCLUSIONS` in `static/terminal_bench.py`.

## Our rules

- **task-toml-schema**: `task.toml` must validate against the pinned Harbor models, with no unknown fields.
- **do_not_modify**: the instruction says a file must not be modified, but the verifier never mentions
  that file. The dynamic part (on by default) checks that the file exists in the container and, when a
  reference solution exists, changes the file after the reference ran and expects the verifier to fail.
- **no_behavioural_tests**: To filter tasks where grading only checks the submitted code's text,
  without testing what that code does, e.g., only checking if a keyword/code fragment exists. We flag
  those because these verifiers may be easy to game or may not accept other valid solutions.
- **python_missing_imports**: a test file, or a solution file that `solve.sh` runs, does not parse or
  uses `np`, `pd`, `plt`, ... without importing it.
- **artifact_efficiency** (warning): a task file larger than 100 MB, which slows uploads into the
  environment.
- **reference_leaks**: the reference solution's files are visible to the agent in the container.

`do_not_modify`, `no_behavioural_tests`, `python_missing_imports` and `artifact_efficiency` run in
stage 4 as scripts on the task (`static/script.py`): exit 1 is a finding, exit 2 means the rule
itself crashed and is recorded as an error. The dynamic part of `do_not_modify` runs in stage 2
(`dynamic/do_not_modify/stage.py`, on by default): each reference run first records which protected
files exist; an existing file the verifier never mentions flags the task; otherwise, after all
reference runs scored 1, the reference is rerun once per change of each protected file, and a
verifier that still scores 1 flags the task. Development history and evaluations are in
`~/rule-development/` (outside this repository).

## Running rules on a dataset

```bash
python -m validation.rules TASKS --out DIR                      # all rules
python -m validation.rules TASKS --out DIR --rules static       # static rules only, no containers
python -m validation.rules TASKS --out DIR --rules nproc,no_behavioural_tests
python -m validation.rules TASKS --out DIR --rules dynamic -- --submit helma --cpus 48
```

Rule names are those in `registry.py`. Dynamic rules run as a normal validation run of the
container stages; arguments after `--` go to `validation/run.py`.
