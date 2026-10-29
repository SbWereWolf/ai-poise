"""Public consumer: Query -> agent-side analysis -> Command -> readback/replay."""
from copy import deepcopy

from requirements_registry.crud_support import assert_effects, invoke, literal, snapshot
from requirements_registry.owned_crud_transport import arrange_owned, invoke_owned


def test_agent_combines_public_packets_without_sql_mutations(project, tmp_path):
    database, _, config, tools = arrange_owned(project, tmp_path)
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
    code, refusal = invoke(config, command)
    assert code == 2 and refusal['status'] == 'rejected'
    assert 'work.operation=requirements' in refusal['reason']
    assert snapshot(database) == before
    response = invoke_owned(tools, command)
    assert response == example["response"]
    step = {"request": example["command"], "response": example["response"], "changes": example["changes"]}
    after = snapshot(database)
    assert_effects(before, after, step)
    assert invoke(config, example["readback"]) == (0, example["readback_response"])
    replay = deepcopy(example["response"])
    replay["replayed"] = True
    assert invoke_owned(tools, command) == replay
    assert snapshot(database) == after
