"""Mixed Query adapts only specialized item errors; legacy calls stay unchanged."""
import pytest

from requirements_registry.crud_support import arrange, assert_effects, invoke, literal, snapshot


CASES = literal("crud_mixed_errors.json") + [{"id": "mixed-positive", **literal("crud_mixed.json")}]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_specialized_error_ownership_in_mixed_and_legacy_queries(project, tmp_path, case):
    database, _, config = arrange(project, tmp_path)
    before = snapshot(database)
    code, response = invoke(config, case["request"])
    assert response == case["response"]
    assert code == (2 if case["response"]["status"] == "rejected" else 0)
    assert_effects(before, snapshot(database), case)
