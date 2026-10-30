"""Public consumer: Query -> agent-side analysis -> Command -> readback/replay."""
from copy import deepcopy

from requirements_registry.crud_support import arrange, assert_effects, invoke, literal, snapshot


def test_agent_combines_public_packets_without_sql_mutations(project, tmp_path):
    database, _, config = arrange(project, tmp_path)
    example = literal("crud_agent_workflow.json")
    before = snapshot(database)
    code, observed = invoke(config, example["query"])
    assert code == 0 and observed == example["query_response"]
    assert snapshot(database) == before

    # This is real consumer analysis of returned data, not an expected-value
    # generator. The expected command and response remain independent literals.
    application = observed["results"][0]["value"]["rows"][-1]
    existing_name = observed["results"][1]["value"]["rows"][0]
    command = deepcopy(example["command"])
    command["input"]["expected_revision"] = observed["revision"]
    command["input"]["operations"][0]["row"]["domain_id"] = existing_name["domain_id"]
    command["input"]["operations"][1]["a"]["filter"]["id"]["eq"] = application["id"]
    assert command == example["command"]
    code, response = invoke(config, command)
    assert code == 0 and response == example["response"]
    step = {"request": example["command"], "response": example["response"], "changes": example["changes"]}
    after = snapshot(database)
    assert_effects(before, after, step)
    assert invoke(config, example["readback"]) == (0, example["readback_response"])
    replay = deepcopy(example["response"])
    replay["replayed"] = True
    assert invoke(config, command) == (0, replay)
    assert snapshot(database) == after
