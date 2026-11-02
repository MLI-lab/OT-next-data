"""Preserve pinned editable VCS requirements using private cached Git objects."""
import hashlib
import re


def build_requirements(entry):
    # Literal build-system.requires at this pinned jupyter_core commit is
    # ["hatchling>=1.4"]. Hatchling's editable build also requests editables.
    # Cache these build-only inputs; the original pip editable installation
    # still creates its isolated build environment at the same setup point.
    if (entry['url'] == 'https://github.com/jupyter/jupyter_core.git'
            and entry['commit'] == 'dc840a3bef34316a511aacb5972b5212c4c0e7af'):
        return ['hatchling>=1.4', 'editables>=0.3']
    return []


def recipe(requirement):
    match = re.fullmatch(r'-e git\+(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})#egg=([A-Za-z0-9_.-]+)', requirement)
    if not match:
        return None
    url, commit, _ = match.groups()
    key = hashlib.sha256((url + '@' + commit).encode()).hexdigest()
    return {'url': url, 'commit': commit, 'filename': 'editable-' + key + '.tar.gz'}


RESTORE = '''import hashlib, json, os, pathlib, shlex, sys, tarfile, tempfile
root = pathlib.Path(os.environ["OT_TASK_OFFLINE"]) / "sources"
records = json.loads((root / "ot-git-sources.json").read_text())
expected = json.loads(sys.argv[1])
settings = []
for spec in expected:
    matches = [record for record in records if all(record.get(k) == v for k, v in spec.items())]
    if len(matches) != 1: sys.exit(1)
    record = matches[0]
    archive = root / spec["filename"]
    checksum = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""): checksum.update(block)
    if checksum.hexdigest() != record["sha256"]: sys.exit(1)
    with tarfile.open(str(archive)) as stream:
        members = stream.getmembers()
        seen = set()
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] != "git" or member.name in seen
                    or not (member.isfile() or member.isdir())):
                raise ValueError("Unsafe editable Git archive")
            seen.add(member.name)
        if not {"git/HEAD", "git/config", "git/objects"}.issubset(seen): sys.exit(1)
        target = pathlib.Path(tempfile.mkdtemp(prefix="ot-editable-source-"))
        for member in members:
            path = target / member.name
            if member.isdir(): path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as source, path.open("xb") as out:
                    import shutil
                    shutil.copyfileobj(source, out)
                path.chmod(member.mode & 0o777)
    settings.append(("url." + (target / "git").as_uri() + ".insteadOf", spec["url"]))
settings.append(("protocol.file.allow", "always"))
count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if count < 0: raise ValueError("Invalid inherited Git configuration count")
for key, value in settings:
    print("export GIT_CONFIG_KEY_" + str(count) + "=" + shlex.quote(key))
    print("export GIT_CONFIG_VALUE_" + str(count) + "=" + shlex.quote(value))
    count += 1
print("export GIT_CONFIG_COUNT=" + str(count))
'''


def prepared_files(files, entries):
    import json
    import shlex
    result = dict(files)
    result['setup_files/ot_editable_sources.py'] = RESTORE.encode()
    setup = result['setup_files/setup.sh'].decode()
    anchor = 'finish_phase dependencies\n'
    if setup.count(anchor) != 1:
        raise ValueError('Editable dependency installation point changed')
    command = ('if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
               '  ot_editable_config=$(mktemp)\n'
               '  if /opt/conda/bin/python /setup_files/ot_editable_sources.py '
               + shlex.quote(json.dumps(entries)) + ' > "$ot_editable_config"; then\n'
               '    source "$ot_editable_config"\n'
               '  fi\nfi\n')
    result['setup_files/setup.sh'] = setup.replace(anchor, anchor + command, 1).encode()
    return result
