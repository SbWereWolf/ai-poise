"""GREEN sensitivity controls for the exact single-journal-row exception."""

from copy import deepcopy
import json

import pytest

from runtime_services.restart_auto_noop_support import FIXTURE, assert_single_noop


@pytest.mark.parametrize("damage", [
    None, "missing", "duplicate", "digest", "result", "task", "actor", "event", "prior",
])
def test_noop_oracle_rejects_any_other_state_change(damage):
    fixture = json.loads(FIXTURE.read_text())
    before = fixture["before"]
    after = deepcopy(before)
    row = deepcopy(fixture["row"])
    row[-1] = json.dumps(row[-1], sort_keys=True)
    after["owned"]["journal"].append(row)
    if damage == "missing":
        after["owned"]["journal"].pop()
    elif damage == "duplicate":
        after["owned"]["journal"].append(deepcopy(row))
    elif damage in ("digest", "result"):
        data = json.loads(row[-1])
        if damage == "digest":
            data["digest"] = "f" * 64
        else:
            data["result"]["replay"]["reason"] = "work_required"
        row[-1] = json.dumps(data, sort_keys=True)
    elif damage == "task":
        row[3] = "T2"
    elif damage == "actor":
        row[2] = "foreign-actor"
    elif damage == "event":
        row[4] = "progression.started"
    elif damage == "prior":
        after["owned"]["journal"][0][-1] = "changed prior bytes"
    if damage is None:
        assert_single_noop(before, after, commit=fixture["result"]["replay"]["start_commit"])
    else:
        with pytest.raises(AssertionError):
            assert_single_noop(before, after, commit=fixture["result"]["replay"]["start_commit"])

