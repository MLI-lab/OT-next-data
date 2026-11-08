"""CalibForge repairs: what we learned from running tasks and checks, not what Apptainer needs.

patch.py alone converts CalibForge to tasks that build and run on Apptainer; `patch.py --no-repairs`
stops there, so the validation rules can be measured on tasks they have not already shaped.
Each item below came from a rule, no-op or review result, or fixes an upstream release defect.

R3  instruction.md fenced block: after a line/segment `cd <abs>`, rewrite `./name` to `<abs>/name`.
R18 Remove the three apt tool-version pins in the reviewed sensor-log debugging task.
C1  Remove ignored agent resource fields from two task.toml files when environment has the same values.
V1  Missing-output assertions; V2 pandas dependency assertion; V3 expected service failures move from
    fixture setup into test assertions. All explicitly authorized by the user.
R19 A reviewed task whose Docker ENTRYPOINT starts the service state the agent must repair gets a
    setup_files/setup.sh that runs that same entrypoint before the agent (the Apptainer runscript
    only runs its arguments). Image, instruction and verifier are unchanged.
CHECK_EXCLUSIONS  Tasks archived from no-op results (missing Python, memory limit).
"""
import hashlib
import json
import re
import shlex
import tomllib

CD_SEGMENT = re.compile(r"^cd\s+(/[^\s'\"$`;&|]*)$")
DOT_SLASH_TOKEN = re.compile(r"(?<![\w./~$-])\./(?=\w)")


APT_TOOL_PIN_TASK = "contrastive_solver-debugging_20260611_112034_004"
APT_TOOL_PINS = (
    "build-essential=12.10ubuntu1",
    "gdb=15.0.50.20240403-0ubuntu1",
    "valgrind=1:3.22.0-0ubuntu3",
)


def unpin_reviewed_apt_tools(task_id, text):
    """R18: only rewrite the reviewed install stanza; reject source drift."""
    if task_id != APT_TOOL_PIN_TASK:
        return text, []
    start = "apt-get install -y --no-install-recommends \\\n"
    end = "    && rm -rf /var/lib/apt/lists/*"
    old = start + ''.join(f"    {pin} \\\n" for pin in APT_TOOL_PINS) + end
    new = start + ''.join(f"    {pin.split('=')[0]} \\\n" for pin in APT_TOOL_PINS) + end
    if old not in text and text.count(new) == 1:
        return text, []
    if text.count(old) != 1:
        raise ValueError(f"{task_id}: reviewed apt tool install stanza changed")
    return text.replace(old, new), list(APT_TOOL_PINS)


REDUNDANT_AGENT_RESOURCE_TASKS = {
    "contrastive_solver-data-processing_20260606_172622_004",
    "contrastive_solver-software-engineering_20260603_181444_003",
}

def remove_redundant_agent_resources(task_id, text):
    """Remove ignored resource keys only when the environment already agrees."""
    if task_id not in REDUNDANT_AGENT_RESOURCE_TASKS:
        return text, []
    raw = tomllib.loads(text)
    agent, environment = raw.get('agent', {}), raw.get('environment', {})
    keys = [key for key in ('cpus', 'memory', 'storage') if key in agent]
    if any(key not in environment or agent[key] != environment[key] for key in keys):
        raise ValueError(f"{task_id}: agent resources differ from environment resources")
    section, kept, removed = '', [], []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('['):
            section = line.split('#', 1)[0].strip()
        match = re.match(r'^\s*(cpus|memory|storage)\s*=', line)
        if section == '[agent]' and match and match[1] in keys:
            removed.append(match[1])
        else:
            kept.append(line)
    if set(removed) != set(keys):
        raise ValueError(f"{task_id}: unsupported agent resource syntax")
    result = ''.join(kept)
    expected = {**raw, 'agent': {k: v for k, v in agent.items() if k not in keys}}
    if tomllib.loads(result) != expected:
        raise ValueError(f"{task_id}: resource cleanup changed unrelated configuration")
    return result, removed


def patch_instruction(text):
    """Rewrite ./name after a `cd <abs>` inside one fenced block. Returns (new_text, rewrites)."""
    lines = text.split("\n")
    in_block, cwd, count = False, None, 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_block, cwd = not in_block, None
            continue
        if not in_block:
            continue
        pieces = re.split(r"(\s*(?:&&|;)\s*)", line)
        for k in range(0, len(pieces), 2):
            segment = pieces[k].strip()
            cd = CD_SEGMENT.match(segment)
            if cd:
                cwd = cd.group(1).rstrip("/") or "/"
            elif re.match(r"cd(\s|$)", segment):
                cwd = None
            elif cwd and cwd != "/":
                pieces[k], n = DOT_SLASH_TOKEN.subn(cwd + "/", pieces[k])
                count += n
        lines[index] = "".join(pieces)
    return "\n".join(lines), count


# Exclusions from no-op results, at the user's request: verifier prerequisites absent upstream, memory limit.
CHECK_EXCLUSIONS = {}

CHECK_EXCLUSIONS['contrastive_solver-system-administration_20260610_111012_001'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for source builds of OpenSSL, libevent and memcached, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

CHECK_EXCLUSIONS['contrastive_solver-software-engineering_20260611_210349_018'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for a C/shell polyglot base64 program using gcc and bash, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

CHECK_EXCLUSIONS['contrastive_solver-security_20260610_111702_002'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for passphrase recovery and GPG decryption, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

CHECK_EXCLUSIONS['contrastive_solver-software-engineering_20260604_003113_010'] = {
    "category": "verifier-missing-python",
    "reason": "Temporarily archived at the user's request: the original upstream Dockerfile uses ubuntu:24.04 without installing Python, while tests/test.sh requires Python and pytest without provisioning them. No-op verification stops with 'no Python interpreter found' before any tests execute. The agent instructions ask for a C gzip decompressor using only libc, not installation of Python or pytest. These are undocumented verifier prerequisites, not dependencies the instructions require the agent to supply. Our Dockerfile patches did not remove Python; a runtime using the image without injecting dependencies encounters the same mismatch. Original payload preserved for later repair.",
}

CHECK_EXCLUSIONS["multi_solver-software-engineering_20260414_211532_024"] = {
    "category": "verifier-task-memory-limit",
    "reason": "Temporarily archived at the user's request: no-op verification exceeded the task's configured 2 GiB memory limit in all four attempts. Slurm recorded OOM kills in job 1015176, steps 183, 201, 202 and 205 (stage-5 array 1015095_5). The verifier exercises the unchanged logger through threading and multiprocessing. This is not proof that an agent-corrected implementation is unsolvable; no memory-limit increase or grader weakening was applied.",
}


# Explicitly authorized verifier adjustment: missing agent output is a failed test.
REQUIRED_SOLUTION_FILES = {
    "contrastive_solver-security_20260604_003113_006": "/app/base64_decoder.py",
    'contrastive_solver-software-engineering_20260528_174918_024': '/app/server.py',
    'contrastive_solver-software-engineering_20260603_181444_011': '/app/table_format.py',
    'contrastive_solver-data-science_20260604_002443_006': '/app/pipeline.py',
    'contrastive_solver-software-engineering_20260613_152233_017': '/app/calc/calc.c',
    'contrastive_solver-data-science_20260614_141608_006': '/app/csv2parquet.py',
    'contrastive_solver-data-science_20260601_053159_006': '/app/consolidate.py',
    'contrastive_solver-software-engineering_20260606_172622_024': '/app/gc.c',
    'contrastive_solver-software-engineering_20260604_002443_024': '/app/regex_engine.py',
    "contrastive_solver-data-science_20260610_112157_008": "/app/fit_seir.py",
}


def _indent_verifier(text):
    """Indent Python control flow without changing multiline string contents."""
    import io
    import tokenize
    interiors = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.STRING:
            interiors.update(range(token.start[0] + 1, token.end[0] + 1))
    return ''.join(line if number in interiors else '    ' + line
                   for number, line in enumerate(text.splitlines(keepends=True), 1))


def assert_required_solution_file(task_id, text):
    path = REQUIRED_SOLUTION_FILES.get(task_id)
    marker = "# CalibForge: assert missing agent output before loading dependent tests."
    if path is None or text.startswith(marker):
        return text
    return (marker + "\nfrom pathlib import Path as _RequiredSolutionPath\n"
            + f"if not _RequiredSolutionPath({path!r}).is_file():\n"
            + "    def test_required_solution_file_exists():\n"
            + f"        assert _RequiredSolutionPath({path!r}).is_file(), {('Required agent output is missing: ' + path)!r}\n"
            + "else:\n" + _indent_verifier(text))


REQUIRED_VERIFIER_MODULES = {
    "contrastive_solver-data-processing_20260604_003814_003": "pandas",
}


def assert_required_verifier_module(task_id, text):
    module = REQUIRED_VERIFIER_MODULES.get(task_id)
    marker = "# CalibForge: assert missing verifier dependency before loading tests."
    if module is None or text.startswith(marker):
        return text
    return (marker + "\nfrom importlib.util import find_spec as _verifier_find_spec\n"
            + f"if _verifier_find_spec({module!r}) is None:\n"
            + "    def test_required_verifier_dependency():\n"
            + f"        assert _verifier_find_spec({module!r}) is not None, {('Required verifier dependency is missing: ' + module)!r}\n"
            + "else:\n" + _indent_verifier(text))


SERVICE_VERIFIER_REPAIRS = {
    "contrastive_solver-system-administration_20260610_111702_004": "The agent must recover and start PostgreSQL; absence of a usable connection is an expected unsolved state.",
    "contrastive_solver-software-engineering_20260604_002443_020": "The agent must repair the Go/cgo build and start the service; failed compilation or service startup is an expected unsolved state.",
}


def assert_requested_service(task_id, text):
    """Move expected unsolved-state failures into test calls, retaining real checks."""
    import ast
    marker = "# CalibForge: report expected unsolved service state in test assertions."
    if task_id not in SERVICE_VERIFIER_REPAIRS or text.startswith(marker):
        return text
    if task_id == "contrastive_solver-system-administration_20260610_111702_004":
        old = '    pytest.fail("Could not connect to any PostgreSQL instance. "\n                "Is the server running?")'
        if text.count(old) != 1:
            raise ValueError(f"{task_id}: PostgreSQL failure guard changed")
        text = text.replace(old, '    return None  # Expected until the agent recovers and starts PostgreSQL.')
        assertion = 'assert pg_connection is not None, "Required recovered PostgreSQL service is not running"'
    else:
        old = '    assert result.returncode == 0, (\n        f"Go build failed.\\nSTDOUT: {result.stdout}\\nSTDERR: {result.stderr}"\n    )'
        if text.count(old) != 1 or text.count('def setup_module():') != 1:
            raise ValueError(f"{task_id}: Go build setup changed")
        text = text.replace('def setup_module():', 'from functools import lru_cache as _cache_service_preparation\n\n@_cache_service_preparation(maxsize=1)\ndef _prepare_requested_service():')
        text = text.replace(old, '    if result.returncode != 0:\n        return f"Go build failed.\\nSTDOUT: {result.stdout}\\nSTDERR: {result.stderr}"')
        old = '        raise RuntimeError("Server failed to start within 15 seconds")'
        if text.count(old) != 1:
            raise ValueError(f"{task_id}: Go startup failure changed")
        text = text.replace(old, '        return "Server failed to start within 15 seconds"')
        assertion = '_service_error = _prepare_requested_service()\nassert _service_error is None, _service_error'
    lines = text.splitlines(keepends=True)
    tests = [node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
    if not tests:
        raise ValueError(f"{task_id}: no tests found")
    for node in reversed(tests):
        if 'PostgreSQL' in SERVICE_VERIFIER_REPAIRS[task_id] and not any(a.arg == 'pg_connection' for a in node.args.args):
            raise ValueError(f"{task_id}: expected every test to use pg_connection")
        first = node.body[0]
        index = first.end_lineno if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str) else first.lineno - 1
        lines.insert(index, ''.join('    ' + line + '\n' for line in assertion.splitlines()))
    return marker + "\n" + ''.join(lines)


# R19: the Apptainer image's runscript is `cd <workdir>; exec "$@"`, so a Docker ENTRYPOINT never
# runs in our task containers. Where that entrypoint creates the state the instruction asks the
# agent to repair, the agent starts in a healthy environment and the no-op scores reward 1.
# Each record binds the reviewed Dockerfile instructions and the entrypoint's inputs. The setup
# runs the image's own entrypoint with /bin/true as its payload, where Docker passes the CMD
# shell, detached from the setup command's output, before the agent starts.
ENTRYPOINT_STARTUP_SETUP = "setup_files/setup.sh"
ENTRYPOINT_STARTUP_MARKER = "# CalibForge R19: Docker ENTRYPOINT startup restored before the agent (generated)"
ENTRYPOINT_STARTUPS = {
    # Review fp-review-20261108 (Barnard, 2026-11-08): stage 3 of validation-v7-rerun (shard 0013) and
    # validation-v5-rerun (shard 0017) scored the no-op [1.0, 1.0, 1.0]. logsvc.py writes three logs
    # (160 MiB), keeps them open after unlinking them and writes /app/.logsvc.pid; the three tests
    # check that the process is gone, that no deleted /app file is held and that the PID file is gone.
    "multi_solver-system-administration_20260415_123352_009": {
        "entrypoint": ["/entrypoint.sh"],
        "cmd": ["bash"],
        "workdir": "/app",
        "script": "printf '#!/bin/bash\\npython3 /app/logsvc.py &\\nsleep 3\\nexec \"$@\"\\n' > /entrypoint.sh",
        "inputs_sha256": {
            "environment/files/logsvc.py": "36c6f7911de4e1fb83222be4bc4fb70ff7ce69665f906848041172f02966c873",
        },
        # logsvc.py writes its PID file after all three held-deleted logs exist.
        "ready_path": "/app/.logsvc.pid",
        "ready_timeout_sec": 120,
    },
}


def _dockerfile_values(text, op):
    joined = re.sub(r"\\\r?\n", " ", text)
    values = []
    for line in joined.splitlines():
        parts = line.split(None, 1)
        if parts and parts[0].upper() == op:
            values.append(parts[1].strip() if len(parts) > 1 else "")
    return values


def entrypoint_startup_script(task_id):
    """setup_files/setup.sh of a reviewed R19 task."""
    record = ENTRYPOINT_STARTUPS[task_id]
    ready, timeout = record["ready_path"], int(record["ready_timeout_sec"])
    command = shlex.join([*record["entrypoint"], "/bin/true"])
    failure = f"entrypoint service did not write {ready} within {timeout} s"
    return ("#!/bin/bash\n" + ENTRYPOINT_STARTUP_MARKER + "\n"
            f"# Docker starts this image as ENTRYPOINT {json.dumps(record['entrypoint'])} with CMD "
            f"{json.dumps(record['cmd'])}; the Apptainer runscript only runs its arguments.\n"
            "set -e\n"
            'if [ "${1:-}" = "--prepare" ]; then exit 0; fi\n'
            f"cd {shlex.quote(record['workdir'])}\n"
            "# New session without inherited output: the service outlives this command.\n"
            f"setsid -w {command} </dev/null >/dev/null 2>&1\n"
            "ot_waited=0\n"
            f"until [ -s {shlex.quote(ready)} ]; do\n"
            f'  if [ "$ot_waited" -ge {timeout} ]; then\n'
            f"    echo {shlex.quote(failure)} >&2\n"
            "    exit 1\n"
            "  fi\n"
            "  sleep 1\n"
            "  ot_waited=$((ot_waited + 1))\n"
            "done\n")


def is_entrypoint_startup_setup(name, data):
    """True for an R19-generated setup.sh, which runtime history ignores like other patcher annotations."""
    return (name == ENTRYPOINT_STARTUP_SETUP and data is not None
            and any(data == entrypoint_startup_script(task).encode() for task in ENTRYPOINT_STARTUPS))


def restore_entrypoint_startup(task_id, files):
    """R19: ({path: bytes} to add, ENTRYPOINT) for a reviewed task, ({}, None) otherwise.

    files maps the converted task's paths to bytes. Source drift raises for review.
    """
    record = ENTRYPOINT_STARTUPS.get(task_id)
    if record is None:
        return {}, None
    script = entrypoint_startup_script(task_id).encode()
    existing = files.get(ENTRYPOINT_STARTUP_SETUP)
    if existing is not None and existing != script:
        raise ValueError(f"{task_id}: existing agent setup needs review before restoring the entrypoint")
    if any(path.startswith("setup_files/") and path != ENTRYPOINT_STARTUP_SETUP for path in files):
        raise ValueError(f"{task_id}: existing agent setup files need review before restoring the entrypoint")
    dockerfile = files.get("environment/Dockerfile", b"").decode("utf-8")
    entrypoints = _dockerfile_values(dockerfile, "ENTRYPOINT")
    cmds = _dockerfile_values(dockerfile, "CMD")
    workdirs = _dockerfile_values(dockerfile, "WORKDIR")
    try:
        entrypoint, cmd = json.loads(entrypoints[-1]), json.loads(cmds[-1])
    except (IndexError, ValueError) as error:
        raise ValueError(f"{task_id}: reviewed exec-form ENTRYPOINT/CMD changed") from error
    path = record["entrypoint"][0]
    if (entrypoint != record["entrypoint"] or cmd != record["cmd"] or len(entrypoints) != 1
            or not workdirs or workdirs[-1] != record["workdir"]
            or dockerfile.count(record["script"]) != 1
            or dockerfile.count(path) != 3 or f"chmod +x {path}" not in dockerfile):
        raise ValueError(f"{task_id}: reviewed Docker ENTRYPOINT startup changed")
    for name, digest in record["inputs_sha256"].items():
        if name not in files or hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError(f"{task_id}: reviewed entrypoint input {name} changed")
    # An input that already carries this exact setup is left unchanged.
    return ({} if existing is not None else {ENTRYPOINT_STARTUP_SETUP: script}), " ".join(record["entrypoint"])
