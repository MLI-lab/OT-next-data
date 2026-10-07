#!/usr/bin/env python3
"""Apply this cluster's explicit Maven proxy settings to an existing settings.xml.

Run before recipe setup copies the settings file. This changes transport only.
"""
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

# Central answers this cluster's shared egress with 429 under load; Google's Central mirror serves the
# same artifacts. Repository ids stay unchanged, so cached artifacts remain valid.
CENTRAL = os.environ.get('INFERREDBUGS_CENTRAL_MIRROR', 'https://maven-central.storage-download.googleapis.com/maven2')


def configure(path):
    path = Path(path)
    # Some historical repair recipes omit only the final settings closing tag.
    # Accept that narrowly defined repair only if the completed XML parses.
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        raw = path.read_text()
        if '<settings' not in raw or '</settings>' in raw:
            raise
        tree = ET.ElementTree(ET.fromstring(raw + '\n</settings>\n'))
    root = tree.getroot()
    namespace = root.tag.partition('}')[0].removeprefix('{') if '}' in root.tag else ''
    if namespace:
        ET.register_namespace('', namespace)
    def tag(name):
        return '{' + namespace + '}' + name if namespace else name
    for url in root.iter(tag('url')):
        if (url.text or '').rstrip('/') in ('http://172.17.0.1:8081/maven2', 'https://repo.maven.apache.org/maven2',
                                            'https://repo1.maven.org/maven2', 'http://repo1.maven.org/maven2'):
            url.text = CENTRAL
    proxies = root.find(tag('proxies'))
    if proxies is None:
        proxies = ET.SubElement(root, tag('proxies'))
    for proxy in list(proxies):
        if (proxy.findtext(tag('id')) or '').startswith('helma-'):
            proxies.remove(proxy)
    for protocol in ('http', 'https'):
        value = os.environ.get(protocol + '_proxy') or os.environ.get(protocol.upper() + '_PROXY')
        if not value:
            continue
        endpoint = urllib.parse.urlsplit(value)
        if endpoint.scheme != 'http' or not endpoint.hostname or endpoint.username or endpoint.password:
            raise ValueError('Expected an unauthenticated HTTP cluster proxy')
        proxy = ET.SubElement(proxies, tag('proxy'))
        for name, value in {'id':'helma-' + protocol, 'active':'true', 'protocol':protocol,
                            'host':endpoint.hostname, 'port':str(endpoint.port or 80),
                            # Central too: direct access only works over IPv6, and Java prefers
                            # IPv4, so each direct request waited for a connect timeout.
                            'nonProxyHosts':'localhost|127.0.0.1|[::1]'}.items():
            ET.SubElement(proxy, tag(name)).text = value
    # Prefer Central for normal releases. The optional WSO2 fallback otherwise
    # receives a request for every common dependency before Maven tries Central.
    for profile in root.findall(tag('profiles') + '/' + tag('profile')):
        if profile.findtext(tag('id')) != 'inferredbugs-fallbacks':
            continue
        for section, entry in (('repositories', 'repository'), ('pluginRepositories', 'pluginRepository')):
            parent = profile.find(tag(section))
            if parent is None:
                parent = ET.SubElement(profile, tag(section))
            for child in list(parent):
                if child.findtext(tag('id')) == 'central':
                    parent.remove(child)
            central = ET.Element(tag(entry))
            ET.SubElement(central, tag('id')).text = 'central'
            ET.SubElement(central, tag('url')).text = CENTRAL
            for name, enabled in (('releases', 'true'), ('snapshots', 'false')):
                ET.SubElement(ET.SubElement(central, tag(name)), tag('enabled')).text = enabled
            parent.insert(0, central)
    tree.write(path, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    for path in sys.argv[1:]:
        configure(path)
