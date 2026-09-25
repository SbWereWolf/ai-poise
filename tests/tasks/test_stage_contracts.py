from copy import deepcopy

import pytest

from poise.modules.content_requirements.domain import ContentPolicy
from poise.modules.foundation.errors import DomainError
from poise.modules.workflow.domain import RouteDefinition


EMPTY = {"sections": [], "routes": [], "requirements": []}


def _feature():
    from poise.modules.tasks import domain

    feature = getattr(domain, "TaskStageContracts", None)
    assert feature is not None, "TaskStageContracts must own exact Task-specific stage gates and scope"
    return feature


def _stage(stage_id, *, handler="produce", transitions=None, rework_targets=None,
           read_only=False, allowed_paths=None):
    if transitions is None:
        transitions = {"complete": None}
    return {
        "id": stage_id,
        "handler": handler, "role": "executor",
        "transitions": transitions,
        "rework_targets": [] if rework_targets is None else rework_targets,
        "read_only": read_only,
        "allowed_paths": ["src/**"] if allowed_paths is None else allowed_paths,
    }


def _process():
    return {
        "route": {"entry": "prepare"},
        "stages": [
            _stage("prepare", transitions={"complete": "inspect"}, rework_targets=["prepare"]),
            _stage(
                "inspect",
                handler="inspect",
                transitions={"clear": "consume", "changes_requested": "prepare"},
                rework_targets=["prepare"],
                read_only=True,
                allowed_paths=[],
            ),
            _stage("consume", rework_targets=["prepare"], allowed_paths=["app/**"]),
        ],
    }


def _artifact(rule_id, stage, phase, source, pattern="build/**"):
    return {
        "id": rule_id,
        "kind": "artifact",
        "stages": [stage],
        "phase": phase,
        "scope": "task",
        "pattern": pattern,
        "minimum": 1,
        "maximum": 1,
        "source": source,
    }


def _policy(process, requirements):
    stages = tuple(stage["id"] for stage in process["stages"])
    return ContentPolicy.from_layers(
        EMPTY,
        {"sections": [], "routes": [], "requirements": requirements},
        stages,
        (),
        (),
        (),
    )


def _contracts():
    return [
        {
            "stage_id": "prepare",
            "allowed_paths": ["build/**"],
            "entry_requirements": [],
            "exit_requirements": ["built-output"],
        },
        {
            "stage_id": "inspect",
            "allowed_paths": [],
            "entry_requirements": [],
            "exit_requirements": [],
        },
        {
            "stage_id": "consume",
            "allowed_paths": ["app/**"],
            "entry_requirements": ["built-input"],
            "exit_requirements": [],
        },
    ]


def _requirements():
    source = {"kind": "stage_output", "producer_stage": "prepare"}
    return [
        _artifact("built-output", "prepare", "post", source),
        _artifact("built-input", "consume", "pre", source),
    ]


def _parse(*, process=None, contracts=None, requirements=None):
    feature = _feature()
    process = _process() if process is None else process
    route = RouteDefinition.from_process(process)
    policy = _policy(process, _requirements() if requirements is None else requirements)
    return feature.parse(
        _contracts() if contracts is None else contracts,
        route,
        policy,
    )


def test_creation_requires_stage_contract_for_every_route_stage():
    contracts = _contracts()[:-1]
    with pytest.raises(DomainError, match="exact route coverage"):
        _parse(contracts=contracts)


def test_stage_contract_requires_exact_route_coverage():
    contracts = _contracts() + [{
        "stage_id": "foreign",
        "allowed_paths": [],
        "entry_requirements": [],
        "exit_requirements": [],
    }]
    with pytest.raises(DomainError, match="exact route coverage"):
        _parse(contracts=contracts)


def test_stage_contract_references_matching_gate_phases():
    contracts = _contracts()
    contracts[0]["entry_requirements"] = ["built-output"]
    contracts[0]["exit_requirements"] = []
    with pytest.raises(DomainError, match="phase"):
        _parse(contracts=contracts)


def test_stage_contract_gate_refs_select_existing_policy_conditions():
    contracts = _contracts()
    requirements = _requirements() + [
        _artifact(
            "optional-input",
            "consume",
            "pre",
            {"kind": "preexisting"},
            pattern="optional/**",
        )
    ]
    parsed = _parse(contracts=contracts, requirements=requirements)
    assert parsed.stage("consume").entry_requirements == ("built-input",)


def test_stage_output_is_exit_only_for_its_producer():
    source = {"kind": "stage_output", "producer_stage": "prepare"}
    requirements = [_artifact("built-input", "prepare", "pre", source)]
    contracts = _contracts()
    contracts[0]["entry_requirements"] = ["built-input"]
    contracts[0]["exit_requirements"] = []
    contracts[2]["entry_requirements"] = []
    with pytest.raises(DomainError, match="producer.*exit"):
        _parse(contracts=contracts, requirements=requirements)


def test_artifact_stage_output_must_dominate_consumer():
    process = _process()
    process["route"]["entry"] = "inspect"
    process["stages"][0]["transitions"] = {"complete": "consume"}
    process["stages"][1]["transitions"] = {
        "clear": "prepare",
        "changes_requested": "consume",
    }
    with pytest.raises(DomainError, match="dominate"):
        _parse(process=process)


def test_artifact_dominance_covers_rework_entry_paths():
    process = _process()
    process["stages"][2]["rework_targets"] = ["inspect"]
    process["stages"][1]["transitions"] = {
        "clear": "consume",
        "changes_requested": "prepare",
    }
    with pytest.raises(DomainError, match="rework.*dominate"):
        _parse(process=process)


def test_declared_arrival_cannot_satisfy_earlier_entry():
    source = {"kind": "declared_arrival", "arrival_stage": "inspect"}
    requirements = [_artifact("arrived", "prepare", "pre", source)]
    contracts = _contracts()
    contracts[0]["entry_requirements"] = ["arrived"]
    contracts[0]["exit_requirements"] = []
    contracts[2]["entry_requirements"] = []
    with pytest.raises(DomainError, match="arrival"):
        _parse(contracts=contracts, requirements=requirements)


def test_artifact_producer_must_have_writable_scope_and_exit_gate():
    contracts = _contracts()
    contracts[0]["allowed_paths"] = ["src/**"]
    with pytest.raises(DomainError, match="producer.*scope"):
        _parse(contracts=contracts)


def test_artifact_producer_must_have_independent_exit_gate():
    contracts = _contracts()
    contracts[0]["exit_requirements"] = []
    with pytest.raises(DomainError, match="producer.*exit"):
        _parse(contracts=contracts)


def test_read_only_stage_rejects_writable_scope():
    contracts = _contracts()
    contracts[1]["allowed_paths"] = ["docs/**"]
    with pytest.raises(DomainError, match="read-only"):
        _parse(contracts=contracts)


def test_task_specific_scope_replaces_template_scope():
    contracts = _contracts()
    contracts[0]["allowed_paths"] = ["docs/task-0081/**"]
    requirements = []
    contracts[0]["exit_requirements"] = []
    contracts[2]["entry_requirements"] = []
    parsed = _parse(contracts=contracts, requirements=requirements)
    assert parsed.stage("prepare").allowed_paths == ("docs/task-0081/**",)
    assert parsed.stage("prepare").allowed_paths != ("src/**",)
