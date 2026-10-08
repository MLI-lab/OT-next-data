"""Install one exact baseline's dependencies during image construction."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from xml.sax.saxutils import escape


def preinstall(spec, configure_gradle_proxy):
    repo = Path('/home') / spec['repo']
    properties = Path(os.environ.get('GRADLE_USER_HOME', '/opt/multiswe-gradle')) / 'gradle.properties'
    original = properties.read_bytes() if properties.exists() else None
    original_mode = properties.stat().st_mode & 0o777 if properties.exists() else None
    java = spec['repo'] in ('checkstyle', 'junit5', 'logstash', 'mockito', 'spotbugs')
    with tempfile.TemporaryDirectory(prefix='multiswe-preinstall-') as temporary:
        try:
            if java:
                configure_gradle_proxy()
                script = 'set -e\ngit reset --hard\ngit checkout ' + shlex.quote(spec['base_sha']) + '\n'
                if spec['repo'] == 'checkstyle':
                    Path('/opt/multiswe-maven-settings.xml').write_text('<settings/>\n')
                    settings = Path(temporary) / 'settings.xml'
                    values = {}
                    for protocol in ('http', 'https'):
                        value = os.environ.get(protocol + '_proxy') or os.environ.get(protocol.upper() + '_PROXY')
                        if value:
                            url = urlsplit(value)
                            # Maven proxy settings are temporary and never baked into the image.
                            values[protocol] = ('<proxy><id>' + protocol + '</id><active>true</active><protocol>' + protocol +
                                '</protocol><host>' + escape(url.hostname or '') + '</host><port>' + str(url.port or 80) +
                                '</port><nonProxyHosts>localhost|127.*|repo.maven.apache.org|repo1.maven.org</nonProxyHosts></proxy>')
                    settings.write_text('<settings><proxies>' + ''.join(values.values()) + '</proxies></settings>')
                    script += 'mvn -B -s ' + shlex.quote(str(settings)) + ' -Dmaven.repo.local=/opt/multiswe-maven -DskipTests test\n'
                else:
                    init = Path(temporary) / 'test-runtime.gradle'
                    init.write_text('''gradle.projectsEvaluated {
                        rootProject.tasks.register('multisweInstallTestRuntime') {
                            doLast {
                                rootProject.allprojects.each { p ->
                                    p.tasks.withType(org.gradle.api.tasks.testing.Test).each { t ->
                                        t.classpath.files
                                        if (t.hasProperty('javaLauncher')) t.javaLauncher.orNull
                                    }
                                }
                            }
                        }
                    }''')
                    script += ('./gradlew --no-daemon --max-workers 2 --init-script ' +
                               shlex.quote(str(init)) + ' testClasses multisweInstallTestRuntime\n')
            else:
                script = spec['electron_proxy'] + '\n' + spec['prepare']
                script = script.replace('bash /home/check_git_changes.sh', 'test -z "$(git status --porcelain)"')
                # Image creation must not silently succeed with a broken install.
                script = re.sub(r'\s*\|\|\s*true\b', '', script)
            log = Path(temporary) / 'installation.log'
            started = time.monotonic()
            print('Installing baseline dependencies for ' + spec['repo'] + '@' + spec['base_sha'], flush=True)
            with log.open('w') as output:
                with subprocess.Popen(['bash', '-euc', script], cwd=repo, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, errors='replace') as process:
                    for line in process.stdout:
                        output.write(line)
                        print(line, end='', flush=True)
                    returncode = process.wait()
            print(f'Baseline dependency installation exit={returncode}, seconds={time.monotonic()-started:.1f}', flush=True)
            if returncode:
                lines = log.read_text(errors='replace')
                marker = lines.find('* What went wrong:')
                errors = [line for line in lines.splitlines() if line.startswith(('error ', '[ERROR]'))]
                detail = lines[marker:marker + 1000] if marker >= 0 else '\n'.join(errors[:4])[:1000] or lines[-1000:]
                raise RuntimeError('Baseline dependency installation failed: ' + detail)
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
            if head != spec['base_sha']:
                raise RuntimeError('Image checkout does not match the task baseline')
            Path('/home/fix.patch').unlink(missing_ok=True)
            Path('/opt/multiswe-preinstalled.json').write_text(json.dumps({
                'repo': spec['repo'], 'base_sha': head, 'baseline_test_command_removed': java,
            }) + '\n')
        finally:
            if java:
                if original is None:
                    properties.unlink(missing_ok=True)
                else:
                    properties.write_bytes(original)
                    properties.chmod(original_mode)
