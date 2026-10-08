"""Readied Sprint-member handoff: material, recovery and refusal boundaries.

Earliest GREEN: manual implementation remediation after independent test review.
The registered original 34-case selector and all five methods stay unchanged.
"""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile

import pytest

from batch.helpers import request
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_task_restart import restart
from sprints.helpers import bootstrap, draft
from sprints.test_unpublish import client, planned, reopen, unchanged, verified_materials


DATA = json.loads((Path(__file__).parent / 'fixtures/wip-handoff-materials.json').read_text())


def git_bytes(root, *args):
    return subprocess.check_output(['git', '--no-optional-locks', '-C', str(root), *args])


def git_text(root, *args):
    return git_bytes(root, *args).decode().strip()


def index_path(root):
    path = Path(git_text(root, 'rev-parse', '--git-path', 'index'))
    return path if path.is_absolute() else root / path


def staged_inventory(root):
    entries = {}
    for record in git_bytes(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not record:
            continue
        metadata, path = record.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        assert stage == b'0'
        name = os.fsdecode(path)
        entries[name] = (mode, oid, git_bytes(root, 'show', ':' + name))
    return entries


def observation(root):
    """Independent raw Git and filesystem oracles; no production serializer."""
    names = set()
    for argv in (('ls-tree', '-rz', '--name-only', 'HEAD'),
                 ('ls-files', '-z', '--cached', '--others', '--exclude-standard')):
        names.update(item.decode() for item in git_bytes(root, *argv).split(b'\0') if item)
    files = {}
    directories = set()
    for name in sorted(names):
        path = root / name
        directories.update(str(parent.relative_to(root)) for parent in path.parents
                           if parent != root and parent.is_relative_to(root))
        if path.is_symlink():
            files[name] = ('symlink', stat.S_IMODE(path.lstat().st_mode), os.fsencode(os.readlink(path)))
        elif path.exists():
            files[name] = ('file', stat.S_IMODE(path.stat().st_mode), path.read_bytes())
        else:
            files[name] = ('absent', None, None)
    return {
        'head': git_bytes(root, 'rev-parse', 'HEAD'),
        'branch': git_bytes(root, 'rev-parse', '--abbrev-ref', 'HEAD'),
        'refs': git_bytes(root, 'show-ref'),
        'status': git_bytes(root, 'status', '--porcelain=v1'),
        'stage': git_bytes(root, 'ls-files', '--stage', '-z'),
        'staged_inventory': staged_inventory(root),
        'index': index_path(root).read_bytes(),
        'index_mode': stat.S_IMODE(index_path(root).stat().st_mode),
        'files': files,
        'directories': {name: stat.S_IMODE((root / name).stat().st_mode)
                        for name in sorted(directories)},
    }


def arrange(planned, *, ready=True):
    project, planner, published = planned
    context, proof, _, _ = verified_materials(project)
    root = Path(context['worktree'])
    directory = root / DATA['directory']['path']
    directory.mkdir(mode=DATA['directory']['mode'])
    split = root / DATA['split']['path']
    split.write_bytes(bytes.fromhex(DATA['split']['staged_hex']))
    split.chmod(DATA['split']['mode'])
    executable = root / DATA['executable']['path']
    executable.write_text(DATA['executable']['text'])
    executable.chmod(DATA['executable']['mode'])
    git_text(root, 'add', '--', DATA['split']['path'], DATA['executable']['path'])
    split.write_bytes(bytes.fromhex(DATA['split']['working_hex']))
    untracked = root / DATA['untracked']['path']
    untracked.write_bytes(bytes.fromhex(DATA['untracked']['working_hex']))
    untracked.chmod(DATA['untracked']['mode'])
    (root / DATA['symlink']['path']).symlink_to(DATA['symlink']['target'])
    outside = project['root'].parent / 'external-private-sentinel.bin'
    outside.write_bytes(bytes.fromhex(DATA['outside_hex']))
    (root / DATA['external_link']).symlink_to(outside)
    (root / DATA['deleted']).unlink()
    prior = planner.runtime.task_queries.record('A')
    assert reopen(planner, published['revision'])['status'] == 'draft'
    owner = client(project, 'restart-owner')
    born = restart(owner, 'A', prior['version'])
    if ready:
        packet = {**DATA['requests']['ready'], 'expected_revision': born['revision']}
        readied = owner.invoke(request('task', packet))
        assert readied['status'] == 'newborn' and readied['ready'] is True
    return project, planner, owner, root, proof, outside


def preserve(owner, **changes):
    return owner.invoke(request('handoff', {**DATA['requests']['handoff'], **changes}))


def snapshot(receipt):
    descriptor = receipt['wip_snapshot']
    assert descriptor['schema'] == DATA['schema']
    path = Path(descriptor['path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == descriptor['digest']
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        assert len({member.name for member in members}) == len(members)
        assert all(not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
                   and member.isfile() for member in members)
        payload = {member.name: archive.extractfile(member).read() for member in members}
    manifest = json.loads(payload['manifest.json'])
    assert manifest['schema'] == DATA['schema'] and manifest['task_id'] == 'A'
    return manifest, payload


def assert_snapshot(manifest, payload, before):
    assert manifest['head'] == before['head'].decode().strip()
    assert manifest['branch'] == before['branch'].decode().strip()
    assert payload[manifest['index']['path']] == before['index']
    assert manifest['index']['mode'] == before['index_mode']
    staged_bytes = payload[manifest['staged_tree']['path']]
    assert hashlib.sha256(staged_bytes).hexdigest() == manifest['staged_tree']['sha256']
    with tarfile.open(fileobj=io.BytesIO(staged_bytes)) as archive:
        members = archive.getmembers()
        assert len({entry.name for entry in members}) == len(members)
        leaves = {entry.name: entry for entry in members if not entry.isdir()}
        assert set(leaves) == set(before['staged_inventory'])
        ancestors = {str(parent) for name in leaves for parent in Path(name).parents
                     if str(parent) != '.'}
        assert all(entry.name.rstrip('/') in ancestors for entry in members if entry.isdir())
        for name, (mode, _, content) in before['staged_inventory'].items():
            member = leaves[name]
            if mode == b'120000':
                assert member.issym()
                actual = os.fsencode(member.linkname)
            else:
                assert mode in (b'100644', b'100755') and member.isfile()
                assert bool(member.mode & 0o111) == (mode == b'100755')
                actual = archive.extractfile(member).read()
            assert actual == content, name
    entries = {entry['path']: entry for entry in manifest['files']}
    assert len(entries) == len(manifest['files'])
    assert set(entries) == set(before['files']) | set(before['directories'])
    for path, (kind, mode, content) in before['files'].items():
        entry = entries[path]
        assert (entry['kind'], entry['mode']) == (kind, mode)
        if content is None:
            assert entry['payload'] is None
        else:
            assert payload[entry['payload']] == content
            assert hashlib.sha256(content).hexdigest() == entry['sha256']
    for path, mode in before['directories'].items():
        assert (entries[path]['kind'], entries[path]['mode']) == ('directory', mode)


def test_readied_handoff_delivers_complete_material_without_git_rewrite(planned):
    _, planner, owner, root, proof, outside = arrange(planned)
    before = observation(root)
    receipt = preserve(owner)
    assert receipt['status'] == 'handed_off' and receipt['task_status'] == 'newborn'
    assert receipt['verified'] is False and receipt['commit'] == before['head'].decode().strip()
    assert observation(root) == before
    assert {path: path.read_bytes() for path in proof} == proof
    assert outside.read_bytes() == bytes.fromhex(DATA['outside_hex'])
    assert owner.runtime.current_task() is None
    assert owner.runtime.ownership.snapshot('restart-owner').worktree_task_id is None
    manifest, payload = snapshot(receipt)
    assert_snapshot(manifest, payload, before)
    assert Path(receipt['wip_snapshot']['path']).read_bytes().find(outside.read_bytes()) == -1
    with planner.runtime.store.transaction() as database:
        rows = database.execute('SELECT path,digest FROM artifacts WHERE owner=? AND scope=?',
                                ('A', 'task')).fetchall()
    assert (receipt['wip_snapshot']['path'], receipt['wip_snapshot']['digest']) in [tuple(row) for row in rows]


def test_bundle_and_snapshot_reconstruct_staging_split_and_files_independently(planned, tmp_path):
    _, _, owner, root, _, _ = arrange(planned)
    before = observation(root)
    receipt = preserve(owner)
    manifest, payload = snapshot(receipt)
    assert_snapshot(manifest, payload, before)
    # Only the bundle and snapshot remain available at their declared paths.
    root.rename(root.with_name(root.name + '-unavailable'))
    restored = tmp_path / 'isolated-restoration'
    subprocess.run(['git', 'clone', receipt['bundle_path'], str(restored)],
                   check=True, capture_output=True)
    git_text(restored, 'checkout', '-b', before['branch'].decode().strip(), receipt['commit'])
    # Import staged blob bytes from the delivered archive only, independently
    # of the production validator and of the original worktree/object store.
    with tarfile.open(fileobj=io.BytesIO(payload[manifest['staged_tree']['path']])) as staged:
        for number, member in enumerate(staged.getmembers()):
            if member.isdir():
                continue
            blob = tmp_path / f'staged-blob-{number}'
            blob.write_bytes(os.fsencode(member.linkname) if member.issym()
                             else staged.extractfile(member).read())
            git_text(restored, 'hash-object', '-w', '--no-filters', '--', str(blob))
    # The recovery reader consumes the declared format; expected values come
    # from the independent pre-handoff Git/filesystem observation above.
    for entry in manifest['files']:
        path = restored / entry['path']
        if entry['kind'] == 'directory':
            path.mkdir(parents=True, exist_ok=True)
        elif entry['kind'] == 'absent':
            if path.exists() or path.is_symlink():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() or path.is_symlink():
                path.unlink()
            if entry['kind'] == 'symlink':
                path.symlink_to(os.fsdecode(payload[entry['payload']]))
            else:
                path.write_bytes(payload[entry['payload']])
                path.chmod(entry['mode'])
    for entry in manifest['files']:
        if entry['kind'] == 'directory':
            (restored / entry['path']).chmod(entry['mode'])
    index_path(restored).write_bytes(payload[manifest['index']['path']])
    index_path(restored).chmod(manifest['index']['mode'])
    recovered = observation(restored)
    for key in ('head', 'branch', 'stage', 'staged_inventory', 'index', 'index_mode', 'files', 'directories'):
        assert recovered[key] == before[key], key
    assert git_bytes(restored, 'show', ':' + DATA['split']['path']) == bytes.fromhex(DATA['split']['staged_hex'])
    assert (restored / DATA['split']['path']).read_bytes() == bytes.fromhex(DATA['split']['working_hex'])
    assert not (restored / DATA['deleted']).exists()
    assert os.readlink(restored / DATA['symlink']['path']) == DATA['symlink']['target']


def test_real_resume_and_exact_replay_preserve_new_owner(planned):
    project, planner, owner, root, _, _ = arrange(planned)
    before = observation(root)
    receipt = preserve(owner)
    receiver = client(project, 'receiver')
    resumed = bootstrap(receiver, 'A')
    assert resumed['status'] == 'newborn' and resumed['ready'] is True
    assert resumed['worktree'] == str(root) and observation(root) == before
    replay = preserve(owner)
    assert replay == {**receipt, 'replayed': True}
    assert planner.runtime.task_queries.record('A')['claimed_by'] == 'receiver'
    assert receiver.runtime.ownership.snapshot('receiver').worktree_task_id == 'A'
    with pytest.raises(PoiseError, match='identity|different|reused'):
        preserve(owner, reason='A different handoff intent.')
    assert planner.runtime.task_queries.record('A')['claimed_by'] == 'receiver'


@pytest.mark.parametrize('drift', ['index', 'bytes', 'mode', 'snapshot-missing', 'snapshot-corrupt', 'branch', 'head'])
def test_resume_rejects_material_drift_before_acquiring(planned, drift):
    project, planner, owner, root, _, _ = arrange(planned)
    receipt = preserve(owner)
    receiver = client(project, 'receiver')
    if drift == 'index':
        git_text(root, 'add', '--', DATA['split']['path'])
    elif drift == 'bytes':
        (root / DATA['split']['path']).write_bytes(b'drifted working bytes\n')
    elif drift == 'mode':
        (root / DATA['split']['path']).chmod(0o600)
    elif drift == 'branch':
        git_text(root, 'checkout', '--detach', 'HEAD')
    elif drift == 'head':
        branch = git_bytes(root, 'symbolic-ref', 'HEAD')
        old = git_text(root, 'rev-parse', 'HEAD')
        commit = git_text(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                          'commit-tree', 'HEAD^{tree}', '-p', old, '-m', 'Independent HEAD drift')
        git_text(root, 'update-ref', 'HEAD', commit, old)
        assert git_bytes(root, 'symbolic-ref', 'HEAD') == branch
        assert git_text(root, 'rev-parse', 'HEAD') != old
    else:
        # Inject failed/corrupt reads at the existing I/O boundary below;
        # registered immutable snapshot files themselves are never overwritten.
        original = Path.open
        snapshot_path = Path(receipt['wip_snapshot']['path'])
        def corrupt_read(path, *args, **kwargs):
            if path == snapshot_path:
                if drift == 'snapshot-missing':
                    raise FileNotFoundError('snapshot unavailable')
                return io.BytesIO(b'corrupt snapshot bytes')
            return original(path, *args, **kwargs)
        # A context manager keeps fault injection confined to the real resume.
    before = unchanged(planner), observation(root)
    with pytest.MonkeyPatch.context() as patch:
        if drift.startswith('snapshot-'):
            patch.setattr(Path, 'open', corrupt_read)
        with pytest.raises(PoiseError, match='(?i)snapshot|index|worktree|material|branch|changed|сним|измен'):
            bootstrap(receiver, 'A')
    assert (unchanged(planner), observation(root)) == before
    assert receiver.runtime.ownership.snapshot('receiver').task_id is None
    assert receiver.runtime.ownership.snapshot('receiver').worktree_task_id is None


@pytest.mark.parametrize('attribute_source', ['tree', 'info'])
def test_export_ignore_refuses_incomplete_staged_transport_and_retains_claims(planned, attribute_source):
    _, planner, owner, root, _, _ = arrange(planned)
    if attribute_source == 'tree':
        (root / '.gitattributes').write_text(DATA['export_ignore'])
        git_text(root, 'add', '--', '.gitattributes')
    else:
        location = Path(git_text(root, 'rev-parse', '--git-path', 'info/attributes'))
        location = location if location.is_absolute() else root / location
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_text(DATA['export_ignore'])
    before = unchanged(planner), observation(root)
    with pytest.raises(PoiseError, match='(?i)snapshot|archive|staged|сним|архив') as failure:
        preserve(owner)
    assert DATA['split']['path'] in str(failure.value)
    assert (unchanged(planner), observation(root)) == before
    assert owner.runtime.ownership.snapshot('restart-owner').task_id == 'A'
    assert owner.runtime.ownership.snapshot('restart-owner').worktree_task_id == 'A'


@pytest.mark.parametrize('fault', ['snapshot-publication', 'artifact-registration', 'release'])
def test_failed_preservation_retains_claims_and_exact_retry_preserves_snapshot(planned, monkeypatch, fault):
    _, planner, owner, root, _, _ = arrange(planned)
    before = observation(root)
    calls = []
    if fault == 'snapshot-publication':
        import poise.infrastructure.handoff as module
        original = module.atomic_write
        def fail(path, *args, **kwargs):
            if Path(path).suffix == '.tar':
                calls.append(str(path))
                raise OSError('snapshot-publication-failure')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(module, 'atomic_write', fail)
        restore = lambda: monkeypatch.setattr(module, 'atomic_write', original)
    elif fault == 'artifact-registration':
        original = owner.runtime.register_artifact_paths
        def fail(paths, data):
            calls.extend(paths)
            raise OSError('artifact-registration-failure')
        monkeypatch.setattr(owner.runtime, 'register_artifact_paths', fail)
        restore = lambda: monkeypatch.setattr(owner.runtime, 'register_artifact_paths', original)
    else:
        original = owner.runtime.handoff_tools.commands.release
        def fail(*args, **kwargs):
            calls.append('release')
            raise OSError('release-failure')
        monkeypatch.setattr(owner.runtime.handoff_tools.commands, 'release', fail)
        restore = lambda: monkeypatch.setattr(owner.runtime.handoff_tools.commands, 'release', original)
    with pytest.raises(OSError, match=fault + '-failure'):
        preserve(owner)
    assert calls and observation(root) == before
    assert planner.runtime.task_queries.record('A')['claimed_by'] == 'restart-owner'
    assert owner.runtime.ownership.snapshot('restart-owner').worktree_task_id == 'A'
    record = owner.runtime.handoff_tools.commands.lookup('restart-owner', DATA['requests']['handoff']['request_id'])
    saved = None if record is None else deepcopy(record['plan']['wip_snapshot'])
    restore()
    receipt = preserve(owner)
    if saved is not None:
        assert receipt['wip_snapshot'] == saved
    assert observation(root) == before and owner.runtime.current_task() is None
    manifest, payload = snapshot(receipt)
    assert_snapshot(manifest, payload, before)


def test_unready_member_retains_ordinary_wip_message_requirement(planned):
    _, planner, owner, root, _, _ = arrange(planned, ready=False)
    before = unchanged(planner), observation(root)
    with pytest.raises(PoiseError, match='WIP requires explicit'):
        preserve(owner)
    assert (unchanged(planner), observation(root)) == before


def test_context_edit_between_prepare_and_release_retains_claims(planned, monkeypatch):
    _, planner, owner, root, _, _ = arrange(planned)
    before = observation(root)
    original = owner.runtime.handoff_tools.commands.release
    edits = []
    def concurrent_edit(*args, **kwargs):
        revision = planner.runtime.sprint_tools.commands.read('S', 'plan')['aggregate']['revision']
        edits.append(draft(planner, [], revision=revision, request_id='edit-during-preservation', updates=[
            {'kind': 'sections', 'values': {'plan': 'Concurrent plan revision must survive.'}},
        ]))
        return original(*args, **kwargs)
    monkeypatch.setattr(owner.runtime.handoff_tools.commands, 'release', concurrent_edit)
    with pytest.raises(PoiseError, match='(?i)context|sprint|changed|revision|измен'):
        preserve(owner)
    assert edits and observation(root) == before
    assert planner.runtime.task_queries.record('A')['claimed_by'] == 'restart-owner'
    assert owner.runtime.ownership.snapshot('restart-owner').worktree_task_id == 'A'
    assert planner.runtime.sprint_tools.commands.read('S', 'plan')['aggregate']['revision'] == edits[0]['revision']
    record = owner.runtime.handoff_tools.commands.lookup('restart-owner', DATA['requests']['handoff']['request_id'])
    assert record['state'] == 'preparing'


def test_symlink_ancestor_refuses_traversal_and_retains_claims(planned):
    project, planner, owner, root, _, _ = arrange(planned)
    directory = root / DATA['directory']['path']
    directory.rename(root / 'saved-materials')
    outside = project['root'].parent / 'external-directory'
    outside.mkdir()
    sentinel = outside / 'split.bin'
    sentinel.write_bytes(bytes.fromhex(DATA['outside_hex']))
    directory.symlink_to(outside, target_is_directory=True)
    before = unchanged(planner), index_path(root).read_bytes(), git_bytes(root, 'show-ref')
    with pytest.raises(PoiseError, match='(?i)ancestor|unsafe|symlink|symbolic|символ'):
        preserve(owner)
    assert (unchanged(planner), index_path(root).read_bytes(), git_bytes(root, 'show-ref')) == before
    assert sentinel.read_bytes() == bytes.fromhex(DATA['outside_hex'])
    assert os.readlink(directory) == str(outside)
    assert owner.runtime.ownership.snapshot('restart-owner').task_id == 'A'
    assert owner.runtime.ownership.snapshot('restart-owner').worktree_task_id == 'A'


@pytest.mark.parametrize('corruption', ['missing', 'duplicate', 'wrong-blob'])
def test_snapshot_consumer_rejects_inner_archive_with_recomputed_hashes(planned, tmp_path, corruption):
    _, _, owner, root, _, _ = arrange(planned)
    receipt = preserve(owner)
    manifest, payload = snapshot(receipt)
    staged_path = manifest['staged_tree']['path']
    # The same unregistered input path must be accepted before corruption.
    # A validator that rejects all unregistered archives cannot pass this test.
    candidate = tmp_path / 'candidate-snapshot.tar'
    candidate.write_bytes(Path(receipt['wip_snapshot']['path']).read_bytes())
    descriptor = {**receipt['wip_snapshot'], 'path': str(candidate),
                  'digest': hashlib.sha256(candidate.read_bytes()).hexdigest()}
    owner.runtime.handoff_tools._validate_wip_snapshot(root, descriptor)
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(payload[staged_path])) as source:
        with tarfile.open(fileobj=output, mode='w') as destination:
            for member in source.getmembers():
                content = None if not member.isfile() else source.extractfile(member).read()
                if member.name == DATA['split']['path']:
                    assert member.isfile()
                    if corruption == 'missing':
                        continue
                    if corruption == 'wrong-blob':
                        content = b'plausible but incorrect staged bytes\n'
                        member.size = len(content)
                    destination.addfile(member, io.BytesIO(content))
                    if corruption == 'duplicate':
                        destination.addfile(member, io.BytesIO(content))
                else:
                    destination.addfile(member, None if content is None else io.BytesIO(content))
    payload[staged_path] = output.getvalue()
    manifest['staged_tree']['sha256'] = hashlib.sha256(payload[staged_path]).hexdigest()
    payload['manifest.json'] = json.dumps(manifest).encode()
    # This unregistered corruption fixture is never used as a native receipt.
    with tarfile.open(candidate, mode='w') as archive:
        for name, content in payload.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    descriptor = {**receipt['wip_snapshot'], 'path': str(candidate),
                  'digest': hashlib.sha256(candidate.read_bytes()).hexdigest()}
    reasons = {'missing': '(?i)missing|incomplete|omitted|неполн|отсутств',
               'duplicate': '(?i)duplicate|repeated|повтор|дублик',
               'wrong-blob': '(?i)blob|object|content.*mismatch|содержим|объект'}
    with pytest.raises(PoiseError, match=reasons[corruption]) as failure:
        owner.runtime.handoff_tools._validate_wip_snapshot(root, descriptor)
    assert DATA['split']['path'] in str(failure.value)


def test_fixture_guard_export_ignore_omits_independent_index_blob(planned, tmp_path):
    _, _, _, root, _, _ = arrange(planned)
    (root / '.gitattributes').write_text(DATA['export_ignore'])
    git_text(root, 'add', '--', '.gitattributes')
    copied_index = tmp_path / 'copied-index'
    copied_index.write_bytes(index_path(root).read_bytes())
    environment = {**os.environ, 'GIT_INDEX_FILE': str(copied_index)}
    tree = subprocess.check_output(['git', '-C', str(root), 'write-tree'], env=environment).decode().strip()
    archive_path = tmp_path / 'attribute-affected.tar'
    git_text(root, 'archive', '--output=' + str(archive_path), tree)
    with tarfile.open(archive_path) as archive:
        assert DATA['split']['path'] not in archive.getnames()
    assert git_bytes(root, 'show', ':' + DATA['split']['path']) == bytes.fromhex(DATA['split']['staged_hex'])


def test_fixture_guard_recovery_detects_omitted_nonprimary_staged_blob(planned, tmp_path):
    _, _, _, root, _, _ = arrange(planned)
    expected = staged_inventory(root)
    missing = 'src/staged-wip.txt'
    assert missing in expected and missing != DATA['split']['path']
    bundle = tmp_path / 'head-only.bundle'
    git_text(root, 'bundle', 'create', str(bundle), 'HEAD')
    restored = tmp_path / 'oracle-sensitivity'
    subprocess.run(['git', 'clone', str(bundle), str(restored)], check=True, capture_output=True)
    git_text(restored, 'checkout', '-b', git_text(root, 'symbolic-ref', '--short', 'HEAD'),
             git_text(root, 'rev-parse', 'HEAD'))
    for number, (name, (_, oid, content)) in enumerate(expected.items()):
        if name == missing:
            continue
        blob = tmp_path / f'oracle-blob-{number}'
        blob.write_bytes(content)
        assert git_bytes(restored, 'hash-object', '-w', '--no-filters', '--', str(blob)).strip() == oid
    index_path(restored).write_bytes(index_path(root).read_bytes())
    # All path/mode/OID metadata can agree while a staged-only blob is absent.
    assert git_bytes(restored, 'ls-files', '--stage', '-z') == git_bytes(root, 'ls-files', '--stage', '-z')
    with pytest.raises(subprocess.CalledProcessError) as failure:
        staged_inventory(restored)
    assert failure.value.cmd[-1] == ':' + missing
    blob = tmp_path / 'previously-omitted-blob'
    blob.write_bytes(expected[missing][2])
    git_text(restored, 'hash-object', '-w', '--no-filters', '--', str(blob))
    assert staged_inventory(restored) == expected


def test_fixture_guard_qualifying_material_has_distinct_real_states(planned):
    _, planner, owner, root, _, outside = arrange(planned)
    record = planner.runtime.task_queries.record('A')
    assert record['status'] == 'newborn' and record['ready'] is True
    sprint = planner.runtime.sprint_tools.commands.read('S', 'plan')['aggregate']
    assert sprint['state'] == 'draft' and sprint['decisions'][-1]['kind'] == 'unpublish'
    assert git_bytes(root, 'show', ':' + DATA['split']['path']) == bytes.fromhex(DATA['split']['staged_hex'])
    assert (root / DATA['split']['path']).read_bytes() == bytes.fromhex(DATA['split']['working_hex'])
    assert (root / DATA['untracked']['path']).read_bytes() == bytes.fromhex(DATA['untracked']['working_hex'])
    assert not (root / DATA['deleted']).exists()
    assert os.readlink(root / DATA['symlink']['path']) == DATA['symlink']['target']
    assert stat.S_IMODE((root / DATA['directory']['path']).stat().st_mode) == DATA['directory']['mode']
    assert outside.read_bytes() == bytes.fromhex(DATA['outside_hex'])
    assert owner.runtime.ownership.snapshot('restart-owner').task_id == 'A'
