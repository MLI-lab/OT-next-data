"""Fill one shared dependency cache for every task commit during image construction."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from xml.sax.saxutils import escape


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def maven_settings(path):
    # Proxy settings are temporary and never baked into the image.
    values = []
    for protocol in ('http', 'https'):
        value = os.environ.get(protocol + '_proxy') or os.environ.get(protocol.upper() + '_PROXY')
        if value:
            url = urlsplit(value)
            values.append('<proxy><id>' + protocol + '</id><active>true</active><protocol>' + protocol +
                          '</protocol><host>' + escape(url.hostname or '') + '</host><port>' + str(url.port or 80) +
                          '</port><nonProxyHosts>localhost|127.*|repo.maven.apache.org|repo1.maven.org</nonProxyHosts></proxy>')
    path.write_text('<settings><proxies>' + ''.join(values) + '</proxies></settings>')


def warm(spec):
    repo = Path('/home') / spec['repo']
    # Names only: which proxy settings this build step sees.
    print('Proxy variables: ' + (' '.join(sorted(k for k in os.environ if k.lower().endswith('_proxy'))) or 'none'), flush=True)
    original = git(repo, 'rev-parse', 'HEAD').strip()
    if git(repo, 'status', '--porcelain', '--ignored'):
        raise RuntimeError('Image checkout is not clean before cache warming')
    results = []
    with tempfile.TemporaryDirectory(prefix='multiswe-warm-') as temporary:
        if spec['kind'] == 'maven':
            settings = Path(temporary) / 'settings.xml'
            maven_settings(settings)
            # The verifier passes this file explicitly; keep it free of proxies.
            Path('/opt/multiswe-maven-settings.xml').write_text('<settings/>\n')
            # Run one test class of each commit instead of skipping tests: Surefire
            # resolves its test-framework provider only when a test runs (a filter
            # matching nothing stops before that), and the verifier needs it offline.
            # Its outcome is irrelevant here; graded tests run in the verifier.
            command = ['mvn', '-B', '-s', str(settings), '-Dmaven.repo.local=/opt/multiswe-maven',
                       '-Dmaven.test.failure.ignore=true', '-DfailIfNoTests=false',
                       '-Dsurefire.failIfNoSpecifiedTests=false', 'clean', 'test']
            # Maven Central directly over IPv6: through the shared cluster proxy it answered
            # 429 Too Many Requests (checkstyle image build, 2026-10-18), and cluster nodes
            # reach hosts directly only over IPv6. Other repositories still use the proxy.
            os.environ['MAVEN_OPTS'] = ' '.join(filter(None, (os.environ.get('MAVEN_OPTS'), '-Djava.net.preferIPv6Addresses=true')))
        else:
            command = ['go', 'mod', 'download']
        for index, sha in enumerate(spec['shas'], 1):
            git(repo, 'checkout', '-q', '-f', sha)
            started = time.monotonic()
            if spec['kind'] == 'go' and (not (repo / 'go.mod').exists() or (repo / 'vendor').exists()):
                status = 'skipped'
            else:
                run = command
                if spec['kind'] == 'maven':
                    tests = sorted(repo.glob('src/test/java/**/*Test.java'))
                    run = command[:-2] + ['-Dtest=' + (tests[0].stem if tests else 'MultisweNoTests')] + command[-2:]
                with open(Path(temporary) / 'log', 'w') as log:
                    code = subprocess.run(run, cwd=repo, stdout=log, stderr=subprocess.STDOUT).returncode
                status = 'ok' if code == 0 else 'failed'
                if code:
                    print(Path(temporary, 'log').read_text(errors='replace')[-2000:], flush=True)
            seconds = round(time.monotonic() - started, 1)
            results.append({'sha': sha, 'status': status, 'seconds': seconds})
            print(f'[{index}/{len(spec["shas"])}] {sha} {status} {seconds}s', flush=True)
            git(repo, 'clean', '-q', '-f', '-d', '-x')
    git(repo, 'checkout', '-q', '-f', original)
    git(repo, 'clean', '-q', '-f', '-d', '-x')
    if git(repo, 'status', '--porcelain', '--ignored'):
        raise RuntimeError('Image checkout is not clean after cache warming')
    failed = [r['sha'] for r in results if r['status'] == 'failed']
    print(f'Cache warming: {len(results)} commits, {len(failed)} failed: {" ".join(failed)}', flush=True)
    Path('/opt/multiswe-warm-cache.json').write_text(json.dumps({
        'repo': spec['repo'], 'kind': spec['kind'], 'commits': results}, indent=1) + '\n')
    # A missing Maven artifact makes the verifier download it without the
    # build proxy. Require a complete cache instead of a slow verifier failure.
    if spec['kind'] == 'maven' and failed:
        raise RuntimeError('Maven cache warming failed for ' + ' '.join(failed))


warm(json.loads(sys.argv[1]))
