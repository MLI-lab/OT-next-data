"""Our patch to Terminal-Bench check-nproc.sh: allow measurement-only uses and audited CPU-affinity cases."""
import hashlib
import re

CHECK = 'check-nproc.sh'
ADAPTATION = (
    'training permits measurement-only variables and the exact audited InferredBugs Python 2.7.18 '
    'Dockerfile build on Maven Java 8/11/17 (Helma Slurm affinity probes 934209/934274); also permits '
    'four content-pinned SETA solutions tested with 1/2 CPUs (Helma probe 948213); other worker '
    'selection remains checked; not a Docker quota-only portability guarantee Also permits five '
    'content-pinned CalibForge image/script pairs measured at 2/4 CPUs in Helma job 1017578; nproc --all '
    'still saw 384 node CPUs.'
    '; training ignores command -v nproc availability probes while retaining subsequent bare calls')


# Content-pinned CPU-affinity audit: Helma job 1017578, all five images at 2/4 CPUs.
CALIBFORGE_REVIEWED_NPROC = {'contrastive_solver-security_20260508_045740_002': {'file': 'environment/Dockerfile',
                                                     'sha256': {'environment/Dockerfile': '8554c71778e6d80171e69483cafc1c53f016d16607b586eb8d259936c2996ad1'}},
 'contrastive_solver-software-engineering_20260510_053611_008': {'file': 'environment/Dockerfile',
                                                                 'sha256': {'environment/Dockerfile': 'fe371f2e4d5ccf5ac295269324bf2d7dbfb6d3e10377e84910f457f384f365cb'}},
 'contrastive_solver-software-engineering_20260611_210349_008': {'file': 'tests/test.sh',
                                                                 'sha256': {'environment/Dockerfile': '4f6194d3d652fd8cb9d9de5681211adf5ba57146718f2fc0db9d977d11f8d8b7',
                                                                            'tests/test.sh': 'b7f4a23eaffed3576d649078f6bb09f89b84e2a5573063b53552799af644560a'}},
 'contrastive_solver-system-administration_20260604_004411_008': {'file': 'environment/Dockerfile',
                                                                  'sha256': {'environment/Dockerfile': '47c7d9e917e98d3965d48a380c1d82a2fd774505857ccfdcc6bfdaacba388db9'}},
 'contrastive_solver-system-administration_20260604_004411_009': {'file': 'environment/Dockerfile',
                                                                  'sha256': {'environment/Dockerfile': 'bbe5c1425ac6323702e29c0faae4d27efa23de0c6202797491ed5343887a2373'}}}


def calibforge_nproc_text(text, task, relative):
    """Allow only the exact image/script pairs measured in Helma job 1017578."""
    record = CALIBFORGE_REVIEWED_NPROC.get(task.name)
    if not record or relative != record['file']:
        return text
    for filename, digest in record['sha256'].items():
        path = task / filename
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return text
    return text.replace('$(nproc)', 'VERIFIED_CPU_AFFINITY')


def nproc_check_text(text, *, dockerfile=False):
    """Conservatively recognize measurement-only variables; not a shell parser.

    Also recognize the exact Python 2 build audited under Slurm CPU affinity.
    Other build flags and unknown uses retain the original check.
    """
    # Checking for the executable does not invoke it. Keep later bare calls.
    text = re.sub(r'\bcommand\s+-v\s+nproc\b', 'command -v CPU_COUNT_TOOL', text)
    if dockerfile and re.search(r'^FROM maven:3\.9-eclipse-temurin-(?:8|11|17)$', text, re.M):
        # Match the build context and complete command, not make/nproc generally.
        # Mask only on the check's scratch copy; the actual task is unchanged.
        reviewed = (
            ' && tar -xzf "$d/python2.tgz" -C "$d" && cd "$d/Python-2.7.18" \\\n'
            ' && ./configure --prefix=/opt/python2 --disable-shared --without-ensurepip >/dev/null '
            '&& make -j"$(nproc)" >/dev/null && make install >/dev/null \\\n'
            ' && cd / && rm -rf "$d" && /opt/python2/bin/python2.7 -c "import zlib"'
        )
        text = text.replace(reviewed, reviewed.replace('$(nproc)', 'VERIFIED_CPU_AFFINITY'))
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        assignment = re.fullmatch(r'\s*([A-Za-z_]\w*)="?\$\(nproc\)"?\s*', line)
        if not assignment:
            continue
        var = assignment.group(1)
        refs = re.compile(r'\$(?:' + re.escape(var) + r'\b|\{' + re.escape(var) + r'\})')
        uses = [s.strip() for s in lines if refs.search(s) and not s.lstrip().startswith('#')]
        def measurement(s):
            # JSON fields: no command substitutions, backticks or shell chains.
            if s.startswith(('{"', '"')) and not re.search(r'\$\(|`|[;&|]', s):
                return True
            if re.fullmatch(r'if \(\( \$\(echo "\$\w+ > \$' + re.escape(var) + r'" \| bc -l\) \)\); then', s):
                return True
            return bool(re.fullmatch(r'alerts(?:\+)?=\(?"[^\n]*"\)?', s)) and not re.search(r'\$\(|`|[;&|]', s)
        if uses and all(measurement(s) for s in uses):
            lines[i] = line.replace('$(nproc)', 'CPU_MEASUREMENT')
    return ''.join(lines)
