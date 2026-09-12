"""Explicit one-way migration from the retired route count-limit contract."""
from __future__ import annotations

from copy import deepcopy

from ..foundation.errors import DomainError
from .domain import RouteDefinition


LEGACY_ROUTE_FIELDS = frozenset({"entry", "max_transitions", "max_stage_visits"})
CURRENT_ROUTE_FIELDS = frozenset({"entry"})


def migrate_process_route(process: dict) -> tuple[dict, bool]:
    if not isinstance(process, dict) or not isinstance(process.get("route"), dict):
        raise DomainError("Migration requires a process with an explicit route object")
    fields = frozenset(process["route"])
    if fields == CURRENT_ROUTE_FIELDS:
        RouteDefinition.from_process(process)
        return deepcopy(process), False
    if fields != LEGACY_ROUTE_FIELDS:
        raise DomainError("Migration accepts only the current or retired route shape")
    for field in LEGACY_ROUTE_FIELDS - CURRENT_ROUTE_FIELDS:
        value = process["route"][field]
        if type(value) is not int or value <= 0:
            raise DomainError("Retired route counts must be positive integers")
    candidate = deepcopy(process)
    candidate["route"] = {"entry": candidate["route"]["entry"]}
    RouteDefinition.from_process(candidate)
    return candidate, True
