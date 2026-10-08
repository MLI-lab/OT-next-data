"""Configure Gradle's JVM proxy from the task runtime's proxy environment."""
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener


def properties(env):
    result = {}
    for protocol in ('http', 'https'):
        value = env.get(protocol + '_proxy') or env.get(protocol.upper() + '_PROXY')
        if not value:
            continue
        url = urlsplit(value if '://' in value else 'http://' + value)
        if url.scheme != 'http' or not url.hostname:
            raise ValueError('Gradle proxy requires an HTTP proxy URL')
        prefix = 'systemProp.' + protocol + '.'
        result[prefix + 'proxyHost'] = url.hostname
        result[prefix + 'proxyPort'] = str(url.port or 80)
        if url.username:
            result[prefix + 'proxyUser'] = unquote(url.username)
        if url.password:
            result[prefix + 'proxyPassword'] = unquote(url.password)
    if result:
        hosts = ['localhost', '127.*', '[::1]']
        for host in (env.get('no_proxy') or env.get('NO_PROXY') or '').split(','):
            host = host.strip()
            if host and '/' not in host and ':' not in host:
                hosts.append('*' + host if host.startswith('.') else host)
        result['systemProp.http.nonProxyHosts'] = '|'.join(hosts)
    return result


def main():
    values = properties(os.environ)
    if not values:
        return
    # Some cluster proxies share a rate-limited Maven Central exit IP. Bypass
    # only after observing 429 through that proxy and verifying direct access.
    proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
    if proxy:
        for host in ('repo.maven.apache.org', 'repo1.maven.org'):
            url = f'https://{host}/maven2/com/google/code/gson/gson/2.10.1/gson-2.10.1.pom'
            try:
                with build_opener(ProxyHandler({'https': proxy})).open(url, timeout=5) as response:
                    response.read()
            except HTTPError as exc:
                if exc.code != 429:
                    continue
                try:
                    with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
                        if response.status != 200 or b'<artifactId>gson</artifactId>' not in response.read():
                            continue
                except OSError:
                    continue
                values['systemProp.http.nonProxyHosts'] += '|' + host
                print(f'Gradle: verified direct access to {host} after proxy HTTP 429')
            except OSError:
                pass
    path = Path(os.environ.get('GRADLE_USER_HOME', str(Path.home() / '.gradle'))) / 'gradle.properties'
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text() if path.exists() else ''
    keys = {line.split('=', 1)[0].strip() for line in old.splitlines() if '=' in line}
    def escape(value):
        return value.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
    with path.open('a') as stream:
        stream.write('\n' + ''.join(k + '=' + escape(v) + '\n' for k, v in values.items() if k not in keys))
    path.chmod(0o600)


if __name__ == '__main__':
    main()
