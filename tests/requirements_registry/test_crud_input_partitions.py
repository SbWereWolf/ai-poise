"""Public validation owners and exact upper bounds; independent literal cases."""
import pytest

from requirements_registry.crud_support import arrange, assert_effects, invoke, literal, snapshot


CASES = literal("crud_input_partitions.json") + literal("crud_relation_cursor_mismatch.json")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_missing_type_boundary_and_relation_cursor_classes(project, tmp_path, case):
    database, _, config = arrange(project, tmp_path)
    for step in case["steps"]:
        before = snapshot(database)
        code, response = invoke(config, step["request"])
        assert response == step["response"]
        assert code == (2 if step["response"]["status"] == "rejected" else 0)
        assert_effects(before, snapshot(database), step)
