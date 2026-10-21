"""Bake a compatible, pinned grader into the pinned Multi-SWE task images."""
from __future__ import annotations

import argparse
import copy
import io
import hashlib
import json
import re
import subprocess
import shlex
from pathlib import Path, PurePosixPath
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# Embedded build inputs retain their exact bytes so image cache keys remain stable.
EMBEDDED_FILES = {
    'grader-requirements.txt': (
        'annotated-doc==0.0.5\n'
        'annotated-types==0.8.0\n'
        'anyio==4.15.1\n'
        'bashlex==0.18\n'
        'certifi==2026.7.22\n'
        'cffi==2.1.1\n'
        'charset-normalizer==3.5.2\n'
        'click==8.5.0\n'
        'cryptography==50.0.2\n'
        'dataclasses-json==0.6.7\n'
        'docker==7.2.0\n'
        'fastapi==0.142.2\n'
        'gitdb==4.0.12\n'
        'GitPython==3.2.0\n'
        'h11==0.16.0\n'
        'idna==3.20\n'
        'markdown-it-py==4.2.0\n'
        'marshmallow==3.26.2\n'
        'mdurl==0.1.2\n'
        'multi-swe-bench @ https://files.pythonhosted.org/packages/48/ad/6b7cda600a50392c790b14ee420b9a3bb318a982a298c05f2d1c066a434f/multi_swe_bench-1.1.2.tar.gz#sha256=44944bc6608d7d9b8d4390f3ce0a3b2c69122ea6be6e35766c6fde2328f50392\n'
        'mypy_extensions==1.1.0\n'
        'opentelemetry-api==1.45.1\n'
        'packaging==26.3\n'
        'pexpect==4.9.0\n'
        'pip==24.3.1\n'
        'ptyprocess==0.7.0\n'
        'pycparser==3.0\n'
        'pydantic==2.13.5\n'
        'pydantic_core==2.46.5\n'
        'PyGithub==2.10.0\n'
        'Pygments==2.21.0\n'
        'PyJWT==2.15.1\n'
        'PyNaCl==1.6.2\n'
        'python-multipart==0.0.32\n'
        'PyYAML==6.0.3\n'
        'requests==2.34.2\n'
        'rich==15.0.0\n'
        'setuptools==80.9.0\n'
        'smmap==5.0.3\n'
        'starlette==1.7.0\n'
        'swe-rex==1.4.0\n'
        'toml==0.10.2\n'
        'tqdm==4.70.1\n'
        'typing-inspect==0.9.0\n'
        'typing-inspection==0.4.4\n'
        'typing_extensions==4.16.0\n'
        'unidiff==1.0.1\n'
        'urllib3==2.8.0\n'
        'uvicorn==0.54.0\n'
    ),
    'preinstall_cache_v8.py': (
        '"""Install one exact baseline\'s dependencies during image construction."""\n'
        'import json\n'
        'import os\n'
        'from pathlib import Path\n'
        'import re\n'
        'import shlex\n'
        'import subprocess\n'
        'import sys\n'
        'import tempfile\n'
        'import time\n'
        'from urllib.parse import urlsplit\n'
        'from xml.sax.saxutils import escape\n'
        '\n'
        '\n'
        "JUNIT_LOCAL_SNAPSHOT = '''gradle.projectsEvaluated {\n"
        "    def vintage = rootProject.findProject(':junit-vintage-engine')\n"
        '    if (vintage != null) {\n'
        '        rootProject.allprojects { p ->\n'
        '            p.configurations.configureEach { c ->\n'
        '                c.resolutionStrategy.dependencySubstitution.all { dep ->\n'
        '                    def requested = dep.requested\n'
        '                    if (requested instanceof org.gradle.api.artifacts.component.ModuleComponentSelector &&\n'
        "                        requested.group == 'org.junit.vintage' &&\n"
        "                        requested.module == 'junit-vintage-engine' &&\n"
        '                        requested.version == vintage.version.toString()) {\n'
        "                        dep.useTarget(c.resolutionStrategy.dependencySubstitution.project(':junit-vintage-engine'))\n"
        '                    }\n'
        '                }\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '}\n'
        "'''\n"
        '\n'
        '\n'
        'def javascript_install_script(spec):\n'
        "    script = spec['electron_proxy'] + '\\n' + spec['prepare']\n"
        '    script = script.replace(\'bash /home/check_git_changes.sh\', \'test -z "$(git status --porcelain)"\')\n'
        "    script = re.sub(r'\\s*\\|\\|\\s*true\\b', '', script)\n"
        "    if spec['repo'] == 'material-ui' and spec['base_sha'] == '293b579ab1dc22770921e57d1e5a51f9b90bbec0':\n"
        "        script = 'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1\\n' + script\n"
        "    if spec['repo'] == 'redux' and spec['base_sha'] == 'cefb03adfd672891e65166403cfc70709f73d6a8':\n"
        '        # Skip only the root release/test hook, retaining dependency lifecycle scripts.\n'
        '        disable = \'const fs=require("fs");const p=JSON.parse(fs.readFileSync("package.json"));delete p.scripts.prepublish;fs.writeFileSync("package.json",JSON.stringify(p));\'\n'
        '        install = (\'( backup=$(mktemp); cp package.json "$backup"; \'\n'
        '                   \'trap \\\'cp "$backup" package.json; rm -f "$backup"\\\' EXIT; \'\n'
        "                   'node -e ' + shlex.quote(disable) + '; yarn install )')\n"
        "        script = re.sub(r'(?m)^yarn install\\s*$', lambda _: install, script)\n"
        '    return script\n'
        '\n'
        '\n'
        'def preinstall(spec, configure_gradle_proxy):\n'
        "    repo = Path('/home') / spec['repo']\n"
        "    properties = Path(os.environ.get('GRADLE_USER_HOME', '/opt/multiswe-gradle')) / 'gradle.properties'\n"
        '    original = properties.read_bytes() if properties.exists() else None\n'
        '    original_mode = properties.stat().st_mode & 0o777 if properties.exists() else None\n'
        "    java = spec['repo'] in ('checkstyle', 'fastjson2', 'junit5', 'logstash', 'mockito', 'spotbugs')\n"
        "    with tempfile.TemporaryDirectory(prefix='multiswe-preinstall-') as temporary:\n"
        '        try:\n'
        '            if java:\n'
        '                configure_gradle_proxy()\n'
        "                script = 'set -e\\ngit reset --hard\\ngit checkout ' + shlex.quote(spec['base_sha']) + '\\n'\n"
        "                if spec['repo'] in ('checkstyle', 'fastjson2'):\n"
        "                    Path('/opt/multiswe-maven-settings.xml').write_text('<settings/>\\n')\n"
        "                    settings = Path(temporary) / 'settings.xml'\n"
        '                    values = {}\n'
        "                    for protocol in ('http', 'https'):\n"
        "                        value = os.environ.get(protocol + '_proxy') or os.environ.get(protocol.upper() + '_PROXY')\n"
        '                        if value:\n'
        '                            url = urlsplit(value)\n'
        '                            # Maven proxy settings are temporary and never baked into the image.\n'
        "                            values[protocol] = ('<proxy><id>' + protocol + '</id><active>true</active><protocol>' + protocol +\n"
        "                                '</protocol><host>' + escape(url.hostname or '') + '</host><port>' + str(url.port or 80) +\n"
        "                                '</port><nonProxyHosts>localhost|127.*|repo.maven.apache.org|repo1.maven.org</nonProxyHosts></proxy>')\n"
        "                    settings.write_text('<settings><proxies>' + ''.join(values.values()) + '</proxies></settings>')\n"
        "                    maven_args = ' -B -s ' + shlex.quote(str(settings)) + ' -Dmaven.repo.local=/opt/multiswe-maven '\n"
        "                    if spec['repo'] == 'fastjson2':\n"
        '                        # Keep upstream profiles and packaging; execute graded tests only in the verifier.\n'
        '                        script = spec[\'prepare\'].replace(\'bash /home/check_git_changes.sh\', \'test -z "$(git status --porcelain)"\')\n'
        "                        script = re.sub(r'\\s*\\|\\|\\s*true\\b', '', script)\n"
        "                        script = script.replace('./mvnw ', './mvnw' + maven_args + '-DskipTests ')\n"
        '                    else:\n'
        "                        script += 'mvn' + maven_args + '-DskipTests test\\n'\n"
        '                else:\n'
        "                    junit_flag = ''\n"
        "                    if spec['repo'] == 'junit5':\n"
        "                        local = Path('/opt/multiswe-junit-local.gradle')\n"
        '                        local.write_text(JUNIT_LOCAL_SNAPSHOT)\n'
        "                        junit_flag = '--init-script ' + str(local) + ' '\n"
        "                    init = Path(temporary) / 'test-runtime.gradle'\n"
        "                    init.write_text('''gradle.projectsEvaluated {\n"
        "                        rootProject.tasks.register('multisweInstallTestRuntime') {\n"
        '                            doLast {\n'
        '                                rootProject.allprojects.each { p ->\n'
        '                                    p.tasks.withType(org.gradle.api.tasks.testing.Test).each { t ->\n'
        '                                        t.classpath.files\n'
        "                                        if (t.hasProperty('javaLauncher')) t.javaLauncher.orNull\n"
        '                                    }\n'
        '                                }\n'
        '                            }\n'
        '                        }\n'
        "                    }''')\n"
        "                    script += ('./gradlew --no-daemon --max-workers 2 ' + junit_flag + '--init-script ' +\n"
        "                               shlex.quote(str(init)) + ' testClasses multisweInstallTestRuntime\\n')\n"
        '            else:\n'
        '                script = javascript_install_script(spec)\n'
        "            log = Path(temporary) / 'installation.log'\n"
        '            started = time.monotonic()\n'
        "            print('Installing baseline dependencies for ' + spec['repo'] + '@' + spec['base_sha'], flush=True)\n"
        "            with log.open('w') as output:\n"
        "                with subprocess.Popen(['bash', '-euc', script], cwd=repo, stdout=subprocess.PIPE,\n"
        "                                      stderr=subprocess.STDOUT, text=True, errors='replace') as process:\n"
        '                    for line in process.stdout:\n'
        '                        output.write(line)\n'
        "                        print(line, end='', flush=True)\n"
        '                    returncode = process.wait()\n'
        "            print(f'Baseline dependency installation exit={returncode}, seconds={time.monotonic()-started:.1f}', flush=True)\n"
        '            if returncode:\n'
        "                lines = log.read_text(errors='replace')\n"
        "                marker = lines.find('* What went wrong:')\n"
        "                errors = [line for line in lines.splitlines() if line.startswith(('error ', '[ERROR]'))]\n"
        "                detail = lines[marker:marker + 1000] if marker >= 0 else '\\n'.join(errors[:4])[:1000] or lines[-1000:]\n"
        "                raise RuntimeError('Baseline dependency installation failed: ' + detail)\n"
        "            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()\n"
        "            if head != spec['base_sha']:\n"
        "                raise RuntimeError('Image checkout does not match the task baseline')\n"
        "            Path('/home/fix.patch').unlink(missing_ok=True)\n"
        "            Path('/opt/multiswe-preinstalled.json').write_text(json.dumps({\n"
        "                'repo': spec['repo'], 'base_sha': head, 'baseline_test_command_removed': java,\n"
        "            }) + '\\n')\n"
        '        finally:\n'
        '            if java:\n'
        '                if original is None:\n'
        '                    properties.unlink(missing_ok=True)\n'
        '                else:\n'
        '                    properties.write_bytes(original)\n'
        '                    properties.chmod(original_mode)\n'
    ),
    'preinstall.py': (
        '"""Install one exact baseline\'s dependencies during image construction."""\n'
        'import json\n'
        'import os\n'
        'from pathlib import Path\n'
        'import re\n'
        'import shlex\n'
        'import subprocess\n'
        'import sys\n'
        'import tempfile\n'
        'import time\n'
        'from urllib.parse import urlsplit\n'
        'from xml.sax.saxutils import escape\n'
        '\n'
        '\n'
        "JUNIT_LOCAL_SNAPSHOT = '''gradle.projectsEvaluated {\n"
        "    def vintage = rootProject.findProject(':junit-vintage-engine')\n"
        '    if (vintage != null) {\n'
        '        rootProject.allprojects { p ->\n'
        '            p.configurations.configureEach { c ->\n'
        '                c.resolutionStrategy.dependencySubstitution.all { dep ->\n'
        '                    def requested = dep.requested\n'
        '                    if (requested instanceof org.gradle.api.artifacts.component.ModuleComponentSelector &&\n'
        "                        requested.group == 'org.junit.vintage' &&\n"
        "                        requested.module == 'junit-vintage-engine' &&\n"
        '                        requested.version == vintage.version.toString()) {\n'
        "                        dep.useTarget(c.resolutionStrategy.dependencySubstitution.project(':junit-vintage-engine'))\n"
        '                    }\n'
        '                }\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '}\n'
        "'''\n"
        '\n'
        '\n'
        'def preinstall(spec, configure_gradle_proxy):\n'
        "    repo = Path('/home') / spec['repo']\n"
        "    properties = Path(os.environ.get('GRADLE_USER_HOME', '/opt/multiswe-gradle')) / 'gradle.properties'\n"
        '    original = properties.read_bytes() if properties.exists() else None\n'
        '    original_mode = properties.stat().st_mode & 0o777 if properties.exists() else None\n'
        "    java = spec['repo'] in ('checkstyle', 'fastjson2', 'junit5', 'logstash', 'mockito', 'spotbugs')\n"
        "    with tempfile.TemporaryDirectory(prefix='multiswe-preinstall-') as temporary:\n"
        '        try:\n'
        '            if java:\n'
        '                configure_gradle_proxy()\n'
        "                script = 'set -e\\ngit reset --hard\\ngit checkout ' + shlex.quote(spec['base_sha']) + '\\n'\n"
        "                if spec['repo'] in ('checkstyle', 'fastjson2'):\n"
        "                    Path('/opt/multiswe-maven-settings.xml').write_text('<settings/>\\n')\n"
        "                    settings = Path(temporary) / 'settings.xml'\n"
        '                    values = {}\n'
        "                    for protocol in ('http', 'https'):\n"
        "                        value = os.environ.get(protocol + '_proxy') or os.environ.get(protocol.upper() + '_PROXY')\n"
        '                        if value:\n'
        '                            url = urlsplit(value)\n'
        '                            # Maven proxy settings are temporary and never baked into the image.\n'
        "                            values[protocol] = ('<proxy><id>' + protocol + '</id><active>true</active><protocol>' + protocol +\n"
        "                                '</protocol><host>' + escape(url.hostname or '') + '</host><port>' + str(url.port or 80) +\n"
        "                                '</port><nonProxyHosts>localhost|127.*|repo.maven.apache.org|repo1.maven.org</nonProxyHosts></proxy>')\n"
        "                    settings.write_text('<settings><proxies>' + ''.join(values.values()) + '</proxies></settings>')\n"
        "                    maven_args = ' -B -s ' + shlex.quote(str(settings)) + ' -Dmaven.repo.local=/opt/multiswe-maven '\n"
        "                    if spec['repo'] == 'fastjson2':\n"
        '                        # Keep upstream profiles and packaging; execute graded tests only in the verifier.\n'
        '                        script = spec[\'prepare\'].replace(\'bash /home/check_git_changes.sh\', \'test -z "$(git status --porcelain)"\')\n'
        "                        script = re.sub(r'\\s*\\|\\|\\s*true\\b', '', script)\n"
        "                        script = script.replace('./mvnw ', './mvnw' + maven_args + '-DskipTests ')\n"
        '                    else:\n'
        "                        script += 'mvn' + maven_args + '-DskipTests test\\n'\n"
        '                else:\n'
        "                    junit_flag = ''\n"
        "                    if spec['repo'] == 'junit5':\n"
        "                        local = Path('/opt/multiswe-junit-local.gradle')\n"
        '                        local.write_text(JUNIT_LOCAL_SNAPSHOT)\n'
        "                        junit_flag = '--init-script ' + str(local) + ' '\n"
        "                    init = Path(temporary) / 'test-runtime.gradle'\n"
        "                    init.write_text('''gradle.projectsEvaluated {\n"
        "                        rootProject.tasks.register('multisweInstallTestRuntime') {\n"
        '                            doLast {\n'
        '                                rootProject.allprojects.each { p ->\n'
        '                                    p.tasks.withType(org.gradle.api.tasks.testing.Test).each { t ->\n'
        '                                        t.classpath.files\n'
        "                                        if (t.hasProperty('javaLauncher')) t.javaLauncher.orNull\n"
        '                                    }\n'
        '                                }\n'
        '                            }\n'
        '                        }\n'
        "                    }''')\n"
        "                    script += ('./gradlew --no-daemon --max-workers 2 ' + junit_flag + '--init-script ' +\n"
        "                               shlex.quote(str(init)) + ' testClasses multisweInstallTestRuntime\\n')\n"
        '            else:\n'
        "                script = spec['electron_proxy'] + '\\n' + spec['prepare']\n"
        '                script = script.replace(\'bash /home/check_git_changes.sh\', \'test -z "$(git status --porcelain)"\')\n'
        '                # Image creation must not silently succeed with a broken install.\n'
        "                script = re.sub(r'\\s*\\|\\|\\s*true\\b', '', script)\n"
        "                if spec['repo'] == 'material-ui' and spec['base_sha'] == '293b579ab1dc22770921e57d1e5a51f9b90bbec0':\n"
        "                    script = 'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1\\n' + script\n"
        "                if spec['repo'] == 'redux' and spec['base_sha'] == 'cefb03adfd672891e65166403cfc70709f73d6a8':\n"
        '                    # Skip only the root release/test hook, retaining dependency lifecycle scripts.\n'
        '                    disable = \'const fs=require("fs");const p=JSON.parse(fs.readFileSync("package.json"));delete p.scripts.prepublish;fs.writeFileSync("package.json",JSON.stringify(p));\'\n'
        '                    install = (\'( backup=$(mktemp); cp package.json "$backup"; \'\n'
        '                               \'trap \\\'cp "$backup" package.json; rm -f "$backup"\\\' EXIT; \'\n'
        "                               'node -e ' + shlex.quote(disable) + '; yarn install )')\n"
        "                    script = re.sub(r'(?m)^yarn install\\s*$', lambda _: install, script)\n"
        "            log = Path(temporary) / 'installation.log'\n"
        '            started = time.monotonic()\n'
        "            print('Installing baseline dependencies for ' + spec['repo'] + '@' + spec['base_sha'], flush=True)\n"
        "            with log.open('w') as output:\n"
        "                with subprocess.Popen(['bash', '-euc', script], cwd=repo, stdout=subprocess.PIPE,\n"
        "                                      stderr=subprocess.STDOUT, text=True, errors='replace') as process:\n"
        '                    for line in process.stdout:\n'
        '                        output.write(line)\n'
        "                        print(line, end='', flush=True)\n"
        '                    returncode = process.wait()\n'
        "            print(f'Baseline dependency installation exit={returncode}, seconds={time.monotonic()-started:.1f}', flush=True)\n"
        '            if returncode:\n'
        "                lines = log.read_text(errors='replace')\n"
        "                marker = lines.find('* What went wrong:')\n"
        "                errors = [line for line in lines.splitlines() if line.startswith(('error ', '[ERROR]'))]\n"
        "                detail = lines[marker:marker + 1000] if marker >= 0 else '\\n'.join(errors[:4])[:1000] or lines[-1000:]\n"
        "                raise RuntimeError('Baseline dependency installation failed: ' + detail)\n"
        "            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()\n"
        "            if head != spec['base_sha']:\n"
        "                raise RuntimeError('Image checkout does not match the task baseline')\n"
        "            Path('/home/fix.patch').unlink(missing_ok=True)\n"
        "            Path('/opt/multiswe-preinstalled.json').write_text(json.dumps({\n"
        "                'repo': spec['repo'], 'base_sha': head, 'baseline_test_command_removed': java,\n"
        "            }) + '\\n')\n"
        '        finally:\n'
        '            if java:\n'
        '                if original is None:\n'
        '                    properties.unlink(missing_ok=True)\n'
        '                else:\n'
        '                    properties.write_bytes(original)\n'
        '                    properties.chmod(original_mode)\n'
    ),
    'extract_fix_patch.py': (
        '"""Extract agent changes without staging dependency trees or overflowing argv."""\n'
        'import os\n'
        'from pathlib import Path\n'
        'import subprocess\n'
        'import sys\n'
        '\n'
        '\n'
        'def git(*args, **kwargs):\n'
        "    return subprocess.run(['git', '--literal-pathspecs', *args], check=True, **kwargs)\n"
        '\n'
        '\n'
        'def names(*args):\n'
        "    return [os.fsdecode(p) for p in git(*args, stdout=subprocess.PIPE).stdout.split(b'\\0') if p]\n"
        '\n'
        '\n'
        'def batches(paths):\n'
        '    batch, size = [], 0\n'
        '    for path in paths:\n'
        '        length = len(os.fsencode(path)) + 1\n'
        '        if batch and size + length > 32768:\n'
        '            yield batch\n'
        '            batch, size = [], 0\n'
        '        batch.append(path)\n'
        '        size += length\n'
        '    if batch:\n'
        '        yield batch\n'
        '\n'
        '\n'
        'def main():\n'
        '    repo, base, output = sys.argv[1:4]\n'
        '    os.chdir(repo)\n'
        "    baseline = Path(git('rev-parse', '--git-path', 'multiswe-setup-untracked', stdout=subprocess.PIPE,\n"
        '                        text=True).stdout.strip())\n'
        "    existing = set(os.fsdecode(p) for p in baseline.read_bytes().split(b'\\0') if p) if baseline.exists() else set()\n"
        "    untracked = [p for p in names('ls-files', '--others', '--exclude-standard', '-z') if p not in existing]\n"
        '    for batch in batches(untracked):\n'
        "        git('add', '--intent-to-add', '--', *batch)\n"
        "    changed = names('diff', '--name-only', '--no-renames', '-z', base, '--')\n"
        "    # Preserve the upstream dataset's fix/test path split.\n"
        "    fixes = [p for p in changed if not any(s in p.lower() for s in ('test', 'e2e', 'testing'))]\n"
        "    with open(output, 'wb') as stream:\n"
        '        for batch in batches(fixes):\n'
        "            git('diff', '--binary', '--no-renames', base, '--', *batch, stdout=stream)\n"
        '    if len(changed) != len(fixes):\n'
        "        print(f'Dropped {len(changed) - len(fixes)} test-like file(s) from Multi-SWE fix patch.', file=sys.stderr)\n"
        "    git('reset', '--hard', base, stdout=subprocess.DEVNULL)\n"
        '    for batch in batches(untracked):\n'
        "        git('clean', '-fd', '--', *batch, stdout=subprocess.DEVNULL)\n"
        '\n'
        '\n'
        "if __name__ == '__main__':\n"
        '    main()\n'
    ),
    'gradle_proxy.py': (
        '"""Configure Gradle\'s JVM proxy from the task runtime\'s proxy environment."""\n'
        'import os\n'
        'from pathlib import Path\n'
        'from urllib.parse import unquote, urlsplit\n'
        'from urllib.error import HTTPError\n'
        'from urllib.request import ProxyHandler, build_opener\n'
        '\n'
        '\n'
        'def properties(env):\n'
        '    result = {}\n'
        "    for protocol in ('http', 'https'):\n"
        "        value = env.get(protocol + '_proxy') or env.get(protocol.upper() + '_PROXY')\n"
        '        if not value:\n'
        '            continue\n'
        "        url = urlsplit(value if '://' in value else 'http://' + value)\n"
        "        if url.scheme != 'http' or not url.hostname:\n"
        "            raise ValueError('Gradle proxy requires an HTTP proxy URL')\n"
        "        prefix = 'systemProp.' + protocol + '.'\n"
        "        result[prefix + 'proxyHost'] = url.hostname\n"
        "        result[prefix + 'proxyPort'] = str(url.port or 80)\n"
        '        if url.username:\n'
        "            result[prefix + 'proxyUser'] = unquote(url.username)\n"
        '        if url.password:\n'
        "            result[prefix + 'proxyPassword'] = unquote(url.password)\n"
        '    if result:\n'
        "        hosts = ['localhost', '127.*', '[::1]']\n"
        "        for host in (env.get('no_proxy') or env.get('NO_PROXY') or '').split(','):\n"
        '            host = host.strip()\n'
        "            if host and '/' not in host and ':' not in host:\n"
        "                hosts.append('*' + host if host.startswith('.') else host)\n"
        "        result['systemProp.http.nonProxyHosts'] = '|'.join(hosts)\n"
        '    return result\n'
        '\n'
        '\n'
        'def main():\n'
        '    values = properties(os.environ)\n'
        '    if not values:\n'
        '        return\n'
        '    # Some cluster proxies share a rate-limited Maven Central exit IP. Bypass\n'
        '    # only after observing 429 through that proxy and verifying direct access.\n'
        "    proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')\n"
        '    if proxy:\n'
        "        for host in ('repo.maven.apache.org', 'repo1.maven.org', 'plugins.gradle.org'):\n"
        "            prefix = 'm2' if host == 'plugins.gradle.org' else 'maven2'\n"
        "            url = f'https://{host}/{prefix}/org/jetbrains/kotlin/kotlin-stdlib/1.9.24/kotlin-stdlib-1.9.24.pom'\n"
        '            try:\n'
        "                with build_opener(ProxyHandler({'https': proxy})).open(url, timeout=5) as response:\n"
        '                    response.read()\n'
        '            except HTTPError as exc:\n'
        '                if exc.code != 429:\n'
        '                    continue\n'
        '                try:\n'
        '                    with build_opener(ProxyHandler({})).open(url, timeout=5) as response:\n'
        "                        if response.status != 200 or b'<artifactId>kotlin-stdlib</artifactId>' not in response.read():\n"
        '                            continue\n'
        '                except OSError:\n'
        '                    continue\n'
        "                values['systemProp.http.nonProxyHosts'] += '|' + host\n"
        "                print(f'Gradle: verified direct access to {host} after proxy HTTP 429')\n"
        '            except OSError:\n'
        '                pass\n'
        "    path = Path(os.environ.get('GRADLE_USER_HOME', str(Path.home() / '.gradle'))) / 'gradle.properties'\n"
        '    path.parent.mkdir(parents=True, exist_ok=True)\n'
        "    old = path.read_text() if path.exists() else ''\n"
        "    keys = {line.split('=', 1)[0].strip() for line in old.splitlines() if '=' in line}\n"
        '    def escape(value):\n'
        "        return value.replace('\\\\', '\\\\\\\\').replace('\\n', '\\\\n').replace('\\r', '\\\\r')\n"
        "    with path.open('a') as stream:\n"
        "        stream.write('\\n' + ''.join(k + '=' + escape(v) + '\\n' for k, v in values.items() if k not in keys))\n"
        '    path.chmod(0o600)\n'
        '\n'
        '\n'
        "if __name__ == '__main__':\n"
        '    main()\n'
    ),
    'base-images.lock.json': (
        '{\n'
        '  "mswebench/alibaba_m_fastjson2:base": "mswebench/alibaba_m_fastjson2@sha256:af5482f01949b8b689d00f164d45c574dc619c9a348ae74adf8eb7eb5b8a20ca",\n'
        '  "mswebench/anuraghazra_m_github-readme-stats:base": "mswebench/anuraghazra_m_github-readme-stats@sha256:8cc48464ec40ca6304d07b4c0a7bd65a392cabf23d3aabec622560fd6c58d65b",\n'
        '  "mswebench/axios_m_axios:base": "mswebench/axios_m_axios@sha256:7707c562abfc8595a28f4370ff8835c304d620bc3618d8d1e20882720fa8ea8d",\n'
        '  "mswebench/beego_m_beego:base": "mswebench/beego_m_beego@sha256:8b348e45db6f04e6e397933927a21e2cca687cba38fa12f4f68bba93b5dc13b8",\n'
        '  "mswebench/caddyserver_m_caddy:base": "mswebench/caddyserver_m_caddy@sha256:cd674d3f8f492fa313c35cf40963c22145c2c815f530494be978df77d777864b",\n'
        '  "mswebench/caolan_m_async:base": "mswebench/caolan_m_async@sha256:6c518c36364ae5834bbf01e6c7e5f268a7198cae30215ce8ddd37d82690b426c",\n'
        '  "mswebench/checkstyle_m_checkstyle:base": "mswebench/checkstyle_m_checkstyle@sha256:d7f2ab8f1c54085642ddc01064946e7f8be0c176552bd278c3bd955216049074",\n'
        '  "mswebench/cli_m_cli:base": "mswebench/cli_m_cli@sha256:801072bdc63db1cc524260ff1b31a307445e19ea7bbae6a12835698161e8e324",\n'
        '  "mswebench/colinhacks_m_zod:base": "mswebench/colinhacks_m_zod@sha256:a8b6079dbfa81eabfbf72d7bcdade218f7960fe5c6b4ae898e0090fe0b4f5feb",\n'
        '  "mswebench/darkreader_m_darkreader:base": "mswebench/darkreader_m_darkreader@sha256:124d8e241413d9308777967251616cb8a35ba3f4852c5663f657d7231d3894d9",\n'
        '  "mswebench/elastic_m_logstash:base": "mswebench/elastic_m_logstash@sha256:ea6fc649df945c3df403613ddce13e5e16c9daf9182f7156ed057ec50307cbab",\n'
        '  "mswebench/etcd-io_m_etcd:base": "mswebench/etcd-io_m_etcd@sha256:f10c75dd319d11cc3611f46b32edff873f6b6df0e7f7c632f0af9733fa3ce0e1",\n'
        '  "mswebench/expressjs_m_express:base": "mswebench/expressjs_m_express@sha256:cb2bfa48685954c0a7f246b062ecdde9b7067d09065c13d711981ab632d9db63",\n'
        '  "mswebench/facebook_m_zstd:base": "mswebench/facebook_m_zstd@sha256:e585fef372bb5f6f97a7540d3e9fb38cb8a3a039190258e4b858bb77e39ec926",\n'
        '  "mswebench/fatedier_m_frp:base": "mswebench/fatedier_m_frp@sha256:1eec40809c47003bd550517d2dc73fbe553f7d10c2fdd06db49e823269976a07",\n'
        '  "mswebench/go-gorm_m_gorm:base": "mswebench/go-gorm_m_gorm@sha256:4dd9ceb1b54e3791ff71db5a908cbd06e4751f548e3d55497a6fe5d1695352f6",\n'
        '  "mswebench/gohugoio_m_hugo:base": "mswebench/gohugoio_m_hugo@sha256:a6eb6ead11717756b25c2a98d1911c16e3d9a2df5daf637599c947ba87cd3f8f",\n'
        '  "mswebench/google_m_zx:base": "mswebench/google_m_zx@sha256:8b62b706996794bf76dc165919412ad5fff0324022f9be8b056bbc7d4db81bc7",\n'
        '  "mswebench/iamkun_m_dayjs:base": "mswebench/iamkun_m_dayjs@sha256:7bccb78f8161a2c5d0ae57b8aa55b90bcd0ac92106c2ad3b7967a4e9e122dd3a",\n'
        '  "mswebench/istio_m_istio:base": "mswebench/istio_m_istio@sha256:328d3d151933b6b725634740063210ae6e96970ec1cb64bc6737aac45f019d8f",\n'
        '  "mswebench/jesseduffield_m_lazygit:base": "mswebench/jesseduffield_m_lazygit@sha256:e8e4c31f8b7930614817dbe33fa9f7b31e00ba0804883ce3354cbb3b3e0279d8",\n'
        '  "mswebench/jqlang_m_jq:base": "mswebench/jqlang_m_jq@sha256:8e3b31d703046a38bee2fe0ef27151d3bc44aaff2505fbba4e3da23ee667d2c3",\n'
        '  "mswebench/junegunn_m_fzf:base": "mswebench/junegunn_m_fzf@sha256:137faf1d45214b55b05a21db2750fffe9d920216e8aed050bab3fe600069c160",\n'
        '  "mswebench/junit-team_m_junit5:base": "mswebench/junit-team_m_junit5@sha256:2c1ca9b9c50cb132ac7e7798f68b89807cff7e67c1a9ac70ee83300475c96507",\n'
        '  "mswebench/junit-team_m_junit5:base-JDK-17": "mswebench/junit-team_m_junit5@sha256:d3c8968c4988a2a2dcd0b4bd3f6c8d896d7c3e375269c35f87c190911d861d19",\n'
        '  "mswebench/kong_m_insomnia:base": "mswebench/kong_m_insomnia@sha256:25593314d127cf0a82aaeb67d6aa2b2e6b957c7dcba94641ada7022b342d4c25",\n'
        '  "mswebench/labstack_m_echo:base": "mswebench/labstack_m_echo@sha256:ede76736dd38fe3c39a90710eba3d902ed994f75928a77e8283fdd7e8a91f783",\n'
        '  "mswebench/libsdl-org_m_sdl:base": "mswebench/libsdl-org_m_sdl@sha256:715635ef142b55d55d8e987cc1d9df2831749cbb4b6218c3c769c811527d398c",\n'
        '  "mswebench/mockito_m_mockito:base": "mswebench/mockito_m_mockito@sha256:2e502e4a38aba9490958b640df9b80ef44f0ba6ff430ee544dc10fb2c56bfb60",\n'
        '  "mswebench/mruby_m_mruby:base": "mswebench/mruby_m_mruby@sha256:94c5cee68f8565d2b8c39d7e0288dd2fef92ad6d9f7e1083ae35128a33923bbc",\n'
        '  "mswebench/mruby_m_mruby:base-cpp-11": "mswebench/mruby_m_mruby@sha256:7532c8ef7f9fc425fcea2c9f8f5ded4b86dd160f6eef2428af4b2eaadb989ca0",\n'
        '  "mswebench/mui_m_material-ui:base": "mswebench/mui_m_material-ui@sha256:8f0ca2539e9f4d116a2498980020b001da61d5cb712c760daa90bfaf76a6ec95",\n'
        '  "mswebench/mui_m_material-ui:base40180": "mswebench/mui_m_material-ui@sha256:47cd636fff4d35ca1bb2ce5a65d5642d0aad8afd99642b997374abe8799f08e3",\n'
        '  "mswebench/nektos_m_act:base": "mswebench/nektos_m_act@sha256:c86fe84ffbe9b85adca7c0637ae470cea5efa5c77298ce63e81a5703e2f21b9a",\n'
        '  "mswebench/nuxt_m_nuxt:base": "mswebench/nuxt_m_nuxt@sha256:c282f561fea935a85e32325bafa16b53043c7b931f37d5d7dfd56f24e28606e4",\n'
        '  "mswebench/openmathlib_m_openblas:base": "mswebench/openmathlib_m_openblas@sha256:43b80982eea796964c3b972e11ef62acf94620b07d3fe31719617f8dcb4c224a",\n'
        '  "mswebench/prometheus_m_prometheus:base": "mswebench/prometheus_m_prometheus@sha256:9b956be0aad877cd8aca70f4242060ba5e89db3256a56ce304932ccc4a872103",\n'
        '  "mswebench/reduxjs_m_redux:base": "mswebench/reduxjs_m_redux@sha256:195e698648484e437f6c39c617e5bc723974ee170ec557f99aa1ea53d9abd899",\n'
        '  "mswebench/remix-run_m_react-router:base": "mswebench/remix-run_m_react-router@sha256:e58b377964c40a40b383bcfd5eb311905822dbf5d3712a9b333b3cd62ee5b47e",\n'
        '  "mswebench/spotbugs_m_spotbugs:base": "mswebench/spotbugs_m_spotbugs@sha256:f829dc040cd0fd9b1854e991362260e4e5d55f3742f5ae75512738cd28a96f36",\n'
        '  "mswebench/spotbugs_m_spotbugs:base-jdk-17": "mswebench/spotbugs_m_spotbugs@sha256:99484b1394da69570c1db3ddbafc0bd620dc16c759b83c58ce24bdd6fceb7ff7",\n'
        '  "mswebench/sveltejs_m_svelte:base8": "mswebench/sveltejs_m_svelte@sha256:7c9770877662114728cbd77e46a19dacdb1b3b38279a850a0ae65c7f2d790774",\n'
        '  "mswebench/sveltejs_m_svelte:base9": "mswebench/sveltejs_m_svelte@sha256:b13712f1032a4a007920f528ef90246e90111c29d0a845c16fbf89a3c6a1aca0",\n'
        '  "mswebench/syncthing_m_syncthing:base": "mswebench/syncthing_m_syncthing@sha256:768b131dd248040e463634c08c1ffcb860134aca361c7051b12543241ebba607",\n'
        '  "mswebench/tj_m_commander.js:base": "mswebench/tj_m_commander.js@sha256:6386ac92e545ca57547d1179da523a3b365464f1653738933f6c09177707de35",\n'
        '  "mswebench/trpc_m_trpc:base": "mswebench/trpc_m_trpc@sha256:be994fee0f1c0e9f2f26ddbc85e7b1bfd2e50db96cd368c70e87de7ebc910d85",\n'
        '  "mswebench/vuejs_m_core:base": "mswebench/vuejs_m_core@sha256:b8c7399b4bd684117e99f8569c64c2586187ebff74814e8a8ff77228256c5ad1",\n'
        '  "mswebench/zeromicro_m_go-zero:base": "mswebench/zeromicro_m_go-zero@sha256:27987d56a61cd1017dda0708914479271102506d0b6997e6c8bb610e07ca875f"\n'
        '}\n'
    ),
    'maven_proxy.py': (
        '"""Give Maven\'s bootstrap JVM the build proxy before settings.xml is loaded."""\n'
        'from contextlib import contextmanager\n'
        'import os\n'
        '\n'
        '\n'
        '@contextmanager\n'
        'def maven_wrapper_proxy(proxy_properties):\n'
        "    original = os.environ.get('MAVEN_OPTS')\n"
        '    # The wrapper downloads Maven before Maven can read its proxy settings.\n'
        '    # Reuse the JVM proxy and bypass rules used for Gradle on this cluster.\n'
        '    values = proxy_properties(os.environ)\n'
        "    options = ' '.join('-D' + key.removeprefix('systemProp.') + '=' + value\n"
        '                       for key, value in values.items())\n'
        '    try:\n'
        '        if options:\n'
        "            os.environ['MAVEN_OPTS'] = ' '.join(filter(None, (original, options)))\n"
        '        yield\n'
        '    finally:\n'
        '        if original is None:\n'
        "            os.environ.pop('MAVEN_OPTS', None)\n"
        '        else:\n'
        "            os.environ['MAVEN_OPTS'] = original\n"
    ),
}

REVISION = '80de95c62ac792c99dcfa8e26569bcd7d036bdc3'
MARKER = '# Multi-SWE pinned grader runtime v4'
TASK_MEMORY_MB = {'gohugoio__hugo-12703': 8192}
SPOTBUGS_VERIFIER_FIXTURES = {
    'spotbugs__spotbugs-2872': 'spotbugsTestCases/src/java/ghIssues/Issue453.java',
    'spotbugs__spotbugs-2887': 'spotbugsTestCases/src/java/ghIssues/Issue608.java',
    'spotbugs__spotbugs-3032': 'spotbugsTestCases/src/java/ghIssues/Issue2968.java',
}


def repair_spotbugs_verifier_fixtures(files, task_id):
    """Supply regression fixtures discarded by the upstream fix/test path split."""
    fixture = SPOTBUGS_VERIFIER_FIXTURES.get(task_id)
    target = 'setup_files/upstream/test.patch'
    if fixture is None or target not in files:
        return files
    header = f'diff --git a/{fixture} b/{fixture}\n'.encode()
    blocks = re.split(rb'(?m)(?=^diff --git )', files['solution/gold.patch'])
    matches = [block for block in blocks if block.startswith(header)]
    if len(matches) != 1 or b'\n--- /dev/null\n' not in matches[0]:
        raise ValueError(f'Expected one new regression fixture for {task_id}')
    test_patch = files[target]
    if header in test_patch:
        if matches[0].rstrip(b'\n') not in test_patch:
            raise ValueError(f'Conflicting regression fixture for {task_id}')
        return files
    # The fixture is a verifier input, not the production fix. Preserve the
    # original reference and assertions; apply the fixture after extraction
    # resets the repository, for both oracle and unchanged-baseline trials.
    return {**files, target: test_patch.rstrip(b'\n') + b'\n' + matches[0]}


def normalize_hugo_output(output):
    """Separate Go test results appended to a warning without a newline."""
    import re
    return re.sub(
        r'(?m)(?<=[^\n])(?=--- (?:PASS|FAIL|SKIP): \S+ \([0-9]+(?:\.[0-9]+)?s\)[ \t]*$)',
        '\n', output)


def repair_hugo_result_lines(files, task_id):
    if task_id != 'gohugoio__hugo-13005':
        return files
    import inspect
    grade = files['tests/grade.py'].decode()
    if 'def normalize_hugo_output(' in grade:
        return files
    anchor = '    reward_path = Path(sys.argv[3])'
    if grade.count('def main():') != 1 or grade.count(anchor) != 1:
        raise ValueError('Unknown Hugo grader structure')
    grade = grade.replace('def main():', inspect.getsource(normalize_hugo_output) + '\n\ndef main():')
    grade = grade.replace(anchor, '    output = normalize_hugo_output(output)\n' + anchor)
    return {**files, 'tests/grade.py': grade.encode()}


def apply_task_memory(files, task_id):
    """Apply the operator-selected budget; expected no-op OOM is reviewed separately."""
    if task_id not in TASK_MEMORY_MB:
        return files
    updated = dict(files)
    updated['task.toml'], count = re.subn(
        rb'(?m)^memory_mb[ \t]*=[ \t]*\d+[ \t]*$',
        f'memory_mb = {TASK_MEMORY_MB[task_id]}'.encode(), files['task.toml'])
    if count != 1:
        raise ValueError('Expected one task memory setting')
    return updated

# Bullseye packages moved off the live mirrors after its LTS end in August 2026.
# Keep signature checking; only relax archive Release-file expiration checks.
BULLSEYE_ARCHIVE = (
    '. /etc/os-release; if [ "$ID" = debian ] && [ "${VERSION_CODENAME:-}" = bullseye ]; then '
    'for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do '
    '[ -f "$source_file" ] || continue; '
    "sed -i -E 's@https?://deb\\.debian\\.org/debian@https://archive.debian.org/debian@g; "
    "s@https?://security\\.debian\\.org/debian-security@https://archive.debian.org/debian-security@g' \"$source_file\"; "
    'done; printf \'%s\\n\' \'Acquire::Check-Valid-Until "false";\' > /etc/apt/apt.conf.d/99-multiswe-archive; fi'
)

PYTHON_INSTALL = (
    'set -eu; python_build_dir=$(mktemp -d /tmp/multiswe-python.XXXXXX); '
    'trap \'rm -rf "$python_build_dir"\' EXIT; '
    "curl --fail --location --retry 3 'https://github.com/astral-sh/python-build-standalone/releases/download/20250818/cpython-3.11.13%2B20250818-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz' "
    '-o "$python_build_dir/python.tar.gz"; '
    'printf \'%s  %s\\n\' 22232b7e892726fbf898e449cdae9ccfabf080319655575a8c5e54a39b553c96 "$python_build_dir/python.tar.gz" | sha256sum --check -; '
    'mkdir -p /opt/multiswe-python; tar -xzf "$python_build_dir/python.tar.gz" --strip-components=1 -C /opt/multiswe-python'
)


def docker_setup():
    # Embed all setup and pins in the exported Dockerfile. No COPY dependency:
    # the Apptainer deferred-build path executes RUN before replaying COPY.
    requirements = EMBEDDED_FILES['grader-requirements.txt'].splitlines()
    pins = ' '.join(shlex.quote(line) for line in requirements if line.strip())
    commands = [
        BULLSEYE_ARCHIVE,
        # Complete tmux package installation in single-UID Apptainer builds.
        # Privileged utmp accounting is unnecessary for task terminals.
        'if ! dpkg-statoverride --list /usr/lib/x86_64-linux-gnu/utempter/utempter; then dpkg-statoverride --add root root 0755 /usr/lib/x86_64-linux-gnu/utempter/utempter; fi',
        'apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tmux && tmux -V && test -z "$(dpkg --audit)"',
        PYTHON_INSTALL,
        '/opt/multiswe-python/bin/python3 -m pip install --no-cache-dir --disable-pip-version-check ' + pins,
        "/opt/multiswe-python/bin/python3 -c 'from multi_swe_bench.harness.dataset import Dataset; from multi_swe_bench.harness.instance import Instance'",
    ]
    return '\n' + MARKER + '\nUSER root\n' + ''.join('RUN ' + command + '\n' for command in commands)


def patched_image_prepare(recipe):
    """Repair only the baseline install command for known Multi-SWE task families."""
    script = recipe['files']['prepare.sh']
    if recipe['repo'] == 'material-ui':
        # The pinned Multi-SWE material-ui verifiers run unit tests only.
        script = 'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1\n' + script
        # These baselines use Puppeteer 1.x, which predates the
        # PUPPETEER_SKIP_DOWNLOAD environment variable.
        if recipe['base_sha'] in {
                '0543dd7bd0f8bacf850780e16d48b0c8aa6f4df7',
                'dfc94e03d391384155cbdd1a0e3b0fe16e2e1042',
                '161fb8565f532df5766b0e152f21e4a88a8d8baa'}:
            script = 'export PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=1\n' + script
    if recipe['repo'] == 'async':
        # Some pinned commits have package manifests newer than their lockfiles.
        script = re.sub(r'(?<!\S)npm ci\b', 'npm install --no-audit --no-fund', script)
    if recipe['repo'] == 'redux' and re.search(r'(?m)^\s*yarn install\b', script):
        # Skip only the legacy root prepublish hook for dependency installation.
        script = re.sub(r'\s*\|\|\s*true\b', '', script)
        disabled_hooks = 'delete p.scripts.prepublish;'
        if recipe['base_sha'] in {
                'beb1fc29ca6ebe45226caa3a064476072cd9ed26',
                '51365e5b4a5c1b0b7ff4f40b89452eb5edf4b0b9'}:
            # These commits run the root test/build hook through `prepare`
            # during yarn install. Stage 3 runs the actual task tests later.
            disabled_hooks += 'delete p.scripts.prepare;'
        disable = ('const fs=require("fs");const p=JSON.parse(fs.readFileSync("package.json"));'
                   + disabled_hooks + 'fs.writeFileSync("package.json",JSON.stringify(p));')
        install = ('( backup=$(mktemp); cp package.json "$backup"; '
                   'trap \'cp "$backup" package.json; rm -f "$backup"\' EXIT; '
                   'node -e ' + shlex.quote(disable) + '; yarn install )')
        script = re.sub(r'(?m)^yarn install\s*$', lambda _: install, script)
    return script


def patch_files(files):
    """Keep task/reference semantics; make grader infrastructure failures explicit."""
    files = dict(files)
    docker = files['environment/Dockerfile'].decode()
    if MARKER in docker:
        return files
    test = files['tests/test.sh'].decode()
    install = [line for line in test.splitlines() if line.startswith('python3 -m pip install --target /tmp/multiswe-grader ')]
    if len(install) != 1 or 'python3 /tests/grade.py ' not in test:
        raise ValueError('Unexpected Multi-SWE verifier layout; refusing partial repair')
    grade = files['tests/grade.py'].decode()
    error_line = '        print(f"Multi-SWE grader error: {type(exc).__name__}: {exc}", file=sys.stderr)'
    if grade.count(error_line) != 1:
        raise ValueError('Unexpected grader error handling')
    grade = grade.replace(error_line, error_line + '\n        reward_path.unlink(missing_ok=True)\n        raise')
    import inspect
    grade = grade.replace('def main():', inspect.getsource(expected_missing_source) + '\n\ndef main():')
    grade = grade.replace('        reward = float(report.valid',
                          '        if report.fix_patch_result.all_count == 0 and not expected_missing_source(row, output):\n'
                          '            raise RuntimeError("Verifier produced no parsed test results; see test_output.txt")\n'
                          '        reward = float(report.valid')
    grade = grade.replace('        print(report.error_msg or "valid report")',
                          '        missing = {field: sorted(set(getattr(dataset, field)) - set(getattr(report, field)))\n'
                          '                   for field in ("p2p_tests", "f2p_tests", "s2p_tests", "n2p_tests")}\n'
                          '        print(report.error_msg or ("Missing required test results: " + json.dumps(missing) if not reward else "valid report"))')
    test = test.replace('set -uo pipefail', 'set -euo pipefail')
    test = test.replace(install[0] + '\n', '')
    test = test.replace('export PYTHONPATH=/tmp/multiswe-grader:${PYTHONPATH:-}\n', '')
    test = test.replace('mkdir -p /logs/verifier\n', 'mkdir -p /logs/verifier\nrm -f /logs/verifier/reward.json\n')
    test = test.replace('python3 /tests/grade.py ', '/opt/multiswe-python/bin/python3 -I /tests/grade.py ')
    files.update({
        'environment/Dockerfile': (docker.rstrip() + '\n' + docker_setup()).encode(),
        'tests/test.sh': test.encode(), 'tests/grade.py': grade.encode(),
    })
    return files


def export_shared_recipes(rows):
    """Use the pinned upstream builder, including its per-PR recipe selection."""
    from importlib.metadata import version
    from dataclasses import fields
    from multi_swe_bench.harness.pull_request import PullRequest
    from multi_swe_bench.harness.image import Config, Image
    from multi_swe_bench.harness.instance import Instance
    if version('multi-swe-bench') != '1.1.2':
        raise ValueError('Shared-image conversion requires multi-swe-bench==1.1.2')
    keys = {f.name for f in fields(PullRequest)}
    result = {}
    for task_id, row in rows.items():
        try:
            pr = PullRequest.from_dict({k: v for k, v in row.items() if k in keys})
            image = Instance.create(pr, Config(need_clone=True, global_env=None, clear_env=False)).dependency()
            base = image.dependency()
            if not isinstance(base, Image):
                raise ValueError('No upstream shared Image recipe')
            files = image.files()
            names = [f.name for f in files]
            if len(names) != len(set(names)) or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', n) for n in names):
                raise ValueError('Unsupported recipe filenames')
            # Deliberately support only the exact COPY + prepare pattern audited
            # in this source. Extra RUN/ENV/USER/etc. need explicit review.
            actual = [l.strip() for l in image.dockerfile().splitlines() if l.strip()]
            expected = ['FROM ' + base.image_full_name()] + ['COPY ' + n + ' /home/' for n in names] + ['RUN bash /home/prepare.sh']
            if actual != expected:
                raise ValueError('Unsupported upstream Dockerfile instructions')
            if not {'prepare.sh', 'test.patch', 'fix-run.sh'}.issubset(names):
                raise ValueError('Incomplete upstream recipe')
            result[task_id] = {
                'original_image': image.image_full_name(), 'base': base.image_full_name(),
                'base_sha': pr.base.sha, 'repo': pr.repo,
                # Never export the reference answer into agent-visible setup.
                'files': {f.name: f.content for f in files if f.name != 'fix.patch'},
            }
        except Exception as exc:
            result[task_id] = {'error': str(exc)}
    return result


ELECTRON_PROXY = '''# Electron's downloader requires explicit proxy opt-in.
electron_https_proxy=${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}
electron_http_proxy=${http_proxy:-${HTTP_PROXY:-$electron_https_proxy}}
if [ -n "$electron_https_proxy" ] || [ -n "${GLOBAL_AGENT_HTTPS_PROXY:-}${GLOBAL_AGENT_HTTP_PROXY:-}" ]; then
    export ELECTRON_GET_USE_PROXY=${ELECTRON_GET_USE_PROXY:-1}
    export GLOBAL_AGENT_HTTPS_PROXY=${GLOBAL_AGENT_HTTPS_PROXY:-$electron_https_proxy}
    export GLOBAL_AGENT_HTTP_PROXY=${GLOBAL_AGENT_HTTP_PROXY:-$electron_http_proxy}
    export GLOBAL_AGENT_NO_PROXY=${GLOBAL_AGENT_NO_PROXY:-${no_proxy:-${NO_PROXY:-}}}
fi
unset electron_https_proxy electron_http_proxy
'''


PREINSTALLED_REPOS = {
    'insomnia', 'github-readme-stats', 'async', 'trpc', 'redux', 'material-ui',
    'dayjs', 'react-router', 'checkstyle', 'fastjson2', 'junit5', 'logstash', 'mockito', 'spotbugs',
}

JUNIT_RUNTIME_SNAPSHOT_BASELINES = {
    '2e27ea70bd9ad30049a922bce882719e5e726f81',
    '686e49f95918a4ee07e366afb5cfd1764696f81d',
    '511ab7d04dd5deec372157381b37a42003778b18',
    '28a261e1211965ce41d418a0a8bccf17fae9aeec',
    'bebff4123850e39517ac67dd31bbb0eff41fa0fc',
}


def preinstalled_docker(recipe):
    # Keep the already-built cache keys for repositories whose baseline
    # installer has not changed. The current shared helper adds setup paths for
    # Fastjson2 and JUnit 5; embedding those branches in every image would
    # invalidate unrelated image caches.
    helper_name = ('preinstall.py' if recipe['repo'] in ('fastjson2', 'junit5')
                   else 'preinstall_cache_v8.py')
    helper = EMBEDDED_FILES[helper_name]
    # These baselines also failed with one worker: resolving a test classpath
    # can realize more tasks and mutate Gradle's live task collection. Iterate
    # a snapshot instead. Keep other baselines' image cache keys unchanged.
    if recipe['repo'] == 'junit5' and recipe['base_sha'] in JUNIT_RUNTIME_SNAPSHOT_BASELINES:
        helper = helper.replace('--max-workers 2', '--max-workers 1')
        helper = helper.replace(
            'p.tasks.withType(org.gradle.api.tasks.testing.Test).each { t ->',
            'p.tasks.withType(org.gradle.api.tasks.testing.Test).toList().each { t ->')
        helper = helper.replace('./gradlew --no-daemon', './gradlew --stacktrace --no-daemon')
    proxy = EMBEDDED_FILES['gradle_proxy.py'].split("\nif __name__ == '__main__':", 1)[0]
    proxy = proxy.replace('def main():', 'def configure_gradle_proxy():')
    invocation = 'preinstall(json.loads(sys.argv[1]), configure_gradle_proxy)\n'
    if recipe['repo'] == 'fastjson2':
        helper += '\n' + EMBEDDED_FILES['maven_proxy.py']
        invocation = 'with maven_wrapper_proxy(properties):\n    ' + invocation
    program = proxy + '\n' + helper + '\n' + invocation
    # Only baseline preparation enters the image, never the solution or test patch.
    spec = {key: recipe[key] for key in ('repo', 'base_sha')}
    spec['prepare'] = patched_image_prepare(recipe)
    spec['electron_proxy'] = ELECTRON_PROXY if recipe['repo'] == 'insomnia' else ''
    readable_cache = ''
    if recipe['repo'] in ('mockito', 'spotbugs'):
        # Gradle creates owner-only directories during the root image build.
        # The runtime copies the baked workdir before starting the root-mapped
        # task container, so its reader must be able to traverse these caches.
        root = '/home/' + recipe['repo']
        readable_cache = ('RUN set -eu; for cache in ' + root + '/.gradle '
                          + root + '/buildSrc/build; do '
                          'if [ -d "$cache" ]; then chmod -R a+rX "$cache"; fi; done\n')
    return ('\n# Install baseline dependencies once, for this exact source commit.\n'
            'ENV GRADLE_USER_HOME=/opt/multiswe-gradle\n'
            + ('ENV MAVEN_USER_HOME=/opt/multiswe-maven-user\n' if recipe['repo'] == 'fastjson2' else '')
            + 'RUN /opt/multiswe-python/bin/python3 -c ' + shlex.quote('exec(' + repr(program) + ')')
            + ' ' + shlex.quote(json.dumps(spec, sort_keys=True)) + '\n' + readable_cache)


def share_image(files, recipe, locks):
    """Move the audited upstream PR layer to idempotent task initialization."""
    if 'error' in recipe:
        raise ValueError(recipe['error'])
    pinned = locks.get(recipe['base'])
    if not pinned or not re.fullmatch(r'[a-z0-9_./-]+@sha256:[a-f0-9]{64}', pinned):
        raise ValueError('Missing digest-pinned shared base: ' + recipe['base'])
    docker = files['environment/Dockerfile'].decode()
    old = 'FROM ' + recipe['original_image']
    if docker.splitlines()[0] != old:
        raise ValueError('Upstream recipe does not match task image')
    if any(n.startswith('setup_files/') for n in files):
        raise ValueError('Existing task setup requires review')
    if not re.fullmatch(r'[a-f0-9]{40}', recipe['base_sha']):
        raise ValueError('Invalid repository commit')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', recipe['repo']):
        raise ValueError('Invalid repository name')
    recipe = repair_recipe(recipe)
    updated = dict(files)
    updated['environment/Dockerfile'] = docker.replace(old, 'FROM ' + pinned, 1).encode()
    if recipe['repo'] == 'logstash':
        # Azul renamed its signed Release metadata. Accept only those fields for
        # the Azul source, without disabling signature or package verification.
        azul = ('RUN set -eu; azul_list=$(mktemp --suffix=.list); '
                'trap \'rm -f "$azul_list"\' EXIT; '
                'for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do '
                '[ -f "$source" ] || continue; '
                'grep -E \'^deb[[:space:]].*https?://repos.azul.com/zulu/deb([/[:space:]]|$)\' "$source" >> "$azul_list" || true; done; '
                'if [ -s "$azul_list" ]; then apt-get -o Dir::Etc::sourcelist="$azul_list" '
                '-o Dir::Etc::sourceparts=- --allow-releaseinfo-change-origin '
                '--allow-releaseinfo-change-label update; fi\n')
        updated['environment/Dockerfile'] = updated['environment/Dockerfile'].replace(
            (MARKER + '\nUSER root\n').encode(), (MARKER + '\nUSER root\n' + azul).encode(), 1)
    preinstalled = recipe['repo'] in PREINSTALLED_REPOS
    if preinstalled:
        updated['environment/Dockerfile'] += preinstalled_docker(recipe).encode()
        recipe['files']['prepare.sh'] = ('#!/bin/bash\nset -eu\ncd /home/' + recipe['repo'] +
            '\n[ "$(git rev-parse HEAD)" = ' + recipe['base_sha'] + ' ]\n')
    proxy_setup = ELECTRON_PROXY if recipe['original_image'].startswith('mswebench/kong_m_insomnia:') else ''
    identity = hashlib.sha256((json.dumps(recipe, sort_keys=True) + proxy_setup).encode()).hexdigest()
    marker = '/setup_files/.multiswe-' + identity
    lines = ['#!/bin/bash', 'set -euo pipefail',
             'if [ -f ' + marker + ' ]; then exit 0; fi']
    if not preinstalled and any('./gradlew ' in content for content in recipe['files'].values()):
        updated['setup_files/gradle_proxy.py'] = EMBEDDED_FILES['gradle_proxy.py'].encode()
        lines.append('/opt/multiswe-python/bin/python3 /setup_files/gradle_proxy.py')
    if proxy_setup:
        lines.extend(proxy_setup.rstrip().splitlines())
    for name, content in sorted(recipe['files'].items()):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name == 'fix.patch':
            raise ValueError('Invalid or answer-bearing setup filename')
        updated['setup_files/upstream/' + name] = content.encode()
        lines.append('install -m 0644 ' + shlex.quote('/setup_files/upstream/' + name) + ' ' + shlex.quote('/home/' + name))
    lines += ['rm -f /home/fix.patch', 'cd ' + shlex.quote('/home/' + recipe['repo']),
              'bash /home/prepare.sh',
              '[ "$(git rev-parse HEAD)" = ' + recipe['base_sha'] + ' ]',
              'git ls-files --others --exclude-standard -z > "$(git rev-parse --git-path multiswe-setup-untracked)"',
              'touch ' + marker]
    updated['setup_files/setup.sh'] = ('\n'.join(lines) + '\n').encode()
    solve = updated['solution/solve.sh'].decode()
    updated['solution/solve.sh'] = solve.replace('set -e\n', 'set -e\nbash /setup_files/setup.sh\n', 1).encode()
    if b'bash /setup_files/setup.sh\n' not in updated['solution/solve.sh']:
        raise ValueError('Unknown reference entrypoint')
    updated['instruction.md'] = updated['instruction.md'].rstrip() + b'\n\nBefore working on the task, run `bash /setup_files/setup.sh`. It initializes this task once and safely does nothing on subsequent calls.\n'
    if 'tests/extract_fix_patch.sh' in updated:
        updated['tests/extract_fix_patch.py'] = EMBEDDED_FILES['extract_fix_patch.py'].encode()
        updated['tests/extract_fix_patch.sh'] = b'#!/bin/bash\nset -euo pipefail\nexec /opt/multiswe-python/bin/python3 /tests/extract_fix_patch.py "$@"\n'
    if recipe['repo'] in ('dayjs', 'darkreader', 'spotbugs', 'mockito') and 'task.toml' in updated:
        updated['task.toml'] = updated['task.toml'].replace(b'memory_mb = 4096', b'memory_mb = 8192')
    if recipe['repo'] == 'nuxt':
        grade = updated['tests/grade.py'].decode()
        # The pinned parser uses the first token of each result line. Verbose
        # Vitest prefixes individual tests with their file, while the original
        # dataset also records suite names from default-reporter slow-test lines.
        import inspect
        grade = grade.replace('def main():', inspect.getsource(normalize_nuxt_output) + '\n\ndef main():')
        grade = grade.replace('    reward_path = Path(sys.argv[3])',
                              '    output = normalize_nuxt_output(output)\n    reward_path = Path(sys.argv[3])')
        updated['tests/grade.py'] = grade.encode()
    if recipe['repo'] == 'trpc':
        import inspect
        grade = updated['tests/grade.py'].decode()
        grade = grade.replace('def main():', inspect.getsource(normalize_trpc_output) + '\n\ndef main():')
        grade = grade.replace('    reward_path = Path(sys.argv[3])',
                              '    output = normalize_trpc_output(output)\n    reward_path = Path(sys.argv[3])')
        updated['tests/grade.py'] = grade.encode()
    return updated


def expected_missing_source(row, output):
    """Recognize a baseline import failure for a source file supplied by the fix."""
    import posixpath
    import re
    baseline = row.get('test_patch_result', {})
    if any(baseline.get(k, 0) for k in ('passed_count', 'failed_count', 'skipped_count')):
        return False
    match = re.search(r"Cannot find module '([^']+)'\nRequire stack:\n- ([^\n]+)", output)
    if not match or not match[1].startswith('.'):
        return False
    missing = posixpath.normpath(posixpath.join(posixpath.dirname(match[2]), match[1]))
    added = re.findall(r'(?m)^--- /dev/null\n\+\+\+ b/([^\n]+)', row.get('fix_patch', ''))
    if any('/home/' + row.get('repo', '') + '/' + name == missing + suffix
           for name in added for suffix in ('', '.js', '.jsx', '.ts', '.tsx', '/index.js', '/index.ts')):
        return True
    # The fix can also remove a broken import rather than add its target.
    importer = match[2].removeprefix('/home/' + row.get('repo', '') + '/')
    for block in row.get('fix_patch', '').split('diff --git '):
        if '\n--- a/' + importer + '\n' not in block:
            continue
        for line in block.splitlines():
            if line.startswith('-') and re.search(r'''(?:from\s*|require\s*\(\s*|import\s*)['"]''' + re.escape(match[1]) + r'''['"]''', line):
                return True
    return False


def normalize_trpc_output(output):
    """The pinned tRPC parser requires ANSI color even for non-interactive logs."""
    import re
    lines = []
    for line in output.splitlines():
        plain = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        match = re.match(r'^(?:@trpc/tests:test-ci: )?\[0\]\s+([✓❯])\s+(?:\|tests\|\s+)?([^|\s]\S*)', plain)
        if match:
            color = '32' if match[1] == '✓' else '33'
            line = f'[0] \x1b[{color}m{match[1]}\x1b[39m \x1b[32m|tests|\x1b[39m {match[2]}'
        lines.append(line)
    return '\n'.join(lines)


def normalize_nuxt_output(output):
    """Map verbose Vitest results to upstream identifiers, with failures winning."""
    import re
    statuses = {}
    for line in output.splitlines():
        line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        match = re.match(r'^([✓×❯])\s+(\S+)(.*)', line)
        if not match:
            continue
        names = [match[2]]
        detail = re.match(r'^ > (\S+)', match[3])
        if detail and re.search(r'\.(test|spec)\.', match[2]):
            names.append(detail[1])
        for name in names:
            statuses[name] = statuses.get(name, True) and match[1] == '✓'
    return '\n'.join(('✓' if passed else '❯') + ' ' + name + ' result'
                     for name, passed in statuses.items())


def repair_recipe(recipe):
    """Repair runtime/test invocation, without changing tests or their expectations."""
    recipe = copy.deepcopy(recipe)
    for name in ('prepare.sh', 'fix-run.sh'):
        content = recipe['files'].get(name, '')
        content = re.sub(r'--max-workers\s+\d+\s*', '', content)
        content = re.sub(r'(?m)^(\./gradlew )(.*)$', r'\1--no-daemon --max-workers 2 \2', content)
        recipe['files'][name] = content
    if recipe['repo'] == 'checkstyle':
        recipe['files']['fix-run.sh'] = recipe['files']['fix-run.sh'].replace(
            'mvn ', 'mvn -s /opt/multiswe-maven-settings.xml -Dmaven.repo.local=/opt/multiswe-maven ')
    if recipe['repo'] == 'fastjson2':
        recipe['files']['fix-run.sh'] = recipe['files']['fix-run.sh'].replace(
            './mvnw ', './mvnw -s /opt/multiswe-maven-settings.xml -Dmaven.repo.local=/opt/multiswe-maven ')
    if recipe['repo'] == 'junit5':
        recipe['files']['fix-run.sh'] = recipe['files']['fix-run.sh'].replace(
            './gradlew ', './gradlew --init-script /opt/multiswe-junit-local.gradle ')
    run = recipe['files']['fix-run.sh']
    # git apply rejects an empty fix.patch, preventing no-op tests from running.
    def split_apply(match):
        command = match.group(0).replace(' /home/fix.patch', '')
        fix_command = command.replace('/home/test.patch', '/home/fix.patch')
        return command + '\nif [ -s /home/fix.patch ]; then ' + fix_command + '; fi'
    run = re.sub(r'(?m)^git apply[^\n]* /home/test\.patch /home/fix\.patch[ \t]*$', split_apply, run)
    if recipe['repo'] == 'darkreader':
        run = run.replace('npm run test:unit -- ', 'npm run test:unit -- --runInBand ')
    if recipe['repo'] == 'dayjs':
        # npm test runs Jest repeatedly for four time zones. Limit every invocation.
        limit = "node -e 'const fs=require(\"fs\"); const p=JSON.parse(fs.readFileSync(\"package.json\")); for(const k of Object.keys(p.scripts||{})) p.scripts[k]=p.scripts[k].replace(/\\bjest\\b/g, \"jest --runInBand\"); fs.writeFileSync(\"package.json\", JSON.stringify(p));'\n"
        run = run.replace('npm test ', limit + 'npm test ')
    if recipe['repo'] == 'nuxt':
        run = run.replace('pnpm test:unit -- --verbose', 'pnpm test:unit --reporter=verbose')
        run = run.replace('pnpm test:runtime  --no-watch', 'pnpm test:runtime --no-watch --reporter=verbose')
    if recipe['repo'] == 'axios':
        # Local proxy tests must control their own proxy environment. Node 20's
        # new family-autoselection default breaks this older custom lookup API.
        run = run.replace('npm test ', 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY npm_config_proxy npm_config_https_proxy npm_config_noproxy\n'
                          'if node --help | grep -q -- --network-family-autoselection; then\n'
                          '    export NODE_OPTIONS="${NODE_OPTIONS:-} --no-network-family-autoselection"\nfi\n'
                          'npm test ')
    recipe['files']['fix-run.sh'] = run
    return recipe


def blob_files(blob):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}


def patch_blob(blob, recipe=None, locks=None, task_id=None):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        members = archive.getmembers()
        files = {}
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(name) in files:
                    raise ValueError('Duplicate task archive member')
                files[str(name)] = archive.extractfile(member).read()
    updated = patch_files(files)
    if recipe is not None:
        updated = share_image(updated, recipe, locks or {})
    updated = apply_task_memory(updated, task_id)
    updated = repair_spotbugs_verifier_fixtures(updated, task_id)
    updated = repair_hugo_result_lines(updated, task_id)
    if updated == files:
        return blob
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for member in members:
            member = copy.copy(member)
            if member.isfile():
                data = updated.pop(str(PurePosixPath(member.name)))
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
        for name, data in sorted(updated.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--share-images', action='store_true', help='experimental: move audited upstream PR preparation into setup_files')
    parser.add_argument('--base-image-lock', type=Path, help='Override the embedded digest-pinned base-image mapping with a JSON file')
    parser.add_argument('--recipe-python', default=sys.executable, help='Python with multi-swe-bench==1.1.2 installed')
    args = parser.parse_args()
    locks = json.loads(args.base_image_lock.read_text() if args.base_image_lock
                       else EMBEDDED_FILES['base-images.lock.json']) if args.share_images else {}
    sources = [args.source] if args.source.is_file() else sorted(args.source.glob('*.parquet'))
    if not sources:
        raise ValueError('No source Parquets found')
    if args.output.exists():
        raise ValueError('Use a new output directory')
    args.output.mkdir(parents=True)
    for source in sources:
        table = pq.read_table(source)
        rows = table.to_pylist()
        labels, reasons = {}, {}
        recipes, unresolved = {}, {}
        if args.share_images:
            row_data = {r['path']: json.loads(blob_files(r['task_binary'])['tests/row.json']) for r in rows}
            generated = subprocess.run([args.recipe_python, str(Path(__file__).resolve()), '--export-shared-recipes'],
                                       input=json.dumps(row_data), capture_output=True, text=True, check=True)
            recipes = json.loads(generated.stdout)
        for row in rows:
            try:
                patched = patch_blob(row['task_binary'], recipes.get(row['path']), locks, task_id=row['path'])
            except ValueError as exc:
                if not args.share_images:
                    raise
                unresolved[row['path']] = str(exc)
                patched = patch_blob(row['task_binary'])
            if patched != row['task_binary']:
                labels[row['path']] = ['runtime-and-verifier-repair']
                reasons[row['path']] = ('Bake a pinned, isolated grader and required network tools into the image; '
                                        'repair Debian archive downloads and report verifier infrastructure failures explicitly.')
            if row['path'] in recipes and row['path'] not in unresolved:
                reasons[row['path']] += (' Use a digest-pinned base image. '
                                         'Exclude setup artifacts from solution patches, batch Git filenames safely, '
                                         'and run tests when the solution patch is empty.')
                recipe = recipes[row['path']]
                if recipe['repo'] in PREINSTALLED_REPOS:
                    reasons[row['path']] += ' Preinstall baseline dependencies in the image for the exact source commit; remove dependency installation and baseline test execution from task setup.'
                if recipe['repo'] in ('mockito', 'spotbugs'):
                    reasons[row['path']] += ' Make baked Gradle workspace caches readable and traversable for runtime workdir copying, preserving contents and write permissions.'
                if recipe['repo'] == 'redux':
                    reasons[row['path']] += ' Suppress only the root prepublish release/test hook during dependency installation and restore package.json afterward.'
                if recipe['repo'] == 'material-ui':
                    reasons[row['path']] += ' Skip Playwright browser downloads; these tasks use unit-test verifiers.'
                if recipe['repo'] == 'async':
                    reasons[row['path']] += ' Reconcile stale package-lock entries with npm install while retaining existing locked versions.'
                if recipe['repo'] == 'junit5':
                    reasons[row['path']] += ' Resolve the matching Vintage engine dependency from the baseline project during installation and verification; verify direct Gradle plugin downloads after proxy rate limits.'
                    if recipe['base_sha'] in JUNIT_RUNTIME_SNAPSHOT_BASELINES:
                        reasons[row['path']] += ' Snapshot Gradle test tasks before resolving their runtime dependencies during image preparation, avoiding mutation of the live task collection; preserve the verifier test command.'
                if recipe['repo'] == 'logstash':
                    reasons[row['path']] += ' Accept the Azul repository Origin and Label metadata rename only for its signed apt source.'
                else:
                    reasons[row['path']] += ' Initialize the task checkout in setup while sharing its compatible base image.'
                if recipe['repo'] in ('dayjs', 'darkreader'):
                    reasons[row['path']] += ' Run Jest serially with an 8 GiB task allowance to avoid worker memory kills.'
                if recipe['repo'] in ('nuxt', 'trpc'):
                    reasons[row['path']] += ' Make actual test results parseable despite reporter verbosity or ANSI-color differences.'
                if recipe['repo'] == 'axios':
                    reasons[row['path']] += ' Let local proxy tests control their own proxy environment.'
            if row['path'] in TASK_MEMORY_MB and patched != row['task_binary']:
                labels[row['path']].append('task-memory-budget')
                reasons[row['path']] += (' Set this task to the operator-selected 8 GiB budget for the reference solution; '
                                         'its expected no-op assertion-output OOM is accepted only through individual review.')
            if (row['path'] in SPOTBUGS_VERIFIER_FIXTURES and args.share_images
                    and row['path'] not in unresolved and patched != row['task_binary']):
                labels.setdefault(row['path'], []).append('reference-test-fixture')
                reasons[row['path']] = reasons.get(row['path'], '') + (
                    ' Include the original Java regression fixture in the verifier test patch so '
                    'the fix/test path filter cannot discard it before compilation; preserve the '
                    'reference implementation and all test assertions.')
            if row['path'] == 'gohugoio__hugo-13005' and patched != row['task_binary']:
                labels.setdefault(row['path'], []).append('verifier-log-parser')
                reasons[row['path']] = reasons.get(row['path'], '') + (
                    ' Separate existing Go test result markers appended to warnings before parsing, '
                    'preserving PASS/FAIL/SKIP statuses and required-test checks.')
            row['task_binary'] = patched
        output = args.output / source.name
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), output)
        write_patch_report(source, output, patcher=__file__, source={
            'dataset': 'PrimeIntellect/Multi-SWE-RL-Verified', 'revision': REVISION,
            'url': f'https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified/tree/{REVISION}'},
            dropped={}, change_labels=labels, change_reasons=reasons, unresolved=unresolved,
            patches=[{'version': 'multiswe-preinstalled-dependencies-v10', 'python': '3.11.13', 'grader': '1.1.2', 'share_images': args.share_images,
                      'helper_sha256': {name: hashlib.sha256(EMBEDDED_FILES[name].encode()).hexdigest()
                                        for name in ('extract_fix_patch.py', 'gradle_proxy.py', 'preinstall.py', 'preinstall_cache_v8.py')},
                      'base_image_lock': locks if args.share_images else {}}])
        if args.share_images:
            (args.output / (source.stem + '.shared-images.json')).write_text(json.dumps({
                'tasks': len(rows), 'converted': len(recipes) - len(unresolved), 'unresolved': unresolved,
                'unique_dockerfiles': len({hashlib.sha256(blob_files(r['task_binary'])['environment/Dockerfile']).hexdigest() for r in rows}),
            }, indent=2) + '\n')
        print(f'{output}: {len(rows)} tasks, {len(labels)} repaired, none dropped')


if __name__ == '__main__':
    if sys.argv[1:] == ['--export-shared-recipes']:
        print(json.dumps(export_shared_recipes(json.load(sys.stdin))))
    else:
        main()
