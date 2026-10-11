"""New requirements/link writes feed the existing specialized planning consumers."""
from requirements_registry.crud_support import arrange, assert_effects, invoke, literal, snapshot


def test_specialized_queries_read_meaningful_crud_changes_and_future_placeholders(project, tmp_path):
    database, _, config = arrange(project, tmp_path)
    fixture = literal("crud_specialized_after_command.json")
    command = fixture["command"]
    before = snapshot(database)
    code, response = invoke(config, command["request"])
    assert response == command["response"] and code == 0
    after = snapshot(database)
    assert_effects(before, after, command)
    for query in fixture["queries"]:
        code, response = invoke(config, query["request"])
        assert response == query["response"] and code == 0
        assert snapshot(database) == after
