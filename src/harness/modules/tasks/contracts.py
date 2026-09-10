from .domain import StageSpec
from ..content.domain import SectionRule
from ..foundation.errors import DomainError


def stages_from_process(process: dict) -> tuple[StageSpec, ...]:
    """Pure explicit mapping, shared by creation and repository rehydration."""
    try:
        return tuple(StageSpec(stage["id"], tuple(
            SectionRule(name, text, stage["normalization"], name in stage["required_sections"])
            for name, text in stage["sections"].items())) for stage in process["stages"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise DomainError(f"Неполный/неверный контракт этапов: {exc}") from exc


def content_policy_from_metadata(metadata: dict, layers: dict):
    from ..content_requirements.domain import ContentPolicy
    process = metadata["process"]
    contract = metadata["contract"]
    return ContentPolicy.from_layers(
        layers["goal"], layers["task"], tuple(s["id"] for s in process["stages"]),
        tuple(contract["requirements"]), tuple(m["id"] for m in contract["methods"]),
        tuple(sorted({name for stage in process["stages"] for name in stage["sections"]})))


def evidence_plan_from_metadata(metadata: dict, registry):
    from ..evidence.domain import EvidencePlan
    process = metadata['process']
    checks = {s['id']:[e.method_id for e in registry.entries if s['id'] in e.stages] for s in process['stages']}
    return EvidencePlan.parse(metadata['contract']['evidence_plan'],
                              {s['id']:s['handler'] for s in process['stages']},checks)
