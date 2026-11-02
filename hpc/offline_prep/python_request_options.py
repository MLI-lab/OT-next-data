"""Literal per-request pip settings for disposable package preparation."""
import shlex
from urllib.parse import urlsplit

from packaging.requirements import Requirement


def write_options(spec, work):
    for index, request in enumerate(spec['python'], 1):
        constraints = request.get('constraints', [])
        links = request.get('find_links', [])
        if not isinstance(constraints, list) or not isinstance(links, list):
            raise ValueError('Python constraints and find_links must be lists')
        for line in constraints:
            if (not isinstance(line, str) or any(c in line for c in '\r\n\t')
                    or line.startswith('-') or Requirement(line).url):
                raise ValueError('Python constraints must be literal package requirements')
        for link in links:
            if not isinstance(link, str) or any(c.isspace() for c in link):
                raise ValueError('Invalid Python find_links location')
            parsed = urlsplit(link)
            if not (link.startswith('/') and '..' not in link.split('/')) and not (
                    parsed.scheme == 'https' and parsed.hostname and not parsed.username
                    and not parsed.password and not parsed.fragment):
                raise ValueError('Python find_links must be an absolute path or HTTPS URL')
        common = ''
        if constraints:
            (work / f'constraints{index}.txt').write_text('\n'.join(constraints) + '\n')
            common = f'export PIP_CONSTRAINT=/ot-work/constraints{index}.txt\n'
        if constraints or links:
            build = common
            if links:
                build += 'export PIP_FIND_LINKS=' + shlex.quote(' '.join(links)) + '\n'
            (work / f'python-options{index}.sh').write_text(build)
            # CHECK resolves only the prepared wheelhouse, never remote links.
            (work / f'python-check-options{index}.sh').write_text(common)
