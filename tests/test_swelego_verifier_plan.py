import copy

import pytest

from data.swelego.verifier_runtime import plan


def measured():
    prefix = '/opt/conda/envs/testbed'
    site = prefix + '/lib/python3.9/site-packages'
    def package(name, version, requires=()):
        return dict(name=name, version=version, location=site, requires=list(requires))
    return dict(python='3.9.21', prefix=prefix, executable=prefix+'/bin/python',
                marker_environment={'python_version': '3.9', 'python_full_version': '3.9.21'},
                packages=[package('pytest', '7.4.4', ['pluggy>=0.12', 'tomli; python_version < "3.11"']),
                          package('pluggy', '1.0.0'), package('tomli', '2.0.1'),
                          package('pytest-custom', '1.2', ['helper[fast]']),
                          package('helper', '2.0', ['native; extra == "fast"']),
                          package('native', '3.0'), package('project', '4.0')],
                pytest_plugins=[dict(name='custom', module='custom.plugin', distribution='pytest-custom')])


def bundle():
    return dict(name='base.sif', files={name: {'sha256': digest*64} for name, digest in
                [('base.sif', 'a'), ('base.overlay.img', 'b'), ('base.deferred.json', 'c')]})


def test_observed_closure_preserves_versions_markers_extras_and_image_identity():
    observation = measured()
    original = copy.deepcopy(observation)
    result = plan(observation, bundle())
    assert result['spec']['requirements'] == [
        'helper==2.0', 'native==3.0', 'pluggy==1.0.0', 'pytest-custom==1.2',
        'pytest==7.4.4', 'tomli==2.0.1']
    assert result['plugins'] == ['custom.plugin']
    assert result['spec']['python'] == '3.9.21'
    assert result['spec']['image_python']['overlay_sha256'] == 'b'*64
    assert result['spec']['install_no_deps'] is True
    assert observation == original


def test_missing_observed_dependency_is_incomplete():
    observation = measured()
    observation['packages'] = [p for p in observation['packages'] if p['name'] != 'native']
    with pytest.raises(ValueError, match='Missing recorded verifier dependency: native'):
        plan(observation, bundle())


def test_plugin_outside_original_environment_is_rejected():
    observation = measured()
    observation['packages'][3]['location'] = '/testbed'
    with pytest.raises(ValueError, match='Plugin is outside'):
        plan(observation, bundle())


def test_vendored_metadata_does_not_replace_installed_distribution():
    observation = measured()
    observation['packages'].append(dict(name='pluggy', version='99', requires=[],
                                       location='/opt/conda/envs/testbed/lib/python3.9/site-packages/setuptools/_vendor'))
    assert 'pluggy==1.0.0' in plan(observation, bundle())['spec']['requirements']


def test_reviewed_local_plugin_version_is_copied_from_exact_pristine_image():
    from data.swelego.verifier_runtime import IMAGE_PACKAGES
    observation=measured()
    observation['packages'].append(dict(name='shiny',version='0.0.0',requires=[],
        location=observation['prefix']+'/lib/python3.9/site-packages'))
    observation['pytest_plugins'].append(dict(name='shiny',module='shiny.pytest',distribution='shiny'))
    image=bundle()
    identities=next(iter(IMAGE_PACKAGES))
    for filename,checksum in zip(['base.sif','base.overlay.img','base.deferred.json'],identities):
        image['files'][filename]['sha256']=checksum
    spec=plan(observation,image)['spec']
    assert 'shiny==0.0.0' in spec['requirements']
    assert spec['image_python']['packages']=={'shiny==0.0.0':['shiny']}
    image['files']['base.overlay.img']['sha256']='d'*64
    assert 'packages' not in plan(observation,image)['spec']['image_python']


@pytest.mark.parametrize('repository', ['owner/pluggy', 'agronholm/anyio', 'pytest-dev/pytest-xdist', 'jaraco/zipp'])
def test_subject_that_is_a_grading_dependency_is_not_silently_frozen(repository):
    import json
    from data.swelego.verifier_runtime import prepared_files
    from validation.contract import files_digest
    files = {'setup_files/swelego.json': json.dumps({'repo': repository}).encode()}
    observation = dict(kind='swelego-dependencies-v1', returncode=0,
                       source_bundle_unchanged=True,
                       source_content_sha256=files_digest(files.items()),
                       stdout=json.dumps(measured()))
    spec = plan(measured(), bundle())['spec']
    with pytest.raises(ValueError, match='subject-specific import handling|Unreviewed grading-dependency task revision'):
        prepared_files(files, spec, None, observation=observation, image_bundle=bundle())
