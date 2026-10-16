from .domain import StageSpec
from ..content.domain import SectionRule
from ..foundation.errors import DomainError


def stages_from_workflow(workflow: dict) -> tuple[StageSpec, ...]:
    """Pure explicit mapping, shared by creation and repository rehydration."""
    try:
        return tuple(StageSpec(stage["id"], tuple(
            SectionRule(name, text, stage["normalization"], name in stage["required_sections"])
            for name, text in stage["sections"].items())) for stage in workflow["stages"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise DomainError(f"Неполный/неверный контракт этапов: {exc}") from exc


def candidate_content_policy_from_metadata(metadata: dict, layers: dict):
    from ..content_requirements.domain import ContentPolicy
    workflow = metadata["workflow"]
    contract = metadata["contract"]
    return ContentPolicy.from_layers(
        layers["goal"], layers["task"], tuple(s["id"] for s in workflow["stages"]),
        tuple(contract["requirements"]), tuple(m["id"] for m in contract["methods"]),
        tuple(sorted({name for stage in workflow["stages"] for name in stage["sections"]})))


def stored_content_policy_from_metadata(metadata: dict, layers: dict):
    from ..content_requirements.domain import ContentPolicy
    workflow = metadata["workflow"]
    contract = metadata["contract"]
    return ContentPolicy.restore_layers(
        layers["goal"], layers["task"], tuple(s["id"] for s in workflow["stages"]),
        tuple(contract["requirements"]), tuple(m["id"] for m in contract["methods"]),
        tuple(sorted({name for stage in workflow["stages"] for name in stage["sections"]})))


def evidence_plan_from_metadata(metadata: dict, registry):
    from ..evidence.domain import EvidencePlan
    workflow = metadata['workflow']
    checks = {s['id']:[e.method_id for e in registry.entries if s['id'] in e.stages] for s in workflow['stages']}
    return EvidencePlan.parse(metadata['contract']['evidence_plan'],
                              {s['id']:s['handler'] for s in workflow['stages']},checks)
