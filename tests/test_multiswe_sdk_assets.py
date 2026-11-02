import pytest

from hpc.offline_prep.image_runtime_assets import canonical, dereference_policy


@pytest.mark.parametrize('root', ['/usr/lib/jvm/java-17-openjdk-amd64',
                                  '/usr/lib/jvm/zulu8-ca-amd64', '/usr/share/maven'])
def test_sdk_symlink_closure_requires_an_explicit_reviewed_root(root):
    spec = canonical({'image_sha256': 'a'*64, 'trees': {'sdk': root}, 'dereference': ['sdk']})
    allowed, excluded = dereference_policy(spec['trees']['sdk'])
    assert root in allowed
    assert not any(path in allowed for path in ['/home', '/root', '/etc', '/usr'])
    assert set(excluded) <= {'src.zip', 'lib/src.zip'}


@pytest.mark.parametrize('root', ['/usr/lib/jvm/custom-agent-sdk', '/usr/share/maven/../../home',
                                  '/opt/yarn-v1.22.22/../../root'])
def test_sdk_assets_do_not_allow_arbitrary_or_escaping_directories(root):
    with pytest.raises(ValueError):
        canonical({'image_sha256': 'a'*64, 'trees': {'sdk': root}})


def test_node_module_links_cannot_use_sdk_dereference_rules():
    with pytest.raises(ValueError, match='symlink closure'):
        canonical({'image_sha256': 'a'*64, 'trees': {'modules': '/usr/local/lib/node_modules'},
                   'dereference': ['modules']})


def test_workspace_modules_keep_live_project_links_out_of_archive_extraction():
    import tarfile
    from hpc.offline_prep.image_runtime_assets import split_project_links
    workspace = tarfile.TarInfo('@example/core')
    workspace.type = tarfile.SYMTYPE
    workspace.linkname = '../../packages/core'
    regular = tarfile.TarInfo('jest/index.js')
    selected, links = split_project_links([workspace, regular], '/home/example/node_modules', '/home/example')
    assert [m.name for m in selected] == ['jest/index.js']
    assert links == {'@example/core': 'packages/core'}
    workspace.linkname = '/etc/passwd'
    with pytest.raises(ValueError, match='leaves the reviewed project'):
        split_project_links([workspace], '/home/example/node_modules', '/home/example')


def test_project_link_root_cannot_be_widened_to_home():
    with pytest.raises(ValueError, match='matching node_modules'):
        canonical({'image_sha256': 'a'*64, 'trees': {'modules': '/home/example/node_modules'},
                   'project_links': {'modules': '/home'}})


def test_sdk_dereference_preserves_aliases_and_absence_but_rejects_cycles(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    import hpc.offline_prep.image_python_runtime as image
    from hpc.offline_prep.image_runtime_assets import prepare
    source = tmp_path/'source'; source.mkdir()
    (source/'real').mkdir();(source/'real/tool').write_text('pristine')
    (source/'alias').symlink_to('real', target_is_directory=True)
    (source/'missing').symlink_to('unavailable')
    outside = tmp_path/'development-docs'; outside.mkdir()
    (outside/'not-a-runtime-input').write_text('excluded')
    (source/'lib').mkdir()
    (source/'lib/src.zip').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(image, 'source_image', lambda *a, **kw: 'verified-source')
    monkeypatch.setattr(image, 'stage_source', lambda original, scratch: original)
    def command(original, scratch, *args):
        if args[0] == 'python3':
            return [sys.executable, '-I', '-c', args[3], str(source), json.dumps([str(source)]), args[6]]
        args = list(map(str, args)); args[args.index('-C')+1] = str(source)
        return args
    monkeypatch.setattr(image, 'command', command)
    runtime = tmp_path/'runtime';runtime.mkdir()
    scratch = tmp_path/'scratch';scratch.mkdir()
    spec = {'image_sha256': 'a'*64, 'trees': {'jdk': '/usr/lib/jvm/java-11-openjdk-amd64'}, 'dereference': ['jdk']}
    record = prepare(spec, runtime, scratch)
    assert (runtime/'assets/jdk/alias/tool').read_text() == 'pristine'
    assert not (runtime/'assets/jdk/missing').exists()
    assert not (runtime/'assets/jdk/lib/src.zip').exists()
    assert 'missing' in record['jdk']['omitted_unavailable_or_development_links']
    (source/'real/loop').symlink_to('..', target_is_directory=True)
    runtime2 = tmp_path/'runtime2';runtime2.mkdir()
    with pytest.raises(ValueError, match='Cyclic dependency directory'):
        prepare(spec, runtime2, scratch)


def test_bin_alias_through_workspace_module_stays_a_live_project_link():
    import tarfile
    from hpc.offline_prep.image_runtime_assets import split_project_links
    package = tarfile.TarInfo('insomnia');package.type=tarfile.SYMTYPE
    package.linkname='../packages/insomnia'
    cli = tarfile.TarInfo('.bin/insomnia');cli.type=tarfile.SYMTYPE
    cli.linkname='../insomnia/bin/cli.js'
    selected,links=split_project_links([cli,package],'/home/insomnia/node_modules','/home/insomnia')
    assert not selected
    assert links=={'insomnia':'packages/insomnia','.bin/insomnia':'packages/insomnia/bin/cli.js'}
    package.linkname='insomnia'
    with pytest.raises(ValueError,match='Cyclic'):
        split_project_links([package],'/home/insomnia/node_modules','/home/insomnia')


def test_transient_node_gyp_interpreter_link_is_not_a_cached_dependency():
    import tarfile
    from hpc.offline_prep.image_runtime_assets import split_project_links
    generated=tarfile.TarInfo('./uws/build/node_gyp_bins/python3');generated.type=tarfile.SYMTYPE
    generated.linkname='/usr/bin/python3'
    addon=tarfile.TarInfo('./uws/build/Release/uws.node')
    selected,links=split_project_links([generated,addon],'/home/dayjs/node_modules','/home/dayjs')
    assert [m.name for m in selected]==[addon.name]
    assert not links
    generated.linkname='/etc/passwd'
    with pytest.raises(ValueError,match='leaves the reviewed project'):
        split_project_links([generated],'/home/dayjs/node_modules','/home/dayjs')


def test_link_only_workspace_cache_keeps_directory_and_inventory_required():
    import tarfile
    from hpc.offline_prep.image_runtime_assets import validate_members
    spec = {'image_sha256': 'a' * 64,
            'trees': {'modules': '/home/example/node_modules'},
            'project_links': {'modules': '/home/example'}}
    root = tarfile.TarInfo('python/assets/modules'); root.type = tarfile.DIRTYPE
    links = tarfile.TarInfo(root.name + '.project-links.json'); links.size = 47
    members = {root.name: root, links.name: links}
    validate_members(spec, members)
    for missing in (root.name, links.name):
        with pytest.raises(ValueError, match='Incomplete'):
            validate_members(spec, {k: v for k, v in members.items() if k != missing})


def test_native_compiler_names_survive_runtime_lookup(tmp_path):
    import io
    import json
    import tarfile
    from harbor_patches.verifier_bundle import FORMAT, digest, lookup, spec_id
    spec = {'python': '3.11.13', 'requirements': [], 'tools': ['c++', 'g++']}
    key = spec_id(spec)
    path = tmp_path / (key + '.tar.gz')
    with tarfile.open(path, 'w:gz') as stream:
        for name in ('python3', 'c++', 'g++'):
            member = tarfile.TarInfo('python/bin/' + name)
            member.size = 4; member.mode = 0o755
            stream.addfile(member, io.BytesIO(b'test'))
    ref = {'format': FORMAT, 'id': key, 'spec': spec,
           'status': 'ready', 'archive_sha256': digest(path)}
    (tmp_path / (key + '.json')).write_text(json.dumps(ref))
    assert lookup(ref, root=tmp_path) == (path, None)


def test_native_compiler_names_survive_full_staging(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    from harbor_patches.verifier_bundle import _stage
    import harbor_patches.runtime_transfer as transfer
    import harbor_patches.verifier_mount as mount
    archive = tmp_path/'sdk.tar.gz';archive.write_bytes(b'archive')
    (tmp_path/'sdk.json').write_text(json.dumps({'spec': {'tools': ['c++', 'g++']}}))
    commands = []
    class Environment:
        async def exec(self, command, **kwargs):
            commands.append(command)
            return SimpleNamespace(return_code=0, stdout='/tests/.ot-verifier-runtime-abc123', stderr='')
    async def upload(*args, **kwargs):
        return None
    async def prepare(*args, **kwargs):
        return None
    monkeypatch.setattr(transfer, 'upload', upload)
    monkeypatch.setattr(mount, 'prepare', prepare)
    result = asyncio.run(_stage(Environment(), archive, {'id':'a'*64, 'archive_sha256':'b'*64}, tmp_path/'evidence'))
    assert result['network_downloads'] is False
    assert 'test -x python/bin/c++' in commands[1]
    assert 'test -x python/bin/g++' in commands[1]
