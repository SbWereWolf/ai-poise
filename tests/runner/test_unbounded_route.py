from copy import deepcopy

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.workflow.domain import RouteDefinition, RouteProgress

from .helpers import process, task


def without_count_limits(value):
    candidate = deepcopy(value)
    candidate["route"] = {"entry": candidate["route"]["entry"]}
    return candidate


def test_route_accepts_only_entry_and_keeps_audit_counters_without_limits():
    route = RouteDefinition.from_process(without_count_limits(process()))
    progress = RouteProgress.initial(route)

    progress = route.enter(progress, "audit")
    for _ in range(61):
        progress = route.enter(progress, "amend")
        progress = route.enter(progress, "follow_up")

    assert progress.transitions == 123
    assert dict(progress.visits)["amend"] == 61
    assert dict(progress.visits)["follow_up"] == 61


def test_legacy_count_limit_fields_are_rejected_by_the_runtime_contract():
    legacy = process()
    legacy["route"].update(max_transitions=12, max_stage_visits=4)
    with pytest.raises(DomainError, match="точный набор полей"):
        RouteDefinition.from_process(legacy)


def test_workflow_context_exposes_history_without_count_limits():
    context = task(without_count_limits(process())).workflow_context()

    assert context["transitions"] == 0
    assert context["visits"]["draft"] == 1
    assert "limits" not in context
