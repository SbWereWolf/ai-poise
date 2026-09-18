"""ID selection resumes authoritative state, not an inferred creation request."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import add_test
from batch.helpers import bootstrap, request, result, verify
from poise.common import PoiseError
from verification.test_current_registry_mutation import (
    OBLIGATIONS, configure_public_registry_case, public_tools, change,
    operation, focused_product_registration,
)


def edited(project, *, handoff=True):
    configure_public_registry_case(project)
    owner = public_tools(project, 'id-resume-executor')
    context = bootstrap(owner, project)
    add_test(context['worktree'])
    wip = Path(context['worktree'])/'tests/keep-wip.txt'
    wip.write_bytes(b'real uncommitted WIP\n')
    payload = result(context)
    payload['method_additions'] = change(
        operation('replace','GREEN',registration=focused_product_registration(project,OBLIGATIONS)),
        operation('remove','CLEANUP_FULL_GREEN'),request_id='id-resume-edit',
    )
    assert verify(owner,payload)['status']=='verified'
    task_id=project['task']['id']
    with owner.runtime.store.unit_of_work() as uow:
        saved=uow.tasks.restart_context(task_id)
    registry=owner.runtime.task_queries.verification_registry(task_id)
    assert registry['revision']==1
    assert owner.runtime.task_queries.record(task_id)['contract'] != saved['contract']
    if handoff:
        released=owner.invoke(request('handoff',{
            'request_id':'id-resume-release','reason':'Pass produced work to a distinct reviewer.',
            'result':None,'commit_message':None,'artifact_paths':[],
        }))
        assert released['status']=='handed_off'
        assert owner.runtime.current_task() is None
    return owner, saved, registry, wip


def resume(tools, contract):
    return tools.invoke(request('bootstrap',{
        'task':contract,'decision':None,'feedback':None,'rework_stage':None,
    }))


def unchanged(runtime, task_id, saved, registry, wip):
    with runtime.store.unit_of_work() as uow:
        assert uow.tasks.restart_context(task_id)==saved
    assert runtime.task_queries.verification_registry(task_id)==registry
    assert wip.read_bytes()==b'real uncommitted WIP\n'


@pytest.mark.parametrize('form',['id_only','original_contract','saved_canonical'])
def test_resume_after_registry_edit_uses_saved_task_without_replanning(project,monkeypatch,form):
    _,saved,registry,wip=edited(project)
    successor=public_tools(project,'id-resume-reviewer')
    def no_live_registry():
        raise AssertionError('Resume is not agreement against a new live registry')
    monkeypatch.setattr(successor.runtime.task_commands.requirements_gate,'_registry_loader',no_live_registry)
    task_id=project['task']['id']
    contract={'id':task_id} if form=='id_only' else deepcopy(project['task'] if form=='original_contract' else saved['contract'])
    out=resume(successor,contract)
    assert out['worktree']==str(wip.parent.parent)
    assert successor.runtime.current_task()['id']==task_id
    unchanged(successor.runtime,task_id,saved,registry,wip)
    version=successor.runtime.current_task()['version']
    resume(successor,{'id':task_id})
    assert successor.runtime.current_task()['version']==version
    unchanged(successor.runtime,task_id,saved,registry,wip)


@pytest.mark.parametrize('field',['goal','snapshot','agreement','current_projection'])
def test_explicit_changed_contract_is_still_rejected_before_acquisition(project,field):
    owner,saved,registry,wip=edited(project)
    successor=public_tools(project,'id-resume-reject')
    task_id=project['task']['id']; contract=deepcopy(project['task'])
    if field=='goal': contract['goal']+=' changed'
    elif field=='snapshot': contract['requirements_snapshot']['task_requirements'][0]['text']+=' changed'
    elif field=='agreement': contract['requirements_agreement']['accepted']=False
    else: contract=owner.runtime.task_queries.record(task_id)['contract']
    before=successor.runtime.task_queries.record(task_id)
    with pytest.raises(PoiseError): resume(successor,contract)
    assert successor.runtime.current_task() is None
    assert successor.runtime.task_queries.record(task_id)==before
    unchanged(successor.runtime,task_id,saved,registry,wip)


def test_id_only_cannot_steal_an_owned_task(project):
    owner,saved,registry,wip=edited(project,handoff=False)
    task_id=project['task']['id'];before=owner.runtime.current_task()
    foreign=public_tools(project,'id-resume-foreign')
    with pytest.raises(PoiseError): resume(foreign,{'id':task_id})
    assert foreign.runtime.current_task() is None
    assert owner.runtime.current_task()==before
    unchanged(owner.runtime,task_id,saved,registry,wip)


def test_id_only_does_not_bypass_reviewer_identity(project):
    owner,saved,registry,wip=edited(project)
    task_id=project['task']['id'];before=owner.runtime.task_queries.record(task_id)
    with pytest.raises(PoiseError,match='Reviewer actor'):
        resume(owner,{'id':task_id})
    assert owner.runtime.current_task() is None
    assert owner.runtime.task_queries.record(task_id)==before
    unchanged(owner.runtime,task_id,saved,registry,wip)
