from copy import deepcopy

import pytest

from batch.helpers import batch_config, request
from poise.modules.foundation.errors import PoiseError
from poise.modules.work.domain import parse_request


VALID = {
    "request_id": "cleanup-1",
    "task_id": "T1",
    "commit_disposition": {
        "kind": "discard_authorized",
        "expected_commit": "a" * 40,
    },
    "authorization": "User authorizes deletion of this exact task commit reference.",
}


def test_cleanup_packet_and_query_have_exact_public_shapes():
    parsed = parse_request(request("cleanup", deepcopy(VALID)), batch_config())
    assert parsed["input"] == VALID
    query = request("show", {"queries": [{
        "id": "cleanup",
        "kind": "task_cleanup",
        "task_id": "T1",
        "request_id": "cleanup-1",
    }]})
    assert parse_request(query, batch_config()) == query


@pytest.mark.parametrize("change,match", [
    (lambda value: value.pop("commit_disposition"), "commit_disposition"),
    (lambda value: value.update(extra="not-allowed"), "cleanup input.*fields"),
    (lambda value: value["commit_disposition"].pop("expected_commit"), "expected_commit"),
    (lambda value: value["commit_disposition"].update(kind="published"), "disposition.*kind|preserved|discard_authorized"),
])
def test_cleanup_packet_rejects_missing_extra_or_forged_publication(change, match):
    value = deepcopy(VALID)
    change(value)
    with pytest.raises(PoiseError, match=match):
        parse_request(request("cleanup", value), batch_config())
