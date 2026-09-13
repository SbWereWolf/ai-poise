"""One pure creation contract for standalone and sprint-published tasks."""
from copy import deepcopy
from .contracts import stages_from_process, candidate_content_policy_from_metadata, evidence_plan_from_metadata
from .domain import Task, TaskStageContracts
from ..verification.domain import (
    CheckRegistry,
    declared_executable_obligations,
    exact_keys,
)
from ..goal_config.domain import GoalTypeDefinition
from ..workflow.domain import RouteDefinition
from ..foundation.errors import DomainError


def path_identifier(value):
    if (not isinstance(value,str) or not value.strip() or value in ('.','..')
            or any(c in value for c in ('/','\\','\x00'))):
        raise DomainError('ID должен быть одним непустым компонентом пути')
    return value


def registry_inspection_stages(process):
    mutable_stages = {
        stage['id'] for stage in process['stages']
        if 'test_registry' in stage['sections']
    }
    return tuple(
        stage['id'] for stage in process['stages']
        if (
            stage['handler'] == 'inspect'
            and stage['transitions']['changes_requested'] in mutable_stages
        )
    )


def executable_obligations(contract, process):
    if not registry_inspection_stages(process):
        return ()
    return declared_executable_obligations(
        contract['executable_obligations'],
        obligation_catalog(contract),
        'task.executable_obligations',
    )


def obligation_catalog(contract):
    return tuple(
        [f"requirements[{index}]" for index, _ in enumerate(contract['requirements'])]
        + [f"definition_of_done[{index}]" for index, _ in enumerate(contract['definition_of_done'])]
    )


def stored_executable_obligations(contract, process, registry_state):
    if not registry_inspection_stages(process):
        return ()
    catalog = obligation_catalog(contract)
    if not isinstance(registry_state, dict) or 'executable_obligations' not in registry_state:
        raise DomainError(
            'Stored Task требует сохранённый registry state.executable_obligations'
        )
    return declared_executable_obligations(
        registry_state['executable_obligations'],
        catalog,
        'stored registry state.executable_obligations',
    )


def validate_creation(contract, process, automatic_checks, decomposition_policy):
    if isinstance(contract,dict) and 'method_inputs' not in contract:
        method_ids = [method.get('id') for method in contract.get('methods',[]) if isinstance(method,dict)]
        raise DomainError(
            f"methods {method_ids}: method_inputs declaration missing; declare one entry for every method"
        )
    fields={'id','sprint_id','goal_type','goal','requirements','definition_of_done',
        'methods','method_inputs','checks','artifact_requirements','content_contract','evidence_plan',
        'stage_contracts','decomposition'}
    if registry_inspection_stages(process):
        fields.add('executable_obligations')
    exact_keys(contract,fields,'task')
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
    from .decomposition import FocusedDecomposition
    FocusedDecomposition.parse(contract['decomposition'], stages).validate(
        decomposition_policy
    )
    route = RouteDefinition.from_process(process)
    registry=CheckRegistry.from_task(contract['methods'],contract['checks'],stages).with_executable_obligations(
        executable_obligations(contract, process),
        registry_inspection_stages(process),
        obligation_catalog(contract),
    )
    from .creation_preflight import CreationPreflight
    CreationPreflight.parse(contract,process)
    registry.validate_route(route)
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
    policy=candidate_content_policy_from_metadata(metadata,{'goal':metadata['process']['content_contract'],
                                                            'task':metadata['contract']['content_contract']})
    route = RouteDefinition.from_process(metadata['process'])
    registry=CheckRegistry.from_task(
        metadata['contract']['methods'], metadata['contract']['checks'], tuple(s.stage_id for s in stages)
    ).with_executable_obligations(
        executable_obligations(metadata['contract'], metadata['process']),
        registry_inspection_stages(metadata['process']),
        obligation_catalog(metadata['contract']),
    )
    registry.validate_route(route)
    contracts = TaskStageContracts.parse(metadata['contract']['stage_contracts'], route, policy)
    args=(metadata['contract']['id'],stages,policy,registry,route,evidence_plan_from_metadata(metadata,registry),contracts)
    if actor is None:return Task.planned(*args)
    return Task.new(args[0],args[1],actor,*args[2:])
