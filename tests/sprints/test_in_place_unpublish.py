"""Sprint participants continue existing staged and unfinished work without copies."""
from copy import deepcopy
import os
from pathlib import Path
import stat
import subprocess

import pytest

from batch.helpers import request
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_task_restart import restart
from sprints.helpers import bootstrap
from sprints.test_unpublish import client, planned, release_member, reopen, verified_materials


DATA = {
    'directory': {'path': 'src/materials', 'mode': 488},
    'split': {'path': 'src/materials/split.bin', 'staged_hex': '00696e646578ff0a',
              'working_hex': '00776f726b696e67fe0a', 'mode': 416},
    'untracked': {'path': 'src/materials/untracked.bin',
                  'working_hex': 'ff00756e747261636b65640a', 'mode': 384},
    'executable': {'path': 'src/materials/run.sh', 'text': '#!/bin/sh\nexit 0\n', 'mode': 489},
    'symlink': {'path': 'src/materials/link', 'target': '../double.py'},
    'deleted': 'src/double.py',
    'external_link': 'src/materials/external-link',
    'outside_hex': '00707269766174652d6f757473696465ff0a',
    'ready': {'action': 'ready', 'task_id': 'A', 'request_id': 'ready-existing-materials',
              'expected_revision': 0},
}


def git_bytes(root, *args):
    return subprocess.check_output(['git', '--no-optional-locks', '-C', str(root), *args])


def git_text(root, *args):
    return git_bytes(root, *args).decode().strip()


def observation(root):
    """Raw Git/filesystem oracles independent of production serializers."""
    names = set()
    for argv in (('ls-tree', '-rz', '--name-only', 'HEAD'),
                 ('ls-files', '-z', '--cached', '--others', '--exclude-standard')):
        names.update(os.fsdecode(item) for item in git_bytes(root, *argv).split(b'\0') if item)
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
    staged = {}
    for item in git_bytes(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if item:
            fields, name = item.split(b'\t', 1)
            mode, oid, stage = fields.split()
            assert stage == b'0'
            path = os.fsdecode(name)
            staged[path] = (mode, oid, git_bytes(root, 'show', ':' + path))
    index = Path(git_text(root, 'rev-parse', '--git-path', 'index'))
    if not index.is_absolute():
        index = root / index
    return {
        'head': git_bytes(root, 'rev-parse', 'HEAD'),
        'branch': git_bytes(root, 'rev-parse', '--abbrev-ref', 'HEAD'),
        'refs': git_bytes(root, 'show-ref'),
        'status': git_bytes(root, 'status', '--porcelain=v1'),
        'stage': git_bytes(root, 'ls-files', '--stage', '-z'),
        'staged_inventory': staged,
        'index': index.read_bytes(),
        'index_mode': stat.S_IMODE(index.stat().st_mode),
        'files': files,
        'directories': {name: stat.S_IMODE((root / name).stat().st_mode)
                        for name in sorted(directories)},
    }


def arrange_member(planned, ready):
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
        readied = owner.invoke(request('task', {**DATA['ready'], 'expected_revision': born['revision']}))
        assert readied['status'] == 'newborn' and readied['ready'] is True
    return project, planner, owner, root, proof, outside


@pytest.mark.parametrize('ready', [False, True])
def test_participant_continues_the_exact_unfinished_member_in_place(planned, ready):
    project, planner, owner, root, proof, outside = arrange_member(planned, ready)
    before = observation(root)
    assert before['staged_inventory'][DATA['split']['path']][2] == bytes.fromhex(DATA['split']['staged_hex'])
    assert before['files'][DATA['split']['path']][2] == bytes.fromhex(DATA['split']['working_hex'])
    assert DATA['untracked']['path'] not in before['staged_inventory']
    draft = deepcopy(owner.runtime.current_task()['draft'])

    assert release_member(owner, 'unfinished-A')['status'] == 'ownership_released'

    assert observation(root) == before
    receiver = client(project, 'next-participant')
    resumed = bootstrap(receiver, 'A')
    assert resumed['status'] == 'newborn'
    assert resumed['draft'] == draft
    assert resumed['worktree'] == str(root)
    assert observation(root) == before
    assert outside.read_bytes() == bytes.fromhex(DATA['outside_hex'])
    assert {path: path.read_bytes() for path in proof} == proof
    assert planner.runtime.task_queries.record('A')['claimed_by'] == 'next-participant'


def test_release_replay_does_not_release_the_next_participant(planned):
    project, _, owner, root, _, _ = arrange_member(planned, True)
    current = owner.runtime.current_task()
    packet = request('release', {
        'task_id': 'A', 'request_id': 'same-in-place-release',
        'expected_version': current['_version'], 'reason': 'Continue the actual member in place.',
    })
    assert owner.invoke(packet)['status'] == 'ownership_released'
    receiver = client(project, 'next-participant')
    bootstrap(receiver, 'A')
    before = observation(root)
    assert owner.invoke(packet)['replayed'] is True
    assert receiver.runtime.current_task()['claimed_by'] == 'next-participant'
    assert observation(root) == before


@pytest.mark.parametrize('fault', ['stale', 'foreign'])
def test_invalid_release_preserves_actual_member_and_all_work(planned, fault):
    project, _, owner, root, proof, outside = arrange_member(planned, True)
    current = deepcopy(owner.runtime.current_task())
    before = observation(root)
    packet = request('release', {
        'task_id': 'A', 'request_id': 'invalid-release',
        'expected_version': current['_version'], 'reason': 'Continue existing work.',
    })
    caller = owner
    if fault == 'stale':
        packet['input']['expected_version'] -= 1
        message = 'Release version conflict'
    else:
        caller = client(project, 'foreign-participant')
        message = 'Release requires the named Task owned by the current caller'
    with pytest.raises(PoiseError, match=message):
        caller.invoke(packet)
    assert owner.runtime.current_task() == current
    assert observation(root) == before
    assert outside.read_bytes() == bytes.fromhex(DATA['outside_hex'])
    assert {path: path.read_bytes() for path in proof} == proof
