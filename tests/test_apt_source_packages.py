import copy

import pytest

from hpc.offline_prep.apt_source_packages import requests


def fixture():
    return {'apt': ['nodejs=18.20.8-1nodesource1'],
            'sources': [{'filename': 'node.deb', 'sha256': 'a'*64,
                         'url': 'https://example.test/node.deb'}],
            'apt_source_packages': [{'filename': 'node.deb', 'package': 'nodejs',
                                     'version': '18.20.8-1nodesource1'}]}


@pytest.mark.parametrize('change', ['unknown_source', 'unpinned', 'traversal', 'duplicate', 'shell'])
def test_source_deb_requires_verified_source_and_literal_pinned_identity(change):
    spec = fixture()
    if change == 'unknown_source': spec['sources'] = []
    elif change == 'unpinned': spec['apt'] = ['nodejs']
    elif change == 'traversal': spec['apt_source_packages'][0]['filename'] = '../node.deb'
    elif change == 'duplicate': spec['apt_source_packages'] *= 2
    elif change == 'shell': spec['apt_source_packages'][0]['version'] = '$(touch /tmp/bad)'
    with pytest.raises(ValueError): requests(spec)


def test_source_deb_request_and_existing_requests():
    assert requests(fixture()) == 'node.deb\tnodejs\t18.20.8-1nodesource1\n'
    assert requests({'apt': ['curl']}) == ''
