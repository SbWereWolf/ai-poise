"""Public boundary bounds apply to packet items, not selected mutation rows."""
from contextlib import closing
import json
import sqlite3

import pytest

from conftest import write_json
from requirements_registry.crud_support import (
    arrange, assert_effects, invoke, invoke_bytes, literal, snapshot,
)


def test_one_command_may_change_more_rows_than_max_items(project, tmp_path):
    database, _, config_path = arrange(project, tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["batch"]["max_items"] = 1
    write_json(config_path, config)
    case = literal("crud_many_rows.json")
    with closing(sqlite3.connect(database)) as db:
        db.executemany(
            "INSERT INTO domain_entities(id,domain_id,business_name,code_name,definition) "
            "VALUES(?,?,?,?,?)", case["extra_rows"],
        )
        db.execute("DELETE FROM observed_crud_writes")
        db.commit()
    before = snapshot(database)
    assert invoke(config_path, case["request"]) == (0, case["response"])
    assert_effects(before, snapshot(database), case)


def test_byte_limit_retains_existing_before_dispatch_error(project, tmp_path):
    database, _, config = arrange(project, tmp_path)
    before = snapshot(database)
    code, response = invoke_bytes(config, b" " * 4097)
    assert code == 2
    assert response == {
        "status": "rejected", "reason": "Requirements packet exceeds max_input_bytes",
    }
    assert snapshot(database) == before


@pytest.mark.parametrize("case", literal("crud_scalar_rows.json"), ids=lambda case: case["id"])
def test_existing_identifier_text_and_literal_whitespace_invariants(project, tmp_path, case):
    database, _, config = arrange(project, tmp_path)
    before = snapshot(database)
    code, response = invoke(config, case["request"])
    assert response == case["response"]
    assert code == (0 if case["response"]["status"] == "applied" else 2)
    assert_effects(before, snapshot(database), case)
