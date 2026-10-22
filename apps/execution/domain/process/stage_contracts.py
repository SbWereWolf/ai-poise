"""Pure stage declaration relationships extracted from the existing Task owner."""
from __future__ import annotations
from dataclasses import dataclass
import fnmatch
import json
from .errors import DomainError
from .workflow import RouteDefinition, exact
from .content_policy import ContentPolicy


def text_list(raw, where):
    if (not isinstance(raw, list)
            or any(not isinstance(item, str) or not item.strip() for item in raw)
            or len(raw) != len(set(raw))):
        raise DomainError(f"{where}: unique nonempty text list required")
    return tuple(raw)


def matches_allowed_path(path, pattern):
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.endswith('/**') and path == pattern[:-3])


@dataclass(frozen=True)
class StageCriteria:
    requirements: tuple[str, ...]
    definition_of_ready: tuple[str, ...]
    definition_of_done: tuple[str, ...]

    @classmethod
    def parse(cls, raw):
        fields = ("requirements", "definition_of_ready", "definition_of_done")
        exact(raw, fields, "criteria")
        return cls(*(text_list(raw[field], f"criteria.{field}") for field in fields))

    def to_dict(self):
        return {name: list(getattr(self, name)) for name in
                ("requirements", "definition_of_ready", "definition_of_done")}



@dataclass(frozen=True)
class TaskStageContract:
    stage_id: str
    allowed_paths: tuple[str, ...]
    entry_requirements: tuple[str, ...]
    exit_requirements: tuple[str, ...]
    criteria: StageCriteria

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "allowed_paths": list(self.allowed_paths),
            "entry_requirements": list(self.entry_requirements),
            "exit_requirements": list(self.exit_requirements),
            "criteria": self.criteria.to_dict(),
        }


def _contract_names(value, label):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise DomainError(f"{label}: requires unique nonempty strings")
    return tuple(value)


def _reachable_without(route: RouteDefinition, start: str, target: str, excluded: str) -> bool:
    pending = [start]
    reached = set()
    while pending:
        stage = pending.pop()
        if stage == excluded or stage in reached:
            continue
        if stage == target:
            return True
        reached.add(stage)
        node = route.node(stage)
        pending.extend(value for _, value in node.transitions if value is not None)
        pending.extend(node.rework_targets)
    return False


@dataclass(frozen=True)
class TaskStageContracts:
    items: tuple[TaskStageContract, ...]

    @classmethod
    def parse(cls, raw, route: RouteDefinition, policy: ContentPolicy):
        if not isinstance(raw, list):
            raise DomainError("stage contracts require an explicit list with exact route coverage")
        parsed = []
        for value in raw:
            if not isinstance(value, dict) or set(value) != {
                "stage_id", "allowed_paths", "entry_requirements", "exit_requirements", "criteria"
            }:
                raise DomainError("stage contract requires exact fields")
            stage_id = value["stage_id"]
            if not isinstance(stage_id, str) or not stage_id:
                raise DomainError("stage contract stage_id is required")
            parsed.append(TaskStageContract(
                stage_id,
                _contract_names(value["allowed_paths"], f"{stage_id}.allowed_paths"),
                _contract_names(value["entry_requirements"], f"{stage_id}.entry_requirements"),
                _contract_names(value["exit_requirements"], f"{stage_id}.exit_requirements"),
                StageCriteria.parse(value["criteria"]),
            ))
        route_stages = tuple(node.stage_id for node in route.nodes)
        if (tuple(item.stage_id for item in parsed) != route_stages
                or len({item.stage_id for item in parsed}) != len(parsed)):
            raise DomainError("stage contracts require exact route coverage in route order")
        requirements = {requirement.id: requirement for requirement in policy.requirements}
        for item in parsed:
            node = route.node(item.stage_id)
            if node.read_only and item.allowed_paths:
                raise DomainError(f"{item.stage_id}: read-only stage cannot have writable scope")
            for phase, refs in (("pre", item.entry_requirements), ("post", item.exit_requirements)):
                for ref in refs:
                    requirement = requirements.get(ref)
                    if requirement is None or requirement.phase != phase or item.stage_id not in requirement.stages:
                        raise DomainError(f"{item.stage_id}: requirement phase mismatch")
        book = cls(tuple(parsed))
        book._validate_artifact_sources(route, policy)
        return book

    def stage(self, stage_id: str) -> TaskStageContract:
        for item in self.items:
            if item.stage_id == stage_id:
                return item
        raise DomainError(f"Unknown Task stage contract: {stage_id}")

    def to_list(self) -> list[dict]:
        return [item.to_dict() for item in self.items]

    def replace(self, stage_id: str, replacement: TaskStageContract) -> TaskStageContracts:
        if replacement.stage_id != stage_id:
            raise DomainError("Replacement stage contract identity mismatch")
        self.stage(stage_id)
        return TaskStageContracts(tuple(
            replacement if item.stage_id == stage_id else item for item in self.items
        ))

    def _validate_artifact_sources(self, route: RouteDefinition, policy: ContentPolicy) -> None:
        active_ids = {
            requirement_id
            for contract in self.items
            for requirement_id in contract.entry_requirements + contract.exit_requirements
        }
        artifact_requirements = [
            requirement for requirement in policy.requirements
            if requirement.kind == "artifact" and requirement.id in active_ids
        ]
        for requirement in artifact_requirements:
            details = json.loads(requirement.details)
            source = details.get("source")
            if source is None or source["kind"] == "preexisting":
                continue
            producer = source.get("producer_stage")
            starts = {route.entry} | {
                target for node in route.nodes for target in node.rework_targets
            }
            for consumer in requirement.stages:
                if source["kind"] == "declared_arrival":
                    arrival = source["arrival_stage"]
                    if consumer != arrival and any(
                        _reachable_without(route, start, consumer, arrival) for start in starts
                    ):
                        raise DomainError(
                            "declared artifact arrival cannot satisfy an earlier entry gate"
                        )
                    continue
                if requirement.phase == "post":
                    if consumer != producer:
                        raise DomainError("stage output must be an exit gate of its producer")
                    consumed = any(
                        candidate.phase == "pre"
                        and json.loads(candidate.details) == details
                        for candidate in artifact_requirements
                    )
                    if consumed and not any(
                        details["pattern"] == pattern
                        or matches_allowed_path(details["pattern"], pattern)
                        for pattern in self.stage(producer).allowed_paths
                    ):
                        raise DomainError("artifact producer scope does not cover its declared output")
                    continue
                if consumer == producer:
                    raise DomainError(
                        "stage output is unavailable at producer entry; require producer exit"
                    )
                bypass = any(
                    _reachable_without(route, start, consumer, producer) for start in starts
                )
                if bypass:
                    label = "rework path does not let producer dominate consumer" if any(
                        start != route.entry and _reachable_without(route, start, consumer, producer)
                        for start in starts
                    ) else "producer does not dominate consumer"
                    raise DomainError(label)
                output = next((candidate for candidate in artifact_requirements
                    if candidate.phase == "post" and producer in candidate.stages
                    and json.loads(candidate.details) == details), None)
                if output is None or output.id not in self.stage(producer).exit_requirements:
                    raise DomainError("artifact producer requires an independent exit gate")


