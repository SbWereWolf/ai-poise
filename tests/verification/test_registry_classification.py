"""Classification changes must not require fictitious method operations."""

from copy import deepcopy
from dataclasses import replace

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.verification.domain import CheckRegistry
from verification.test_current_registry_mutation import (
    OBLIGATIONS, OBLIGATION_CATALOG, STAGES, change, initial_registry,
    operation, registration,
)


def registry(populated):
    if populated:
        return initial_registry()
    return CheckRegistry.from_items([], STAGES).with_executable_obligations(
        OBLIGATIONS, ("test_inspection",), OBLIGATION_CATALOG,
    )


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "methods"])
@pytest.mark.parametrize("target", [(), ("requirements[0]",), OBLIGATION_CATALOG],
                         ids=["clear", "reduce", "expand"])
def test_classification_only_creates_one_revision(populated, target):
    current = registry(populated)
    before = deepcopy(current.to_state())
    packet = change(executable_obligations=target)

    candidate = current.apply_change(packet).registry

    assert candidate.executable_obligations == target
    assert candidate.entries == current.entries
    assert candidate.stages == current.stages
    assert candidate.inspection_stages == current.inspection_stages
    assert candidate.revision == 1
    assert len(candidate.history) == 1
    assert candidate.history[0].to_dict() == {
        "revision": 0, "entries": [entry.to_dict() for entry in current.entries],
    }
    assert len(candidate.requests) == 1
    assert candidate.requests[0].request_id == "registry-1"
    assert candidate.requests[0].revision == 1
    assert current.to_state() == before


def test_classification_replay_after_change_is_exact_and_conflict_is_atomic():
    current = registry(False)
    packet = change(executable_obligations=())
    changed = current.apply_change(packet).registry
    before = deepcopy(changed.to_state())

    replay = changed.apply_change(deepcopy(packet))

    assert replay.replayed is True
    assert replay.registry.to_state() == before
    conflict = {**packet, "executable_obligations": ["requirements[0]"]}
    with pytest.raises(DomainError, match="request_id"):
        changed.apply_change(conflict)
    assert changed.to_state() == before


def test_classification_stale_revision_is_atomic():
    changed = registry(False).apply_change(change(executable_obligations=())).registry
    before = deepcopy(changed.to_state())
    with pytest.raises(DomainError, match="revision conflict"):
        changed.apply_change(change(request_id="stale", executable_obligations=OBLIGATIONS))
    assert changed.to_state() == before


def test_empty_unchanged_request_is_rejected_without_revision():
    current = registry(False)
    before = deepcopy(current.to_state())
    with pytest.raises(DomainError):
        current.apply_change(change())
    assert current.to_state() == before


@pytest.mark.parametrize("invalid", [None, "requirements[0]", [1],
                                    ["requirements[99]"],
                                    ["requirements[0]", "requirements[0]"]])
def test_invalid_classification_is_rejected_without_mutation(invalid):
    current = registry(False)
    before = deepcopy(current.to_state())
    with pytest.raises(DomainError):
        current.apply_change({**change(), "executable_obligations": invalid})
    assert current.to_state() == before


def test_reclassification_preserves_prior_method_history_and_request():
    current = initial_registry().apply_change(change(
        operation("replace", "REPLACE", registration=registration("REPLACE", "new")),
        operation("remove", "DROP"), request_id="methods-before-classification",
    )).registry
    before = deepcopy(current.to_state())

    candidate = current.apply_change(change(
        request_id="classification-after-methods", revision=1,
        executable_obligations=(),
    )).registry

    assert candidate.revision == 2
    assert candidate.entries == current.entries
    assert candidate.method_ids == ("REPLACE", "MOVE")
    assert candidate.executable_obligations == ()
    assert candidate.history[:-1] == current.history
    assert candidate.history[-1].to_dict() == {
        "revision": 1, "entries": [entry.to_dict() for entry in current.entries],
    }
    assert candidate.requests[:-1] == current.requests
    assert current.to_state() == before


def test_classification_cannot_discard_retained_method_coverage():
    current = initial_registry().apply_change(change(operation(
        "replace", "REPLACE", registration=registration("REPLACE", "covered"),
    ))).registry
    restricted = replace(current, obligation_catalog=("requirements[0]",))
    before = deepcopy(restricted.to_state())
    with pytest.raises(DomainError, match=r"definition_of_done\[0\]"):
        restricted.apply_change(change(
            request_id="drop-covered-ref", revision=1,
            executable_obligations=("requirements[0]",),
        ))
    assert restricted.to_state() == before
