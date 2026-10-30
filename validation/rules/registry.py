"""Every validation rule: whether it reads task files only or needs a container, where it comes from,
whether it can fail a task, and where its code lives. See README.md in this folder."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    name: str
    kind: str      # 'static' (task files only) or 'dynamic' (needs an image, container or run)
    origin: str    # 'TB' (pinned Terminal-Bench as is), 'TB patched' (our patch here), 'ours'
    severity: str  # 'fail' or 'warning'
    module: str    # our code; TB rules run from validation/upstream/terminal_bench via static/terminal_bench.py
    status: str = 'active'  # 'active' (run by the default training profile) or 'adopted' (not yet run by a stage)
    # Static rules run in stage 4; dynamic rules run where their evidence is available.
    repair: str | None = None  # automatic repair applied before the check


TB = 'validation.rules.static.terminal_bench'
RULES = [
    # Terminal-Bench checks run unchanged.
    Rule('compose-host-binds', 'static', 'TB', 'fail', TB),
    Rule('dockerfile-platform', 'static', 'TB', 'fail', TB),
    Rule('instruction-suffix', 'static', 'TB', 'fail', TB,
         repair='validation.rules.static.instruction_suffix'),
    Rule('no-cheat-dir', 'static', 'TB', 'fail', TB),
    Rule('resource-sizes', 'static', 'TB', 'fail', TB),
    Rule('task-timeout', 'static', 'TB', 'fail', TB),
    # Terminal-Bench checks with our patch.
    Rule('dockerfile-references', 'static', 'TB patched', 'fail', 'validation.rules.static.dockerfile_references'),
    Rule('dockerfile-sanity', 'static', 'TB patched', 'fail', 'validation.rules.static.dockerfile_sanity'),
    Rule('nproc', 'static', 'TB patched', 'fail', 'validation.rules.static.nproc'),
    Rule('pip-pinning', 'static', 'TB patched', 'warning', 'validation.rules.static.pip_pinning.check',
         repair='validation.rules.static.pip_pinning.repair'),
    Rule('task-absolute-path', 'static', 'TB patched', 'warning', 'validation.rules.static.task_absolute_path.check',
         repair='validation.rules.static.task_absolute_path.repair'),
    Rule('test-file-references', 'static', 'TB patched', 'fail', 'validation.rules.static.test_file_references.check'),
    # Our rules.
    Rule('agent_editable_test', 'static', 'ours', 'fail', 'validation.rules.static.agent_editable_test'),
    Rule('task-toml-schema', 'static', 'ours', 'fail', 'validation.rules.static.task_toml_schema'),
    Rule('do_not_modify', 'static', 'ours', 'fail', 'validation.rules.static.do_not_modify.check'),
    Rule('no_behavioural_tests', 'static', 'ours', 'fail', 'validation.rules.static.no_behavioural_tests.check'),
    Rule('python_missing_imports', 'static', 'ours', 'fail', 'validation.rules.static.python_missing_imports'),
    Rule('artifact_efficiency', 'static', 'ours', 'warning', 'validation.rules.static.artifact_efficiency'),
    Rule('do_not_modify_dynamic', 'dynamic', 'ours', 'fail', 'validation.rules.dynamic.do_not_modify.stage'),
    Rule('reference_leaks', 'dynamic', 'ours', 'fail', 'validation.rules.dynamic.reference_leaks'),
    Rule('verifier_tamper', 'dynamic', 'ours', 'warning', 'validation.rules.dynamic.verifier_tamper.stage'),
]
BY_NAME = {r.name: r for r in RULES}
