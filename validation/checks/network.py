"""Bounded egress observations, not a proof of complete network isolation."""
import shlex
import tomllib

# Keep targets fixed and recorded in the implementation contract; no task content is sent.
TARGETS = ('https://example.com/', 'https://www.cloudflare.com/')


def declared_policy(task, label):
    raw = tomllib.loads(task.paths.config_path.read_text()) if hasattr(task.paths, 'config_path') else tomllib.loads((task.paths.task_dir / 'task.toml').read_text())
    env = raw.get('environment', {})
    if label != 'agent':
        env = raw.get('verifier', {}).get('environment', env)
        if label.startswith('verifier-'):
            step = next((s for s in raw.get('steps', []) if s.get('name') == label[len('verifier-'):]), {})
            env = step.get('verifier', {}).get('environment', env)
    value = env.get('allow_internet')
    if value is not None and not isinstance(value, bool):
        raise ValueError('allow_internet must be a boolean')
    return value


async def inspect_network(environment, expected):
    observations = []
    for direct in (False, True):
        urls = ('https://1.1.1.1/',) if direct else TARGETS
        for url in urls:
            # curl/wget exit zero even for HTTP error responses: connectivity is what matters.
            command = ('if command -v curl >/dev/null 2>&1; then '
                + ('HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= https_proxy= http_proxy= all_proxy= ' if direct else '')
                + 'curl -sS -o /dev/null --connect-timeout 3 --max-time 5 '
                + ('--noproxy "*" ' if direct else '') + shlex.quote(url)
                + '; elif command -v python3 >/dev/null 2>&1; then python3 -c ')
            script = ('import urllib.request,urllib.error,sys\n'
                + ('opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))\n' if direct else 'opener=urllib.request.build_opener()\n')
                + 'try:\n opener.open(' + repr(url) + ',timeout=5).close()\n'
                + 'except urllib.error.HTTPError:\n pass\nexcept Exception as e:\n print(type(e).__name__);sys.exit(1)\n')
            command += shlex.quote(script) + '; else exit 125; fi'
            result = await environment.exec(command, timeout_sec=15)
            observations.append({'url': url, 'route': 'direct' if direct else 'configured-proxy-or-direct',
                'exit_code': result.return_code, 'reachable': result.return_code == 0,
                'client_available': result.return_code != 125,
                'stderr': (result.stderr or '')[:1000]})
    reachable = any(o['reachable'] for o in observations)
    capable = all(o['client_available'] for o in observations)
    status = 'not_checked' if expected is None else ('error' if not capable else ('passed' if reachable == expected else 'failed'))
    return {'check': 'declared-network-egress', 'status': status, 'expected_allow_internet': expected,
        'observed_any_reachable': reachable, 'observations': observations,
        'reason': 'No explicit allow_internet declaration; observations only.' if expected is None else
                  'Requires curl or python3; no tools are installed by validation.' if not capable else
                  'Compare observed reachability with declared policy; endpoint outages can cause false negatives.'}
