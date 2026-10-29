#!/usr/bin/env python3
"""Build verifier-only offline package sets in preparation jobs, never during validation.

Each set is resolved inside the pristine cached task image: apt downloads exactly what
`apt-get install` of the requested packages would fetch there, pip/uv wheels are built by
the interpreter the test uses. The archive (repo/, wheels/, bin/uv) is staged after the
agent by harbor_patches.verifier_bundle.stage_offline; the task image stays unchanged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harbor_patches.verifier_bundle import OFFLINE_FORMAT, OFFLINE_REQUEST_KEYS, OFFLINE_ROOTS, cache_root, digest, offline_setup_command, spec_id, validate_offline_archive, verify

BUILD = r'''set -eu
O=/ot-out; W=/ot-work
export HOME=$W/home TMPDIR=$W/tmp PIP_CACHE_DIR=$W/pip-cache PIP_DISABLE_PIP_VERSION_CHECK=1 UV_CACHE_DIR=$W/uv-cache
mkdir -p $O/repo $O/wheels $HOME $TMPDIR $W/lists/partial $W/archives/partial $W/cache
: > $O/repo/Packages
if [ -n "${OT_APT:-}" ]; then
  A="-o Debug::NoLocking=1 -o APT::Sandbox::User=$(id -un) -o Dir::State::Lists=$W/lists -o Dir::Cache=$W/cache -o Dir::Cache::Archives=$W/archives -o Acquire::Languages=none"
  apt-get $A update
  apt-get $A install -y --download-only $OT_APT
  for f in $W/archives/*.deb; do [ -e "$f" ] || continue; cp "$f" $O/repo/; done
  ( cd $O/repo; for f in *.deb; do [ -e "$f" ] || continue
      dpkg-deb -f "$f"; printf 'Filename: ./%s\nSize: %s\nSHA256: %s\n\n' "$f" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"
    done > Packages )
fi
# Images with Acquire::GzipIndexes (Docker's Ubuntu/Debian defaults) need a compressed index.
gzip -9 -c $O/repo/Packages > $O/repo/Packages.gz
i=0
while IFS='	' read -r tool no_deps reqs; do
  [ -n "$tool" ] || continue
  i=$((i+1))
  N=; if [ "$no_deps" = 1 ]; then N=--no-deps; fi
  if [ "$tool" = uv ]; then
    UV_PYTHON_PREFERENCE=only-system /ot-uv/uv venv --seed -p python3 $W/venv$i
    $W/venv$i/bin/python -m pip wheel --wheel-dir $O/wheels $N $reqs
  else
    $tool wheel --wheel-dir $O/wheels $N $reqs
  fi
done < $W/python-requests.tsv
# R source packages and their Depends/Imports/LinkingTo closure, resolved by the image's R.
if [ -n "${OT_CRAN_PACKAGES:-}" ]; then
  mkdir -p $O/cran/src/contrib
  Rscript $W/cran-prepare.R
fi
# Original setup commands with a recording curl first on PATH (scratch HOME/workdir).
if [ -d $W/http-commands ]; then
  real=$(command -v curl) || { echo 'curl is required to record downloads' >&2; exit 1; }
  mkdir -p $O/http/bin $O/http/responses
  cp /ot-replay/http_replay.sh $O/http/bin/curl && chmod 755 $O/http/bin/curl
  for c in $W/http-commands/*.sh; do
    ( cd "$OT_HTTP_WORKDIR" && HOME=$OT_HTTP_HOME OT_HTTP_RECORD_DIR=$O/http/responses OT_HTTP_REAL_CURL=$real \
        PATH=$O/http/bin:$PATH /bin/sh -c "$(cat "$c")" )
  done
fi
'''

CRAN_PREPARE = r'''requested <- strsplit(Sys.getenv("OT_CRAN_PACKAGES"), " ")[[1]]
repos <- Sys.getenv("OT_CRAN_REPOS")
type <- Sys.getenv("OT_CRAN_TYPE")
db <- available.packages(repos = repos, type = type)
unavailable <- setdiff(requested, rownames(db))
# install.packages(dependencies = NA) installs missing Depends/Imports/LinkingTo.
deps <- tools::package_dependencies(intersect(requested, rownames(db)), db = db,
                                    which = c("Depends", "Imports", "LinkingTo"), recursive = TRUE)
wanted <- unique(c(intersect(requested, rownames(db)), unlist(deps, use.names = FALSE)))
wanted <- intersect(setdiff(wanted, rownames(installed.packages(priority = "base"))), rownames(db))
got <- download.packages(wanted, destdir = "/ot-out/cran/src/contrib", available = db,
                         repos = repos, type = type)
if (length(setdiff(wanted, got[, 1]))) stop("CRAN download incomplete: ", paste(setdiff(wanted, got[, 1]), collapse = " "))
tools::write_PACKAGES("/ot-out/cran/src/contrib", type = "source")
writeLines(unavailable, "/ot-out/cran/unavailable.txt")
writeLines(paste(R.version$major, R.version$minor, sep = "."), "/ot-out/cran/r-version.txt")
'''

CRAN_CHECK = r'''requested <- strsplit(Sys.getenv("OT_CRAN_PACKAGES"), " ")[[1]]
local <- paste0("file://", Sys.getenv("OT_CRAN_LOCAL"))
db <- available.packages(repos = local, type = "source")
unavailable <- readLines(file.path(Sys.getenv("OT_CRAN_LOCAL"), "unavailable.txt"))
wanted <- setdiff(requested, unavailable)
deps <- tools::package_dependencies(wanted, db = db, which = c("Depends", "Imports", "LinkingTo"), recursive = TRUE)
known <- c(rownames(db), rownames(installed.packages()))
missing <- setdiff(c(wanted, unlist(deps, use.names = FALSE)), known)
if (length(missing)) stop("Local CRAN repository is incomplete: ", paste(missing, collapse = " "))
cat("local CRAN repository provides", nrow(db), "packages\n")
'''

# Same staging as validation, then resolution from the staged set alone (no network use).
CHECK = r'''set -eu
T=/ot-check; W=/ot-work
export HOME=$W/home TMPDIR=$W/tmp PIP_CACHE_DIR=$W/pip-cache-check PIP_DISABLE_PIP_VERSION_CHECK=1 UV_CACHE_DIR=$W/uv-cache-check
{setup}
if [ -n "${{OT_APT:-}}" ]; then
  APT_CONFIG=$T/apt.conf apt-get -o Debug::NoLocking=1 -o APT::Sandbox::User=$(id -un) update > $W/check-update.log 2>&1
  cat $W/check-update.log
  ! grep -q '^Err:\|^W: \|^E: ' $W/check-update.log
  APT_CONFIG=$T/apt.conf apt-get -o Debug::NoLocking=1 -s install -y $OT_APT
fi
i=0
while IFS='	' read -r tool no_deps reqs; do
  [ -n "$tool" ] || continue
  i=$((i+1))
  N=; if [ "$no_deps" = 1 ]; then N=--no-deps; fi
  if [ "$tool" = uv ]; then
    UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS=$T/wheels UV_PYTHON_PREFERENCE=only-system $T/bin/uv venv -p python3 $W/check$i
    VIRTUAL_ENV=$W/check$i UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS=$T/wheels $T/bin/uv pip install --dry-run $N $reqs
  else
    PIP_NO_INDEX=1 PIP_FIND_LINKS=$T/wheels $tool download --dest $W/check$i $N $reqs
  fi
done < $W/python-requests.tsv
if [ -n "${{OT_CRAN_PACKAGES:-}}" ]; then
  OT_CRAN_LOCAL=$T/cran Rscript $W/cran-check.R
fi
# Replay only: unrecorded URLs fail like an unreachable network.
if [ -d $W/http-commands ]; then
  for c in $W/http-commands/*.sh; do
    ( cd "$OT_HTTP_WORKDIR" && HOME=$OT_HTTP_HOME PATH=$T/http/bin:$PATH /bin/sh -c "$(cat "$c")" )
  done
fi
'''


def sif_for(key, inventory, scratch, slot=0):
    """Return (image, temporary): a scratch copy of the cached image, removed after use."""
    bundle = Path(inventory[key])
    target = Path(scratch) / 'images' / key
    if bundle.is_dir():
        # Deferred bundle (base SIF + baked overlay). Never mount cache files in place:
        # even a read-only ext3 overlay mount rewrites filesystem metadata, so concurrent
        # containers each need their own copy (slot).
        copy = target / str(slot) / bundle.name
        if not copy.is_dir():
            copy.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['cp', '-r', '--sparse=always', str(bundle), str(copy)], check=True)
        return copy, True
    sifs = list(target.glob('*.sif')) if target.is_dir() else []
    if not sifs:
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(bundle) as archive:
            members = [m for m in archive if m.isfile() and m.name.endswith('.sif') and '/' not in m.name]
            if len(members) != 1:
                raise ValueError(f'Expected one SIF in {bundle}')
            archive.extract(members[0], target, filter='data')
        sifs = list(target.glob('*.sif'))
    return sifs[0], True


def _apptainer(sif, binds, env, script, timeout, writable_tmpfs=False):
    command = ['apptainer', 'exec', '--contain', '--cleanenv', '--no-home']
    if writable_tmpfs:
        command.append('--writable-tmpfs')
    if Path(sif).is_dir():
        for overlay in Path(sif).glob('*.overlay.img'):
            command += ['--overlay', f'{overlay}:ro']
        (sif,) = Path(sif).glob('*.sif')
    for source, target, mode in binds:
        command += ['--bind', f'{source}:{target}:{mode}']
    for name, value in env.items():
        command += ['--env', f'{name}={value}']
    command += [str(sif), 'sh', '-c', script]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)



def apptainer(sif, binds, env, script, timeout, writable_tmpfs=False):
    """Never mount a caller's ext3 bundle, even read-only, in place."""
    source = Path(sif)
    if not source.is_dir():
        return _apptainer(sif, binds, env, script, timeout, writable_tmpfs)
    from hpc.offline_prep.image_cache import copy_image
    (image,) = source.glob('*.sif')
    overlays = sorted(source.glob('*.overlay.img'))
    with tempfile.TemporaryDirectory(prefix='offline-source-overlay-', dir=os.environ.get('TMPDIR')) as tmp:
        private = Path(tmp)
        (private / image.name).symlink_to(image.resolve())
        checks = []
        for overlay in overlays:
            before = digest(overlay)
            target = private / overlay.name
            copy_image(overlay, target)
            if digest(target) != before:
                raise ValueError('Offline source overlay copy checksum mismatch')
            checks.append((overlay, before))
        try:
            return _apptainer(private, binds, env, script, timeout, writable_tmpfs)
        finally:
            for overlay, before in checks:
                if digest(overlay) != before:
                    raise ValueError('Offline preparation source overlay changed')


def python_requests(spec):
    lines = []
    for request in spec['python']:
        no_deps = request.get('no_deps', False)
        if not isinstance(no_deps, bool):
            raise ValueError('Offline python no_deps must be boolean')
        lines.append(request['tool'] + '\t' + str(int(no_deps)) + '\t'
                     + ' '.join(shlex.quote(q) for q in request['requirements']) + '\n')
    return ''.join(lines)


def proxy_env():
    return {k: os.environ[k] for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY')
            if os.environ.get(k)}


def validate_source(spec, source):
    """Bind deferred bundles to every source byte before private-copy execution."""
    source = Path(source)
    sidecars = ('overlay_sha256', 'deferred_sha256')
    if any(k in spec for k in sidecars):
        if not all(isinstance(spec.get(k), str) and re.fullmatch(r'[0-9a-f]{64}', spec[k])
                   for k in ('image_sha256', *sidecars)):
            raise ValueError('Offline bundle preparation requires all source checksums')
        if not source.is_dir():
            raise ValueError('Offline bundle source must be a directory')
        images = list(source.glob('*.sif'))
        if len(images) != 1:
            raise ValueError('Offline bundle must contain exactly one SIF')
        image = images[0]
        for suffix, key in (('.sif', 'image_sha256'), ('.overlay.img', 'overlay_sha256'),
                            ('.deferred.json', 'deferred_sha256')):
            paths = list(source.glob('*' + suffix))
            if paths != [image.with_suffix(suffix)] or digest(paths[0]) != spec[key]:
                raise ValueError('Offline preparation source bundle checksum mismatch: ' + key)
    elif 'image_sha256' in spec:
        if source.is_dir() or not re.fullmatch(r'[0-9a-f]{64}', spec['image_sha256']) or digest(source) != spec['image_sha256']:
            raise ValueError('Offline preparation source image checksum mismatch')


def wheelhouse_inputs():
    """Trusted controller inputs, read-only in the build and recorded by bytes."""
    configured = os.environ.get('OT_RUNTIME_WHEELHOUSE')
    if not configured:
        return [], {}, []
    root = Path(configured).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('OT_RUNTIME_WHEELHOUSE must be a directory')
    records = [{'filename': p.name, 'sha256': digest(p), 'bytes': p.stat().st_size}
               for p in sorted(root.glob('*.whl')) if p.is_file()]
    if not records:
        raise ValueError('OT_RUNTIME_WHEELHOUSE contains no wheels')
    return [(root, '/ot-preparation-wheels', 'ro')], {
        'PIP_NO_INDEX': '1', 'PIP_FIND_LINKS': '/ot-preparation-wheels',
        'UV_NO_INDEX': '1', 'UV_FIND_LINKS': '/ot-preparation-wheels'}, records


def build(spec, sif, cache, uv, timeout=1800):
    key = spec_id(spec)
    cache = Path(cache)
    manifest_path = cache / (key + '.json')
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('status') == 'ready':
            verify({**manifest, 'spec': spec}, cache, OFFLINE_FORMAT)
            return manifest
    validate_source(spec, sif)
    with tempfile.TemporaryDirectory(prefix='verifier-offline-', dir=os.environ.get('TMPDIR')) as tmp:
        tmp = Path(tmp)
        out, work, check = tmp / 'out', tmp / 'work', tmp / 'check'
        for d in (out, work, check):
            d.mkdir()
        from hpc.offline_prep.offline_sources import prepare as prepare_sources, verify_staged
        sources = prepare_sources(spec.get('sources', []), out)
        (work / 'python-requests.tsv').write_text(python_requests(spec))
        uv_dir = Path(uv).resolve().parent
        binds = [(out, '/ot-out', 'rw'), (work, '/ot-work', 'rw'), (uv_dir, '/ot-uv', 'ro')]
        wheel_binds, wheel_env, wheel_records = wheelhouse_inputs()
        binds.extend(wheel_binds)
        env = {**proxy_env(), **wheel_env, 'OT_APT': ' '.join(spec['apt'])}
        extra_env = download_inputs(spec, work)
        env.update(extra_env)
        if spec.get('http'):
            binds.append((REPLAY.parent, '/ot-replay', 'ro'))
        result = apptainer(sif, binds + scratch_binds(spec, tmp / 'record'), env, BUILD, timeout,
                           writable_tmpfs=bool(spec.get('http')))
        log = result.stdout[-4000:] + '\n' + result.stderr[-4000:]
        if result.returncode:
            return {'format': OFFLINE_FORMAT, 'id': key, 'spec': spec, 'status': 'failed',
                    'error': 'package download failed: ' + result.stderr[-1500:]}
        if spec.get('uv'):
            (out / 'bin').mkdir()
            shutil.copy2(uv, out / 'bin/uv')
        staged = tmp / 'packages.tar.gz'
        with tarfile.open(staged, 'w:gz', compresslevel=3) as t:
            for root in OFFLINE_ROOTS:
                if (out / root).exists():
                    t.add(out / root, arcname=root)
        validate_offline_archive(staged)
        # Offline resolution check: stage exactly as validation does, without proxies.
        subprocess.run(['tar', '--no-same-owner', '-xzf', str(staged), '-C', str(check)], check=True)
        verify_staged(sources, check)
        setup = offline_setup_command('/ot-check')
        checked = apptainer(sif, [(check, '/ot-check', 'rw'), (work, '/ot-work', 'rw')]
                            + scratch_binds(spec, tmp / 'replay'),
                            {'OT_APT': ' '.join(spec['apt']), **extra_env}, CHECK.format(setup=setup), timeout,
                            writable_tmpfs=bool(spec.get('http')))
        if checked.returncode:
            return {'format': OFFLINE_FORMAT, 'id': key, 'spec': spec, 'status': 'failed',
                    'error': 'offline resolution check failed: ' + checked.stderr[-1500:]}
        if wheel_records != wheelhouse_inputs()[2]:
            raise ValueError('Trusted wheelhouse changed during offline preparation')
        checksum = digest(staged)
        archive = cache / (key + '.tar.gz')
        partial = cache / (key + '.tar.gz.partial')
        with partial.open('xb') as dst, staged.open('rb') as src:
            shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
        if digest(partial) != checksum:
            raise ValueError('Verifier package copy checksum mismatch')
        # Atomic no-clobber publication: concurrent preparation or a retired
        # incomplete artifact must never replace existing identity bytes.
        os.link(partial, archive)
        partial.unlink()
        manifest = {'format': OFFLINE_FORMAT, 'id': key, 'spec': spec, 'archive_sha256': checksum,
                    'status': 'ready', 'archive_bytes': archive.stat().st_size,
                    'debs': sorted(p.name for p in (out / 'repo').glob('*.deb')),
                    'wheels': sorted(p.name for p in (out / 'wheels').glob('*.whl')),
                    **download_provenance(out),
                    'source_inputs': sources,
                    **({'wheelhouse_inputs': wheel_records} if wheel_records else {}),
                    'build_log_tail': log[-2000:]}
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        return manifest


REPLAY = Path(__file__).resolve().parents[2] / 'harbor_patches/http_replay.sh'


def download_inputs(spec, work):
    """Write CRAN/HTTP preparation inputs; return their container environment."""
    env = {}
    cran = spec.get('cran')
    if cran:
        if not cran['packages'] or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9.]*', p) for p in cran['packages']):
            raise ValueError('Invalid CRAN package request')
        (work / 'cran-prepare.R').write_text(CRAN_PREPARE)
        (work / 'cran-check.R').write_text(CRAN_CHECK)
        env.update(OT_CRAN_PACKAGES=' '.join(cran['packages']), OT_CRAN_REPOS=cran['repos'],
                   OT_CRAN_TYPE=cran.get('type', 'source'))
    http = spec.get('http')
    if http:
        commands = work / 'http-commands'
        commands.mkdir()
        for i, command in enumerate(http['commands']):
            (commands / f'{i:03d}.sh').write_text(command)
        env.update(OT_HTTP_HOME=http['home'], OT_HTTP_WORKDIR=http['workdir'])
    return env


def scratch_binds(spec, root):
    """Fresh writable HOME/workdir for recorded commands; the image stays unchanged."""
    http = spec.get('http')
    if not http:
        return []
    binds = []
    for target in dict.fromkeys((http['home'], http['workdir'])):
        source = root / target.strip('/').replace('/', '_')
        source.mkdir(parents=True, exist_ok=True)
        binds.append((source, target, 'rw'))
    return binds


def download_provenance(out):
    record = {}
    contrib = out / 'cran/src/contrib'
    if contrib.is_dir():
        record['cran_sources'] = sorted(p.name for p in contrib.glob('*.tar.gz'))
        record['cran_unavailable'] = [l for l in (out / 'cran/unavailable.txt').read_text().split() if l]
        record['cran_r_version'] = (out / 'cran/r-version.txt').read_text().strip()
    responses = out / 'http/responses'
    if responses.is_dir():
        record['http_responses'] = [{
            'url': (e / 'url').read_text(), 'http_code': (e / 'code').read_text(),
            'curl_exit': int((e / 'exit').read_text()), 'bytes': (e / 'body').stat().st_size,
            'sha256': digest(e / 'body')} for e in sorted(responses.iterdir()) if e.is_dir()]
    return record


def plan(requests, task_keys, extra=None):
    """Group tasks by (image key, request): one archive per distinct package set and image.
    extra (e.g. the image bundle digest after a rebuild) becomes part of the content address."""
    groups = {}
    for task, request in sorted(requests.items()):
        spec = {'image_key': task_keys[task], **request, **(extra or {})}
        groups.setdefault(spec_id(spec), {'spec': spec, 'tasks': []})['tasks'].append(task)
    return [groups[k] for k in sorted(groups, key=lambda k: (groups[k]['spec']['image_key'], k))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', required=True, type=Path, help='patch output verifier-offline-requests.json')
    parser.add_argument('--inventory', required=True, type=Path, help='image inventory with task_keys')
    parser.add_argument('--verified-images', required=True, type=Path, help='verified image bundles (key -> bundle)')
    parser.add_argument('--cache', type=Path, help='Defaults to OT_RUNTIME_CACHE or OT_WORKSPACE/runtimes')
    parser.add_argument('--results', required=True, type=Path, help='directory for per-shard result JSON')
    parser.add_argument('--uv', required=True, type=Path)
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--overlay-copies', type=int, default=4,
                        help='scratch copies per directory bundle (one container mounts each copy at a time)')
    parser.add_argument('--spec-extra', type=json.loads, help='JSON object added to every package-set spec')
    args = parser.parse_args()
    if args.cache is None:
        args.cache = cache_root()
    requests = json.loads(args.requests.read_text())
    task_keys = json.loads(args.inventory.read_text())['task_keys']
    images = {b['key']: b['bundle'] for b in json.loads(args.verified_images.read_text())['bundles'] if b['verified']}
    groups = plan(requests, task_keys, args.spec_extra)
    keys = sorted({g['spec']['image_key'] for g in groups})
    mine = set(keys[args.shard::args.shards])
    groups = [g for g in groups if g['spec']['image_key'] in mine]
    args.cache.mkdir(parents=True, exist_ok=True)
    args.results.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='verifier-offline-images-', dir=os.environ.get('TMPDIR')))
    results_path = args.results / f'shard-{args.shard:04d}.json'
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    from concurrent.futures import ThreadPoolExecutor
    import threading
    # Groups build in parallel; each image is extracted once and removed after its last group.
    lock = threading.Lock()
    pending = {}
    for g in groups:
        pending[g['spec']['image_key']] = pending.get(g['spec']['image_key'], 0) + 1
    import queue
    key_locks = {k: threading.Lock() for k in pending}
    free, made = {k: queue.Queue() for k in pending}, {k: [] for k in pending}

    def acquire(key):
        # SIF files are shared read-only; directory bundles get one scratch copy per container.
        with key_locks[key]:
            if free[key].empty() and (not made[key] or (
                    Path(images[key]).is_dir() and len(made[key]) < args.overlay_copies)):
                image = sif_for(key, images, scratch, len(made[key]))
                made[key].append(image)
                if not Path(images[key]).is_dir():
                    return image
                free[key].put(image)
            if not Path(images[key]).is_dir():
                return made[key][0]
        return free[key].get()

    def release(key, image):
        if Path(images[key]).is_dir():
            free[key].put(image)

    def one(g):
        key, gid = g['spec']['image_key'], spec_id(g['spec'])
        try:
            if results.get(gid, {}).get('result', {}).get('status') == 'ready':
                return results[gid]
            image = acquire(key)
            try:
                result = build(g['spec'], image[0], args.cache, args.uv)
            except Exception as exc:
                result = {'status': 'failed', 'error': repr(exc)[-1500:]}
            finally:
                release(key, image)
            return {**g, 'result': result}
        except Exception as exc:
            return {**g, 'result': {'status': 'failed', 'error': f'image unavailable: {exc}'}}
        finally:
            with lock:
                pending[key] -= 1
                last = pending[key] == 0
            if last and made[key]:
                shutil.rmtree(Path(scratch) / 'images' / key, ignore_errors=True)

    # Order by key so few images are extracted at once.
    with ThreadPoolExecutor(args.workers) as pool:
        for g in pool.map(one, groups):
            results[spec_id(g['spec'])] = g
            print(json.dumps({'key': g['spec']['image_key'], 'tasks': len(g['tasks']),
                              'status': g['result'].get('status'), 'error': g['result'].get('error', '')[-300:]}),
                  flush=True)
            with lock:
                results_path.write_text(json.dumps(results, indent=1) + '\n')


def catalog(results_dir, output):
    """Merge shard results into the patcher catalog: task -> built package set."""
    tasks = {}
    for path in sorted(Path(results_dir).glob('shard-*.json')):
        for gid, g in json.loads(path.read_text()).items():
            request = {k: g['spec'][k] for k in OFFLINE_REQUEST_KEYS if k in g['spec']}
            for task in g['tasks']:
                tasks[task] = {'request_id': spec_id(request), **g['result']}
    Path(output).write_text(json.dumps(tasks, indent=1) + '\n')
    return tasks


if __name__ == '__main__':
    if sys.argv[1:2] == ['catalog']:
        print(len(catalog(sys.argv[2], sys.argv[3])))
    else:
        main()
