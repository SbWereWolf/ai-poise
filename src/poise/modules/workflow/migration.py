"""Explicit one-way migration from the retired route count-limit contract."""
from __future__ import annotations

from copy import deepcopy

from ..foundation.errors import DomainError
from .domain import RouteDefinition


LEGACY_ROUTE_FIELDS = frozenset({"entry", "max_transitions", "max_stage_visits"})
CURRENT_ROUTE_FIELDS = frozenset({"entry"})


def migrate_workflow_route(workflow: dict) -> tuple[dict, bool]:
    if not isinstance(workflow, dict) or not isinstance(workflow.get("route"), dict):
        raise DomainError("Migration requires a workflow with an explicit route object")
    fields = frozenset(workflow["route"])
    if fields == CURRENT_ROUTE_FIELDS:
        RouteDefinition.from_workflow(workflow)
        return deepcopy(workflow), False
    if fields != LEGACY_ROUTE_FIELDS:
        raise DomainError("Migration accepts only the current or retired route shape")
    for field in LEGACY_ROUTE_FIELDS - CURRENT_ROUTE_FIELDS:
        value = workflow["route"][field]
        if type(value) is not int or value <= 0:
            raise DomainError("Retired route counts must be positive integers")
    candidate = deepcopy(workflow)
    candidate["route"] = {"entry": candidate["route"]["entry"]}
    RouteDefinition.from_workflow(candidate)
    return candidate, True
