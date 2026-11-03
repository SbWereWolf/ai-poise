"""GREEN preparation controls; these do not call the missing feature."""
from pathlib import Path

import pytest

from batch.helpers import request
from .helpers import (arrange, client, configure_case, finish_task,
                      prepare_delivery, protected)


@pytest.mark.parametrize('state', ['newborn', 'available', 'active', 'verified', 'accepted'])
def test_state_arrangement_is_real_and_old_read_preserves_it(project, state):
    configure_case(project)
    owner, _ = arrange(project, state)
    assert owner.runtime.task_queries.record('T1')['status'] == state
    before = protected(owner)
    assert owner.invoke(request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))['status'] == 'read_only'
    assert protected(owner) == before


@pytest.mark.parametrize('member', [False, True], ids=['standalone', 'sprint'])
@pytest.mark.parametrize('state', ['completed', 'cancelled'])
def test_real_terminal_fixture_retires_only_its_owned_resources(project, member, state):
    configure_case(project, terminal=True)
    owner, ctx = arrange(project, 'verified', member=member)
    root, destination = prepare_delivery(project, owner, ctx)
    foreign = root.parent / 'FOREIGN/keep.bin'; foreign.parent.mkdir()
    foreign.write_bytes(b'foreign task bytes\n')
    finish_task(project, owner, state)
    assert owner.runtime.task_queries.record('T1')['status'] == state
    assert not root.exists() and not Path(ctx['worktree']).exists()
    assert destination.read_bytes() == b'customer result\n'
    assert foreign.read_bytes() == b'foreign task bytes\n'


def test_missing_optional_setting_allows_old_runtime_and_old_operation(project):
    configure_case(project, storage=False)
    c = client(project)
    out = c.invoke(request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))
    assert out['status'] == 'read_only'
    assert out['results'][0]['value']['status'] == 'read_only'
    assert not project['material_root'].exists()


def test_cli_fixture_old_read_operates_in_a_fresh_process(project):
    from terminal_materials.helpers import cli
    configure_case(project)
    arrange(project)
    exit_code, response = cli(project, request('show', {
        'queries': [{'id': 'task', 'kind': 'task'}]}))
    assert exit_code == 0, response
    assert response['status'] == 'read_only'


def test_real_active_cancellation_is_a_valid_overlap_prerequisite(project):
    configure_case(project)
    owner, _ = arrange(project, 'active')
    result = owner.invoke(request('cancel', {'reason': 'Fixture executor ends its work.'}))
    assert result['status'] == 'cancelled'
    assert owner.runtime.task_queries.record('T1')['status'] == 'cancelled'
