"""Shared Task admission using the caller's existing unit of work."""
from ..modules.tasks.duplicates import assess_duplicate_start, DuplicateStartBlocked


def require_duplicate_start_in(uow, task_id, actor, *, force_duplicate_start=False,
                               target_stage=None):
    family = uow.tasks.read_duplicate_family(task_id)
    decision = assess_duplicate_start(family, task_id, actor,
        force_duplicate_start=force_duplicate_start, target_stage=target_stage)
    if decision is not None and not decision['allowed']:
        raise DuplicateStartBlocked(decision)
    return decision


def create_duplicate_in(uow, task_id, source_id, sprint_id, actor, config_hash):
    """Clone a declared contract, not execution/results; callers own replay/UoW."""
    from copy import deepcopy
    from dataclasses import replace
    from ..modules.foundation.errors import DomainError
    from ..modules.sprints.domain import Sprint
    from ..modules.tasks.newborn import NewbornTask
    from ..modules.tasks.duplicates import family_projection

    if sprint_id is None:
        raise DomainError('Duplicate requires a destination Sprint')
    family = uow.tasks.read_duplicate_family(source_id)
    parent_id = source_id if family is None else family['parent_id']
    if task_id == parent_id or uow.tasks.exists(task_id):
        raise DomainError('Duplicate requires a new Task identity')
    context = uow.tasks.restart_context(parent_id)
    contract, process = context['contract'], context['process']
    if not isinstance(contract, dict) or not isinstance(process, dict):
        raise DomainError('Duplicate requires an established parent contract')
    record = uow.sprints.get(sprint_id)
    if record is None:
        raise DomainError('Unknown Sprint for duplicate membership')
    sprint = Sprint.restore(record['aggregate']).add_newborn_member(task_id)
    draft = {k: deepcopy(v) for k, v in contract.items() if k not in ('id', 'sprint_id')}
    for key in ('requirements_snapshot', 'requirements_agreement'):
        if context[key] is not None:
            draft[key] = deepcopy(context[key])
    newborn = replace(NewbornTask.create(task_id,sprint_id,None),
                      draft=draft,process=deepcopy(process))
    uow.tasks.create_newborn(newborn, config_hash)
    uow.tasks.link_duplicate(task_id,parent_id,actor)
    uow.sprints.save({**record,'actor':actor,'aggregate':sprint.to_dict()}, sprint.revision-1)
    family = uow.tasks.read_duplicate_family(task_id)
    return newborn.describe() | {'sprint_revision': sprint.revision,
                                 'duplicate': family_projection(family,task_id)}


def require_duplicate_transition_in(uow, task, change, actor, *, force_duplicate_start=False):
    """Validate both the current admission and the prospective new stage in one UoW."""
    before, after = task.state, change.task.state
    if after.status in ('completed','cancelled') or (
            (before.stage_index,before.iteration)==(after.stage_index,after.iteration)):
        return
    family = uow.tasks.read_duplicate_family(before.task_id)
    if family is None:
        return
    if before.status not in ('completed','cancelled'):
        decision=assess_duplicate_start(family,before.task_id,actor,
            force_duplicate_start=force_duplicate_start)
        if not decision['allowed']:
            raise DuplicateStartBlocked(decision)
    prospective = {'parent_id':family['parent_id'],'members':[
        (member | {'stage_id':change.task.stage.stage_id,'status':after.status.value,
                   'iteration':after.iteration})
        if member['task_id']==before.task_id else member for member in family['members']]}
    decision=assess_duplicate_start(prospective,before.task_id,actor,
        force_duplicate_start=force_duplicate_start)
    if not decision['allowed']:
        raise DuplicateStartBlocked(decision)


def restart_local_repair_in(uow, task, history, validate_workspace):
    """Record acquired family input, never inherit a relative's successful checks."""
    import json
    from copy import deepcopy
    from ..modules.foundation.errors import DomainError

    saved = (json.loads(task.duplicate_reuse)['candidate']
             if task.duplicate_reuse is not None else
             next((r['local_repair'] for r in reversed(history) if 'local_repair' in r), None))
    family = uow.tasks.read_duplicate_family(task.state.task_id)
    if family is None:
        return None
    completed = [m['task_id'] for m in family['members']
                 if m['task_id'] != task.state.task_id and m['status'] == 'completed']
    if saved is not None and saved['source_task_id'] not in completed:
        raise DomainError('Local repair requires its completed family input')
    execution = (uow.execution.load(task.state.task_id)[0]
                 if uow.execution.exists(task.state.task_id) else None)
    for source_id in ([saved['source_task_id']] if saved is not None else completed):
        source_execution, _ = uow.execution.load(source_id)
        report = source_execution['last_report']
        if report is None or report.get('status') != 'completed' or not report.get('commit'):
            raise DomainError('Local repair requires an accepted source receipt')
        if saved is not None and report['commit'] != saved['source_commit']:
            raise DomainError('Local repair source receipt changed')
        proposal = saved or {'source_task_id': source_id, 'source_commit': report['commit']}
        if validate_workspace is None:
            raise DomainError('Task restart requires local Git input validation')
        observed = validate_workspace(execution, proposal)
        if observed is not None:
            return {**deepcopy(observed), 'source_task_id': source_id,
                    'source_commit': report['commit'],
                    'branch': None if execution is None else execution['branch'],
                    'worktree': None if execution is None else observed['worktree']}
    if saved is not None:
        raise DomainError('Reuse source is absent from local branch; import it before restart')
    return None  # Restart does not invent a local acquisition that never happened.
