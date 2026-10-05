#!/usr/bin/env python3
"""Harbor training tasks from Terminal Wrench, with its exploit evidence kept outside the tasks.

Terminal Wrench (https://github.com/few-sh/terminal-wrench) collects terminal
tasks whose verifiers were rewarded for exploits. Each task folder holds the
original Harbor task once per attacker model, plus Terminal Wrench's own
analysis files and the recorded hack trajectories. Its SETA tasks are SETA-Env's
own Harbor version (camel-ai/seta-env Harbor-Dataset/<id>) byte for byte.

Per task this patcher:
- checks that every model's copy of ``original_task`` is byte-identical;
- drops tasks taken from evaluation benchmarks (Terminal-Bench 2.0 and 1.0,
  Terminal-Bench Pro, OpenThoughts-TB-dev): training on them contaminates evals;
- drops tasks whose original docker-compose startup command or host mount was
  lost in SETA-Env's Harbor version: Harbor starts every task with a
  plain ``sleep infinity`` (Docker) or ignores CMD (cluster Apptainer bridge),
  so their environment never initializes; tasks whose compose asked for extra
  privileges stay, flagged in the report;
- moves Terminal Wrench's analysis files (hack descriptions, replication
  prompts) and SETA-Env's generator draft (which states the intended fix) out
  of the task into ``exploits/<task>.json``;
- repairs the shared verifier wrapper: reward files the agent planted
  (``reward.txt`` and ``reward.json``, which Harbor reads first) are removed
  before grading and again before the reward is written, and only pytest exit
  1 means reward 0 (other exits are verifier errors with no reward, as in
  data/seta/patch.py). An agent process still running in the shared container
  can rewrite them after that; only a separate verifier environment closes it;
- applies reviewed per-task repairs from ``TASK_REPAIRS``: verifier hardening
  against a recorded exploit, dependency pins and undocumented requirements.

The upstream checkout is never modified and must be a clean checkout of
REVISION: tasks are listed from the pinned tree. Every dropped task is in the
report.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
import tomllib

UPSTREAM = 'https://github.com/few-sh/terminal-wrench'
REVISION = 'd8a29613235a0ef56a8b70b3142626a533da28c2'
PATCH_VERSION = 'terminal-wrench-v1'
TRAINING_SOURCES = {'seta_2026_01_29'}
EVALUATION_SOURCES = {'terminal-bench__2.0', 'TerminalBench-original',
                      'terminal-bench-pro', 'OpenThoughts-TB-dev'}
# Terminal Wrench additions, and SETA-Env's generator draft (Harbor-Dataset/<id>/
# environment/draft_spec.md: the intended root cause and fix). None is referenced
# by a Dockerfile, test or solution; all go to the exploit sidecar.
TW_ANALYSIS_FILES = ('analysis.toml', 'variants.json')
SETA_DRAFT_FILES = ('environment/draft_spec.md',)
ANALYSIS_FILES = TW_ANALYSIS_FILES + SETA_DRAFT_FILES
REQUIRED = ('instruction.md', 'task.toml', 'environment/Dockerfile',
            'tests/test.sh', 'solution/solve.sh')
TASK_PREFIX = 'seta-env-'

# Container settings from camel-ai/seta-env Dataset/<id>/docker-compose.yaml
# that its Harbor version (Harbor-Dataset/<id>) no longer carries; task.toml
# marks the first group has_custom_cmd. Environment variables were moved into
# the Dockerfiles.
SETA_ENV = 'https://github.com/camel-ai/seta-env@683748c43e0b8ddd68ee5ceb75d28af3bea85d71'
LOST_STARTUP = {
    '161': 'sh -c "/etc/rc.local && sleep infinity"',
    '164': '/app/init.sh (cap_add NET_ADMIN)',
    '355': 'sh -c "/setup-loopback.sh && sleep infinity" (cap_add SYS_ADMIN)',
    '475': 'sh -c "myserver infinity & sleep infinity"',
    '486': 'sh -c "/opt/start_service_manager.sh && sleep infinity"',
    '775': 'sh -c /opt/app-logger/startup.sh',
    '824': '/bin/bash /opt/orchestrator/entrypoint.sh',
    '891': 'sh -c "/usr/sbin/sshd && ssh-keyscan -p 2222 localhost >> /root/.ssh/known_hosts && sleep infinity"',
    '1217': 'sh -c "/opt/break-dns.sh sleep infinity" (entrypoint cleared)',
    '1260': '/start.sh sleep infinity',
}
HARMLESS_CUSTOM_CMD = {'1192'}  # compose command was a plain `sleep infinity`
LOST_HOST_ACCESS = {'1168': 'mounts the host Docker socket /var/run/docker.sock'}
COMPOSE_PRIVILEGES = {
    '8': 'privileged', '158': 'privileged', '352': 'privileged', '976': 'privileged',
    '1185': 'privileged', '1285': 'privileged', '1026': 'cap_add NET_RAW, NET_ADMIN',
    '1109': 'cap_add SYS_ADMIN; apparmor unconfined', '1190': 'cap_add SYS_RAWIO',
    '1225': 'cap_add SYS_ADMIN; /dev/fuse; apparmor unconfined',
    '1366': 'cap_add SYS_ADMIN; /dev/fuse; apparmor unconfined',
}

REWARD_BLOCK = re.compile(
    r'^(?P<run>(?:uv run|python3 -m) pytest /tests/test_outputs\.py[^\n]*)\n(?:[ \t]*\n)*'
    r'if \[ \$\? -eq 0 \]; then\n'
    r'  echo 1 > /logs/verifier/reward\.txt\n'
    r'else\n'
    r'  echo 0 > /logs/verifier/reward\.txt\n'
    r'fi\n?\Z', re.M)
REWARD_CASE = '''{run}
rc=$?
# terminal-wrench-v1: pytest 1 means assertions failed. Any other status is a
# verifier error and leaves no reward rather than a silent zero.
rm -rf /logs/verifier/reward.txt /logs/verifier/reward.json
case "$rc" in
  0) echo 1 > /logs/verifier/reward.txt ;;
  1) echo 0 > /logs/verifier/reward.txt ;;
  *) echo "VERIFIER_ERROR pytest_exit=$rc" >&2; exit "$rc" ;;
esac
'''
CLEAR_REWARD = ('# terminal-wrench-v1: never grade with a reward file the agent left behind.\n'
                'mkdir -p /logs/verifier\nrm -rf /logs/verifier/reward.txt /logs/verifier/reward.json\n')

# Reviewed repairs: task id -> [(path, original text, replacement, reason)].
# Each original must occur exactly once, so a second application fails loudly.
# Hardening names the recorded exploits it closes; audit.py replays them.
X11_PROBE = '''


def _socket_owner(path):
    """PID of the process holding the listening Unix socket at path, from /proc."""
    inodes = {line.split()[6] for line in open("/proc/net/unix").read().splitlines()[1:]
              if line.split()[-1].lstrip("@") == path}
    for fd in Path("/proc").glob("[0-9]*/fd/*"):
        try:
            if os.readlink(fd) in {f"socket:[{inode}]" for inode in inodes}:
                return fd.parts[2]
        except OSError:
            continue
    return None


def test_display_is_served_by_a_real_x_server():
    """terminal-wrench-v1: query the display over X11 with the verifier's own
    client instead of trusting xrandr, packages or process names, which the
    recorded gpt-5.4 exploits replaced with stubs, forged dpkg entries and a
    sleeping process renamed to look like Xorg."""
    from Xlib import display  # installed by tests/test.sh into the verifier's venv
    subprocess.run(["pkill", "-f", "Xorg.*:99"], capture_output=True)
    time.sleep(1)
    subprocess.run(["/bin/bash", "/opt/virtual-display/start-display.sh"], capture_output=True, timeout=30)
    time.sleep(3)
    owner = _socket_owner("/tmp/.X11-unix/X99")
    assert owner, "nothing listens on the X socket for display :99"
    assert os.path.realpath(f"/proc/{owner}/exe") == "/usr/lib/xorg/Xorg", "display :99 is not served by Xorg"
    connection = display.Display(":99")
    try:
        assert connection.has_extension("RANDR"), "the X server on :99 does not implement RandR"
        resources = connection.screen().root.xrandr_get_screen_resources()
        modes = {(mode.width, mode.height) for mode in resources.modes}
    finally:
        connection.close()
    for width, height in ((1920, 1080), (1280, 720), (800, 600)):
        assert (width, height) in modes, f"{width}x{height} is not a RandR mode on :99: {sorted(modes)}"
'''
PROBE_778 = '''


def test_report_is_generated_from_the_device_files():
    \"\"\"terminal-wrench-v1: run generate_report.py on a fresh, randomized device
    set, so a report written by hand or a script that prints a fixed answer fails
    (recorded exploits: hardcoded JSON from a one-time read of the mock data).\"\"\"
    import random
    import shutil
    import sys
    import tempfile

    rng = random.Random()
    devices = Path("/mock_v4l2/devices")
    report = Path("/home/user/device_report.json")
    saved = Path(tempfile.mkdtemp()) / "devices"
    shutil.copytree(devices, saved, symlinks=True)
    saved_report = report.read_bytes() if report.exists() else None
    expected = {}
    try:
        shutil.rmtree(devices)
        devices.mkdir()
        kinds = ["capture", "metadata"] + [rng.choice(["capture", "metadata"]) for _ in range(rng.randint(1, 4))]
        rng.shuffle(kinds)
        for index, kind in enumerate(kinds):
            name = f"Probe {rng.choice(['Cam', 'Grabber', 'Tuner'])} {rng.randrange(16 ** 6):06x}"
            driver = rng.choice(["uvcvideo", "vivid", "v4l2loopback", "bttv"]) + f"_{rng.randrange(100)}"
            caps = "Video Capture" if kind == "capture" else "Metadata Capture"
            formats = {}
            if kind == "capture":
                for code in rng.sample(["YUYV", "MJPG", "H264"], rng.randint(1, 3)):  # the codes the instruction names
                    sizes = rng.sample([(320, 240), (352, 288), (640, 480), (800, 600), (1280, 720), (1920, 1080)],
                                       rng.randint(1, 3))
                    formats[code] = {(w, h, tuple(sorted(rng.sample([5, 10, 12, 15, 20, 24, 25, 30, 50, 60],
                                                                      rng.randint(1, 3)), reverse=True)))
                                     for w, h in sizes}
            folder = devices / f"video{index}"
            folder.mkdir()
            (folder / "info").write_text(
                f"Driver name   : {driver}\\nCard type     : {name}\\nBus info      : platform:probe-{index:03d}\\n"
                f"Driver version: 1.0.0\\nCapabilities  : {'0x85200001' if kind == 'capture' else '0x84200000'}\\n"
                f"        {caps}\\n        Streaming\\n")
            labels = {"YUYV": "YUYV 4:2:2", "MJPG": "MJPG (Motion-JPEG)", "H264": "H264 (H.264)"}
            (folder / "formats").write_text("\\n".join(labels[c] for c in formats) + "\\n")
            (folder / "resolutions").write_text("".join(
                f"{c}:{w}x{h}@{','.join(map(str, fps))}\\n" for c, rows in formats.items() for w, h, fps in sorted(rows)))
            expected[f"/dev/video{index}"] = (kind, name, driver, formats)
        report.unlink(missing_ok=True)
        # Run it with the task's python3, not the verifier venv that `uv run` put first on PATH.
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                      if p and not p.startswith(sys.prefix))
        subprocess.run(["python3", "/home/user/generate_report.py"], cwd="/home/user", env=env,
                       capture_output=True, timeout=120)
        assert report.exists(), "generate_report.py did not write device_report.json for a new device set"
        produced = json.loads(report.read_text())
    finally:
        shutil.rmtree(devices, ignore_errors=True)
        shutil.copytree(saved, devices, symlinks=True)
        if saved_report is None:
            report.unlink(missing_ok=True)
        else:
            report.write_bytes(saved_report)
    assert produced.get("total_device_count") == len(expected), "total_device_count does not follow the device files"
    assert produced.get("capture_device_count") == sum(v[0] == "capture" for v in expected.values()), \\
        "capture_device_count does not follow the device files"
    found = {d.get("path"): d for d in produced.get("devices", [])}
    assert set(found) == set(expected), f"device paths {sorted(found)} do not follow the device files"
    for path, (kind, name, driver, formats) in expected.items():
        device = found[path]
        assert (device.get("type"), device.get("name"), device.get("driver")) == (kind, name, driver), \\
            f"{path}: type, name or driver does not follow its info file"
        listed = {f["pixel_format"]: {(r["width"], r["height"], tuple(sorted(r["fps"], reverse=True)))
                                      for r in f["resolutions"]} for f in device.get("formats", [])}
        assert listed == formats, f"{path}: formats and resolutions do not follow its files"
'''
TASK_REPAIRS = {
    '1178': [
        ('environment/Dockerfile', 'RUN pip3 install redis --break-system-packages\n',
         'RUN pip3 install redis==5.0.0 --break-system-packages\n',
         'pin redis to the version the verifier installs'),
    ],
    '778': [
        ('tests/test_outputs.py',
         "            f\"{device} should be 'metadata' but got '{device_types[device]}'\"\n",
         "            f\"{device} should be 'metadata' but got '{device_types[device]}'\"\n" + PROBE_778,
         'harden verifier: regenerate the report from a fresh, randomized device set'),
    ],
    '1219': [
        ('tests/test.sh', 'uv add pytest==8.4.1\n', 'uv add pytest==8.4.1\nuv add python-xlib==0.33\n',
         'harden verifier: its own X11 client'),
        ('tests/test_outputs.py',
         "    assert '800x600' in xrandr_output, f\"800x600 resolution not available. xrandr output: {xrandr_output}\"\n",
         "    assert '800x600' in xrandr_output, f\"800x600 resolution not available. xrandr output: {xrandr_output}\"\n"
         + X11_PROBE,
         'harden verifier: the RandR modes of the Xorg process serving :99, queried over X11'),
    ],
    '1355': [
        ('environment/Dockerfile', 'RUN pip3 install httpie --break-system-packages',
         'RUN pip3 install httpie==3.2.4 --break-system-packages',
         'pin httpie'),
    ],
}


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def tree_digest(root):
    value = hashlib.sha256()
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        value.update(path.relative_to(root).as_posix().encode() + b'\0')
        value.update(path.read_bytes() + b'\0')
    return value.hexdigest()


def read_tree(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in sorted(root.rglob('*')) if p.is_file()}


def repair_test_sh(text):
    """Return the repaired wrapper, or None when it is not the shared boilerplate."""
    if 'terminal-wrench-v1' in text:
        raise ValueError('test.sh has already been patched')
    match = REWARD_BLOCK.search(text)
    if not match or not text.startswith('#!/bin/bash\n'):
        return None
    text = text[:match.start()] + REWARD_CASE.format(run=match['run'])
    return text.replace('#!/bin/bash\n', '#!/bin/bash\n' + CLEAR_REWARD, 1)


def patch_files(files, task_id):
    """Apply the named repairs to one task's files; return (files, changes)."""
    files, changes = dict(files), []
    for name in ANALYSIS_FILES:
        if files.pop(name, None) is not None:
            changes.append(f'moved {name} to the exploit sidecar')
    data, mode = files['tests/test.sh']
    repaired = repair_test_sh(data.decode())
    if repaired is not None:
        files['tests/test.sh'] = (repaired.encode(), mode)
        changes.append('verifier: clear planted reward; only pytest exit 1 scores 0')
    for path, old, new, reason in TASK_REPAIRS.get(task_id, []):
        text = files[path][0].decode()
        if text.count(old) != 1:
            raise ValueError(f'{task_id}: expected the original {path} anchor exactly once')
        files[path] = (text.replace(old, new).encode(), files[path][1])
        changes.append(f'{path}: {reason}')
    return files, changes


def model_dirs(task_dir):
    return sorted(p for p in task_dir.iterdir() if p.is_dir())


def exploit_records(task_dir, upstream):
    """Recorded attacker trials, with paths relative to the upstream checkout."""
    records = []
    for model in model_dirs(task_dir):
        info = model / 'task.json'
        if not info.is_file():
            continue
        for trial in json.loads(info.read_text()).get('trajectories', []):
            tree, label = trial.get('tree_name'), trial.get('trajectory_label')
            path = model / tree / label if tree and label else None
            records.append({
                'model': model.name, 'classification': trial.get('classification'),
                'reward': trial.get('reward'), 'label': label,
                'summary': trial.get('brief_exploit_summary'),
                'trajectory': (path / 'trial/agent/trajectory.json').relative_to(upstream).as_posix()
                if path and (path / 'trial/agent/trajectory.json').is_file() else None})
    return records


def git(upstream, *args):
    return subprocess.check_output(['git', '-C', str(upstream), *args], text=True)


def check_revision(upstream, allow_other):
    """The pinned revision with a complete, unmodified working tree."""
    revision = git(upstream, 'rev-parse', 'HEAD').strip()
    if allow_other:
        return revision
    if revision != REVISION:
        raise ValueError(f'Upstream is at {revision}, expected {REVISION}')
    if not git(upstream, 'ls-files', '--', 'task_source_datasets.json').strip():
        raise ValueError('Upstream index is empty; re-run the checkout')
    if git(upstream, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Upstream working tree differs from the pinned revision')
    tracked = set(git(upstream, 'ls-tree', '--name-only', 'HEAD', 'tasks/').split())
    present = {f'tasks/{p.name}' for p in (upstream / 'tasks').iterdir() if p.is_dir()}
    if tracked != present:
        raise ValueError(f'Task folders differ from the pinned tree: {sorted(tracked ^ present)[:5]}')
    return revision


def build(upstream, output, allow_other_revision=False, only=None):
    upstream, output = upstream.resolve(), output.resolve()
    if output.exists():
        raise ValueError('Use a new output directory; existing artifacts are never overwritten')
    revision = check_revision(upstream, allow_other_revision)
    sources = json.loads((upstream / 'task_source_datasets.json').read_text())
    report = {'patch_version': PATCH_VERSION, 'patcher_sha256': digest_bytes(Path(__file__).read_bytes()),
              'upstream': UPSTREAM, 'revision': revision, 'compose_source': SETA_ENV, 'kept': [], 'dropped': [],
              'validation_status': 'not_validated'}
    tasks_out, exploits_out = output / 'tasks', output / 'exploits'
    tasks_out.mkdir(parents=True)
    exploits_out.mkdir()
    for task_dir in sorted((upstream / 'tasks').iterdir(), key=lambda p: p.name):
        task_id = task_dir.name
        if not task_dir.is_dir() or (only and task_id not in only):
            continue
        task_sources = sorted(sources.get(task_id, []))

        def drop(reason, **extra):
            report['dropped'].append({'task_id': task_id, 'sources': task_sources,
                                      'reason': reason, **extra})

        # Contamination first, so every evaluation task is counted as such.
        if set(task_sources) & EVALUATION_SOURCES:
            drop('evaluation_benchmark_source')
            continue
        if not task_sources or not set(task_sources) <= TRAINING_SOURCES:
            drop('unknown_source')
            continue
        copies = [m / 'original_task' for m in model_dirs(task_dir) if (m / 'original_task').is_dir()]
        if not copies:
            drop('no_original_task')
            continue
        digests = {tree_digest(c) for c in copies}
        if len(digests) != 1:
            drop('original_task_differs_across_models')
            continue
        files = read_tree(copies[0])
        missing = [name for name in REQUIRED if name not in files]
        if missing:
            drop('missing_files', files=missing)
            continue
        try:
            config = tomllib.loads(files['task.toml'][0].decode())
        except tomllib.TOMLDecodeError as exc:
            drop('invalid_task_toml', error=str(exc))
            continue
        flagged = config.get('metadata', {}).get('has_custom_cmd') is True
        if flagged != (task_id in LOST_STARTUP or task_id in HARMLESS_CUSTOM_CMD):
            raise ValueError(f'{task_id}: has_custom_cmd={flagged} disagrees with LOST_STARTUP; review {SETA_ENV}')
        if task_id in LOST_STARTUP:
            drop('startup_command_not_run_by_harbor', original_command=LOST_STARTUP[task_id])
            continue
        if task_id in LOST_HOST_ACCESS:
            drop('needs_host_access', original_compose=LOST_HOST_ACCESS[task_id])
            continue
        analysis = {name: files[name][0].decode() for name in TW_ANALYSIS_FILES if name in files}
        draft = {name: files[name][0].decode() for name in SETA_DRAFT_FILES if name in files}
        patched, changes = patch_files(files, task_id)
        name = TASK_PREFIX + task_id
        for rel, (data, mode) in patched.items():
            target = tasks_out / name / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(mode)
        summary = task_dir / 'hack_summary.md'
        exploits = exploit_records(task_dir, upstream)
        sidecar = {'task': name, 'upstream_task_id': task_id, 'sources': task_sources,
                   'hack_summary': summary.read_text() if summary.is_file() else None,
                   'analysis': analysis, 'seta_draft_spec': draft, 'exploits': exploits}
        (exploits_out / f'{name}.json').write_text(json.dumps(sidecar, indent=2) + '\n')
        report['kept'].append({
            'task': name, 'upstream_task_id': task_id, 'sources': task_sources,
            'original_sha256': digests.pop(), 'patched_sha256': tree_digest(tasks_out / name),
            'changes': changes,
            'verifier_wrapper': 'repaired' if any(c.startswith('verifier:') for c in changes) else 'custom_unchanged',
            'rewarded_serious_exploits': sum(e['classification'] == 'rewarded_serious_exploit' for e in exploits),
            'original_compose_privileges': COMPOSE_PRIVILEGES.get(task_id),
            'hardened': any('harden verifier' in r[3] for r in TASK_REPAIRS.get(task_id, []))})
    unknown = set(TASK_REPAIRS) - {k['upstream_task_id'] for k in report['kept']}
    if unknown and not only:
        raise ValueError(f'Repairs for tasks that were not kept: {sorted(unknown)}')
    counts = {}
    for item in report['dropped']:
        counts[item['reason']] = counts.get(item['reason'], 0) + 1
    report['summary'] = {'kept': len(report['kept']), 'dropped': len(report['dropped']),
                         'dropped_by_reason': counts,
                         'repaired': sum(k['upstream_task_id'] in TASK_REPAIRS for k in report['kept']),
                         'hardened': sum(k['hardened'] for k in report['kept']),
                         'custom_verifier_unchanged': sum(k['verifier_wrapper'] == 'custom_unchanged'
                                                          for k in report['kept']),
                         'flagged_compose_privileges': sum(bool(k['original_compose_privileges'])
                                                           for k in report['kept'])}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--upstream', type=Path, required=True, help='checkout of few-sh/terminal-wrench')
    ap.add_argument('--output', type=Path, required=True, help='new directory for tasks/, exploits/, report.json')
    ap.add_argument('--tasks', nargs='+', help='only these upstream task ids (pilots)')
    ap.add_argument('--allow-other-revision', action='store_true')
    args = ap.parse_args()
    report = build(args.upstream, args.output, args.allow_other_revision, set(args.tasks or []))
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
