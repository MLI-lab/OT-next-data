"""Per-verification package fallback without processing agent site startup hooks."""
import json
from pathlib import Path
import shlex


# -I -S suppresses environment injection, sitecustomize and .pth execution. On
# Python <=3.13, -S also suppresses venv prefix adjustment, so inspect pyvenv.cfg.
PROBE = r'''
import json, os, site, sys
prefix = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
cfg = os.path.join(prefix, 'pyvenv.cfg')
prefixes = [sys.prefix, sys.exec_prefix]
if os.path.isfile(cfg):
    with open(cfg) as stream:
        config = dict(line.strip().lower().split('=', 1) for line in stream if '=' in line)
    include = any(k.strip() == 'include-system-site-packages' and v.strip() == 'true'
                  for k, v in config.items())
    prefixes = [prefix] + (prefixes if include else [])
paths = site.getsitepackages(prefixes)
if not os.path.isfile(cfg) or include:
    paths.append(site.getusersitepackages())
paths = list(dict.fromkeys(os.path.abspath(p) for p in paths if os.path.isdir(p)))
print('OT_AGENT_PACKAGES=' + json.dumps({'python': sys.executable,
      'version': list(sys.version_info[:3]), 'directories': paths}))
'''


# Installed in each clean interpreter's site-packages, including auxiliary ones.
# Keeping agent directories OUT of sys.path also avoids their plugin entry points
# and namespace portions silently extending packages owned by the clean runtime.
HOOK = r'''
import json as _json
import os as _os
import sys as _sys
from importlib.machinery import PathFinder as _PathFinder

class _AgentPackageFallback:
    _ot_agent_package_fallback = True

    def __init__(self, paths):
        self.paths = paths

    def find_spec(self, fullname, path=None, target=None):
        if path is not None or '.' in fullname or fullname in ('sitecustomize', 'usercustomize'):
            return None
        return _PathFinder.find_spec(fullname, self.paths)

    def find_distributions(self, context=None):
        # Named version/metadata lookups may need the fallback package's metadata.
        # Do not advertise agent distributions to plugin/entry-point enumeration.
        if context is None or not getattr(context, 'name', None):
            return iter(())
        from importlib.metadata import DistributionFinder, MetadataPathFinder
        return MetadataPathFinder.find_distributions(
            DistributionFinder.Context(name=context.name, path=self.paths))

with open(_os.path.join(_os.path.dirname(__file__), 'ot_agent_packages.json')) as _stream:
    _records = _json.load(_stream)
_paths = []
for _record in _records:
    if tuple(_record['version'][:2]) != tuple(_sys.version_info[:2]):
        continue
    for _path in _record['directories']:
        if _path not in _paths and _os.path.isdir(_path):
            _paths.append(_path)
if not any(getattr(_finder, '_ot_agent_package_fallback', False) for _finder in _sys.meta_path):
    _sys.meta_path.append(_AgentPackageFallback(_paths))
'''


def probe_command(extra_python=None):
    candidates = 'python3 python'
    if extra_python:
        candidates = shlex.quote(extra_python) + ' ' + candidates
    return (
        'for p in "${VIRTUAL_ENV:-/nonexistent}/bin/python" '
        '"${CONDA_PREFIX:-/nonexistent}/bin/python" ' + candidates + '; do '
        'command -v "$p" >/dev/null 2>&1 || continue; '
        '"$p" -I -S -c ' + shlex.quote(PROBE) + ' || exit; done')


def parse_probe(result):
    if result.return_code:
        raise RuntimeError('Agent Python package discovery failed: ' + (result.stderr or '')[-1000:])
    records = []
    for line in (result.stdout or '').splitlines():
        if not line.startswith('OT_AGENT_PACKAGES='):
            continue
        record = json.loads(line.split('=', 1)[1])
        if (not isinstance(record, dict) or not isinstance(record.get('python'), str)
                or not isinstance(record.get('version'), list) or len(record['version']) != 3
                or not all(type(v) is int for v in record['version'])
                or not isinstance(record.get('directories'), list)
                or len(record['directories']) > 100
                or not all(isinstance(p, str) and p.startswith('/') and len(p) < 4096
                           and not any(ord(c) < 32 for c in p)
                           for p in record['directories'])):
            raise ValueError('Invalid agent package directory evidence')
        if record not in records:
            records.append(record)
    return records


def install_command(root, records):
    # The installer runs with the clean interpreter, and writes only the private
    # extraction or overlay. It never executes any discovered agent interpreter.
    script = ('import pathlib, json\nroot = pathlib.Path(' + repr(root) + ')\n'
              + 'records = ' + repr(records) + '\nhook = ' + repr(HOOK) + '\n') + '''
sites = sorted(root.glob('lib/python*/site-packages')) + sorted(root.glob('lib/python*/dist-packages'))
sites += sorted(root.glob('aux/*/lib/python*/site-packages'))
if not sites:
    raise RuntimeError('No clean runtime package directory for fallback hook')
installed = []
for site in sites:
    (site / 'ot_agent_packages.json').write_text(json.dumps(records))
    (site / 'ot_agent_package_fallback.py').write_text(hook)
    (site / 'zz_ot_agent_package_fallback.pth').write_text('import ot_agent_package_fallback\\n')
    for name in ('ot_agent_packages.json', 'ot_agent_package_fallback.py', 'zz_ot_agent_package_fallback.pth'):
        (site / name).chmod(0o644)
    version = [int(v) for v in site.parent.name[len('python'):].split('.')]
    installed.append({'site': str(site), 'fallback_directories': list(dict.fromkeys(
        p for r in records if r['version'][:2] == version for p in r['directories'])),
        'incompatible_interpreters': [r['python'] for r in records if r['version'][:2] != version]})
print('OT_AGENT_FALLBACK_INSTALLED=' + json.dumps(installed))
'''
    return shlex.quote(root + '/bin/python3') + ' -I -S -c ' + shlex.quote(script)


async def prepare(environment, bundle, context, evidence_dir, *, user=None):
    """Called only for shared verification, before activating the clean runtime."""
    from harbor_patches.verifier_mount import command
    from harbor_patches.verifier_assets import REFERENCE
    evidence = Path(evidence_dir) / 'agent-package-fallback.json'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    report = {'status': 'error', 'policy': 'clean-first-top-level-fallback',
              'scope': 'PATH Python, VIRTUAL_ENV, CONDA_PREFIX and declared task_python; no agent startup hooks',
              'compatibility': 'same Python major/minor only; ABI and dependency compatibility still require task checks'}
    try:
        assets = Path(context) / REFERENCE
        extra = json.loads(assets.read_text()).get('task_python') if assets.is_file() else None
        result = await environment.exec(probe_command(extra), user=user, timeout_sec=30)
        records = parse_probe(result)
        report['interpreters'] = records
        if not records:
            report['status'] = 'unavailable'
            report['reason'] = 'No discoverable agent Python interpreter; no fallback installed'
            return
        image = bundle.get('image_backed')
        root = bundle['directory'] + ('/active' if image else '/python')
        cmd = install_command(root, records)
        if image:
            cmd = command(dict(bundle, mount_mode='writable'), cmd)
        result = await environment.exec(cmd, user='root', timeout_sec=60)
        if result.return_code:
            raise RuntimeError('Agent package fallback installation failed: ' + (result.stderr or '')[-1000:])
        rows = [line.split('=', 1)[1] for line in (result.stdout or '').splitlines()
                if line.startswith('OT_AGENT_FALLBACK_INSTALLED=')]
        if len(rows) != 1:
            raise RuntimeError('Missing agent package fallback installation evidence')
        report['runtime_sites'] = json.loads(rows[0])
        report['status'] = 'installed'
    except BaseException as exc:
        report['error'] = str(exc)
        raise
    finally:
        evidence.write_text(json.dumps(report, indent=2) + '\n')
