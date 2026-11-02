"""APT inputs from already checksum-verified Debian package downloads."""
import re


def requests(spec):
    sources = {source['filename'] for source in spec.get('sources', [])}
    entries = spec.get('apt_source_packages', [])
    if not isinstance(entries, list):
        raise ValueError('apt_source_packages must be a list')
    lines, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'filename', 'package', 'version'}:
            raise ValueError('APT source package requires filename, package and version')
        filename, package, version = (entry[k] for k in ('filename', 'package', 'version'))
        if (not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*\.deb', filename)
                or filename not in sources or filename in seen
                or not isinstance(package, str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', package)
                or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9.+:~_-]+', version)
                or package + '=' + version not in spec['apt']):
            raise ValueError('APT source package must match a pinned apt request and verified source')
        seen.add(filename)
        lines.append('\t'.join((filename, package, version)) + '\n')
    return ''.join(lines)
