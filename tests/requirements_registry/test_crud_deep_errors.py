"""Public CRUD error ownership is independent of malformed JSON nesting depth."""
import json

import pytest

from requirements_registry.crud_support import (
    FIXTURES, arrange, invoke_bytes, literal, snapshot,
)


@pytest.mark.parametrize("case", literal("crud_deep_errors.json"), ids=lambda case: case["id"])
def test_deep_malformed_fields_keep_owned_errors_and_no_effects(project, tmp_path, case):
    database, _, config = arrange(project, tmp_path)
    # Fixture line termination is not part of the reviewer's exact raw request.
    raw = (FIXTURES / case["file"]).read_bytes().removesuffix(b"\n")
    assert len(raw) == case["bytes"] and len(raw) < 4096
    assert isinstance(json.loads(raw), dict), "the request is valid JSON, not a parser rejection"
    before = snapshot(database)
    code, response = invoke_bytes(config, raw)
    assert snapshot(database) == before
    assert code == 2
    assert response == case["response"]
