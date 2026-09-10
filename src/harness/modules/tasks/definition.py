"""One pure creation contract for standalone and sprint-published tasks."""
from copy import deepcopy
from .contracts import stages_from_process, content_policy_from_metadata, evidence_plan_from_metadata
from .domain import Task
from ..verification.domain import CheckRegistry, exact_keys
from ..goal_config.domain import GoalTypeDefinition
from ..workflow.domain import RouteDefinition
from ..foundation.errors import DomainError


def path_identifier(value):
    if (not isinstance(value,str) or not value.strip() or value in ('.','..')
            or any(c in value for c in ('/','\\','\x00'))):
        raise DomainError('ID должен быть одним непустым компонентом пути')
    return value


def validate_creation(contract, process, automatic_checks):
    exact_keys(contract,{'id','sprint_id','goal_type','goal','requirements','definition_of_done',
        'methods','checks','artifact_requirements','content_contract','evidence_plan'},'task')
    path_identifier(contract['id'])
    if contract['sprint_id'] is not None:path_identifier(contract['sprint_id'])
    GoalTypeDefinition.parse(process)
    if process['goal_type']!=contract['goal_type']:
        raise DomainError('Контракт задачи не соответствует process snapshot')
    if not isinstance(contract['goal'],str) or not contract['goal'].strip():
        raise DomainError('Цель задачи обязательна')
    for field in ('requirements','definition_of_done'):
        v=contract[field]
        if not isinstance(v,list) or not v or any(not isinstance(s,str) or not s.strip() for s in v) or len(v)!=len(set(v)):
            raise DomainError(f'{field}: требуются непустые уникальные строки')
    stages=tuple(s['id'] for s in process['stages'])
    registry=CheckRegistry.from_task(contract['methods'],contract['checks'],stages)
    for entry in automatic_checks:
        for stage in stages:
            if stage not in entry['by_stage']:
                raise DomainError(f'Не задан automatic mapping этапа {stage}')
            if not isinstance(entry['by_stage'][stage],list) or set(entry['by_stage'][stage])-set(registry.method_ids):
                raise DomainError(f'Неизвестные точные automatic методы этапа {stage}')
    if not isinstance(contract['artifact_requirements'],list):
        raise DomainError('artifact_requirements должен быть списком')
    for r in contract['artifact_requirements']:
        exact_keys(r,{'scope','pattern','minimum','maximum'},'artifact requirement')
        if (r['scope'] not in ('runtime','task','sprint') or not isinstance(r['pattern'],str)
            or type(r['minimum']) is not int or type(r['maximum']) is not int
            or not 0<=r['minimum']<=r['maximum']):
            raise DomainError('Некорректное требование артефактов')
    metadata={'contract':deepcopy(contract),'process':deepcopy(process)}
    build_task(metadata,None)  # All Content/Evidence/Workflow constructors, no I/O.
    return metadata


def build_task(metadata, actor):
    stages=stages_from_process(metadata['process'])
    policy=content_policy_from_metadata(metadata,{'goal':metadata['process']['content_contract'],
                                                  'task':metadata['contract']['content_contract']})
    registry=CheckRegistry.from_task(metadata['contract']['methods'],metadata['contract']['checks'],tuple(s.stage_id for s in stages))
    args=(metadata['contract']['id'],stages,policy,registry,RouteDefinition.from_process(metadata['process']),evidence_plan_from_metadata(metadata,registry))
    if actor is None:return Task.planned(*args)
    return Task.new(args[0],args[1],actor,*args[2:])
