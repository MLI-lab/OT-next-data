"""FACET repairs: what we learned from running tasks and checks, not what Apptainer needs.

patch.py alone converts FACET to tasks that build and run on Apptainer; `patch.py --no-repairs`
stops there, so the validation rules can be measured on tasks they have not already shaped.
Everything here came from a no-op, reference or rule result.

CHECK_EXCLUSIONS  Tasks archived from reference and rule results.
Missing tools, measured limits, the verifier's early exit, the instruction's time sentence and
the path resolver's rewrites (instruction-paths.json) are applied to the tasks that stay.
"""
import json
from pathlib import Path
import tomllib

from validation.rules.static.instruction_suffix import suffix

DOCKERFILE = "environment/Dockerfile"
INSTRUCTION = "instruction.md"
BUILD_EXCLUSIONS = frozenset(("upstream-image-build-failure", "backend_build_ownership"))
INSTRUCTION_PATHS = Path(__file__).with_name("instruction-paths.json")


def check_exclusions(exclusions):
    """Archived tasks that a build did not rule out; --no-repairs keeps them."""
    return frozenset(task_id for task_id, decision in exclusions.items()
                     if decision["category"] not in BUILD_EXCLUSIONS)


def instruction_paths():
    return json.loads(INSTRUCTION_PATHS.read_text()) if INSTRUCTION_PATHS.is_file() else {}


def repaired_instruction(files, task_id, paths):
    """The path resolver's reviewed line rewrites, then the time sentence."""
    lines = files[INSTRUCTION].decode("utf-8").split("\n")
    for number, old, new in paths.get(task_id, ()):
        if lines[number] != old:
            raise ValueError(f"{task_id}: instruction line {number} changed; review the path rewrite again")
        lines[number] = new
    result = dict(files)
    result[INSTRUCTION] = suffix("\n".join(lines).encode("utf-8"), files["task.toml"])
    return result


# Reviewed 2026-10-16 reference failures: the task's own scripts call a tool the
# image never installs, so they fail under Docker too.
#   task_001011: bare ubuntu:22.04; solve.sh runs `pip install pyyaml`, then python3.
#   task_003697: test.sh runs `python -m pytest`; the image has only python3.
# Reviewed 2026-10-17 (reference job 1038707, all bare ubuntu:22.04 images):
#   task_000701, task_000814, task_001579, task_002463, task_004655, task_006478,
#   task_006651, task_007718: solve.sh runs python3 ("python3: command not found").
#   task_002689: solve.sh loads the R package jsonlite; its CRAN install fails and the
#     image has none ("there is no package called 'jsonlite'"); Debian's r-cran-jsonlite.
#   task_003456: solve.sh falls back to `strings` (binutils), which the image lacks.
PYTHON_TOOLS = ('python3', 'python3-pip')
MISSING_TOOLS = {'task_001011': PYTHON_TOOLS, 'task_003697': ('python-is-python3',),
                 **{task_id: PYTHON_TOOLS for task_id in (
                     'task_000701', 'task_000814', 'task_001579', 'task_002463',
                     'task_004655', 'task_006478', 'task_006651', 'task_007718')},
                 'task_002689': ('r-cran-jsonlite',), 'task_003456': ('binutils',)}
MISSING_TOOLS_MARKER = '# FACET install tools the task scripts call'


def install_missing_tools(files, task_id):
    """Install, right after the base image, packages the task's scripts need but never install."""
    packages = MISSING_TOOLS.get(task_id)
    if not packages:
        return files
    text = files[DOCKERFILE].decode('utf-8')
    if MISSING_TOOLS_MARKER in text:
        return files
    lines = text.splitlines(keepends=True)
    bases = [i for i, line in enumerate(lines) if line.strip().upper().startswith('FROM ')]
    if len(bases) != 1 or lines[bases[0]].strip() != 'FROM ubuntu:22.04':
        raise ValueError(f'{task_id}: review changed base image before installing missing tools')
    step = (MISSING_TOOLS_MARKER + '\nRUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y '
            '--no-install-recommends ' + ' '.join(packages) + ' && rm -rf /var/lib/apt/lists/*\n')
    result = dict(files)
    result[DOCKERFILE] = (''.join(lines[:bases[0] + 1]) + step + ''.join(lines[bases[0] + 1:])).encode('utf-8')
    return result


# Measured 2026-10-17 (job 1039989, /hnvme/workspace/y500bb12-optiagent/facet-validation-20261017/measure5):
# each reference ran once with 4 CPUs, 8192 MiB and 480 s; all five passed. Values from the trial's
# `resources` (cgroup memory.peak, cpu.stat usage, step lifetime). Upstream limits were 1 CPU,
# 2048 MiB, agent 120 s; reference job 1038707 hit the agent timeout (000536, 002890, 004333, 004857)
# or the memory limit (004399). New memory = ceil(peak GiB) + 1 GiB; new agent timeout = 2 x
# max(step seconds, CPU seconds / new CPUs), rounded up to a minute and never below 120 s.
#   task      peak MiB  CPU s   step s  CPU s/wall  -> cpus  memory_mb  agent timeout_sec
#   000536       934.6  1791.3   462.5    3.9            2       2048       1800
#   002890      2149.7    91.2    66.0    1.8            2       4096        180
#   004333      2237.2    93.4    66.0    1.8            2       4096        180
#   004857      1675.3   181.1    66.0    3.5            2       4096        240
#   004399      4882.8    30.9    31.2    1.0            1       8192        120 (unchanged)
# Memory is then rounded up to a size check-resource-sizes allows (1, 2, 4, 8, 16 or 32 GiB):
# 004857 3072 -> 4096 MiB, 004399 6144 -> 8192 MiB (full run 1040937 failed that check).
#
# Measured 2026-10-17 (job 1044307, .../facet-validation-20261017/measure2), same method: both
# references hit the 120 s agent timeout in rerun 1044061 and pass with 4 CPUs, 8192 MiB, 480 s.
#   task      peak MiB  CPU s   step s  CPU s/wall  -> cpus  memory_mb  agent timeout_sec
#   000733       606.9   212.1    68.8    3.1            2       2048        240
#   002085      2342.9   117.3    68.9    1.7            2       4096        180
RESOURCE_LIMITS = {
    'task_000536': {'cpus': 2, 'memory_mb': 2048, 'agent_timeout_sec': 1800},
    'task_002890': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 180},
    'task_004333': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 180},
    'task_004857': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 240},
    'task_004399': {'cpus': 1, 'memory_mb': 8192, 'agent_timeout_sec': 120},
    'task_000733': {'cpus': 2, 'memory_mb': 2048, 'agent_timeout_sec': 240},
    'task_002085': {'cpus': 2, 'memory_mb': 4096, 'agent_timeout_sec': 180},
}
UPSTREAM_LIMITS = ('\n[agent]\ntimeout_sec = 120.0\n', '\ncpus = 1\n', '\nmemory_mb = 2048\n')


# Reviewed 2026-10-17 (full run 1040937 and rerun 1044061): task_006168's test.sh writes reward 0 and
# exits before pytest when outputs are missing, so the no-op never shows that the tests ran
# ("verifier execution evidence is missing" in 4 of 4 attempts). Without the shortcut the tests fail
# on the missing outputs themselves; rewards are unchanged.
VERIFIER_PRECHECKS = {'task_006168': (
    "\n# Fast pre-check: if the solution didn't run, required outputs won't exist.\n"
    "# Fail immediately to save time and ensure no-op gets reward 0.\n"
    'REQUIRED_OUTPUTS=(\n  "/task_file/output/dashboard_spec.json"\n  "/task_file/output/deployment_manifest.json"\n'
    '  "/task_file/output/content_bundle.json"\n  "/task_file/clipboard/preview_url.txt"\n)\n'
    'for f in "${REQUIRED_OUTPUTS[@]}"; do\n  if [ ! -f "$f" ] || [ ! -s "$f" ]; then\n'
    '    echo "ERROR: Required output missing or empty: $f"\n    echo 0 > /logs/verifier/reward.txt\n'
    '    exit 0\n  fi\ndone\n')}


def remove_verifier_precheck(files, task_id):
    """Drop a reviewed early exit that skips the test suite."""
    block = VERIFIER_PRECHECKS.get(task_id)
    if block is None or 'tests/test.sh' not in files:
        return files
    text = files['tests/test.sh'].decode('utf-8')
    if block not in text:
        if 'Fast pre-check' in text:
            raise ValueError(f'{task_id}: review the verifier pre-check again')
        return files
    result = dict(files)
    result['tests/test.sh'] = text.replace(block, '', 1).encode('utf-8')
    return result


def measured_resource_limits(files, task_id):
    """Apply the reviewed measured limits of one task to its task.toml."""
    limits = RESOURCE_LIMITS.get(task_id)
    if limits is None:
        return files
    text = files['task.toml'].decode('utf-8')
    replacements = (f"\n[agent]\ntimeout_sec = {float(limits['agent_timeout_sec'])}\n",
                    f"\ncpus = {limits['cpus']}\n", f"\nmemory_mb = {limits['memory_mb']}\n")
    for old, new in zip(UPSTREAM_LIMITS, replacements):
        if text.count(old) != 1:
            raise ValueError(f'{task_id}: upstream resource limits changed; measure again')
        text = text.replace(old, new)
    parsed = tomllib.loads(text)
    if (parsed['agent']['timeout_sec'], parsed['environment']['cpus'], parsed['environment']['memory_mb']) != (
            limits['agent_timeout_sec'], limits['cpus'], limits['memory_mb']):
        raise ValueError(f'{task_id}: resource limits were not applied')
    result = dict(files)
    result['task.toml'] = text.encode('utf-8')
    return result
