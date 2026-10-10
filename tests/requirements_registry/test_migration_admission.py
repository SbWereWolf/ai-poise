"""Public migration storage admission and actionable recovery regressions."""
from pathlib import Path

import pytest

from tests.requirements_registry.test_domain_migration import connection, invoke, legacy, migrate, old_rows


def snapshot(database):
    with connection(database) as db:
        return db.execute("PRAGMA user_version").fetchone(), list(db.iterdump())


def test_schema1_read_explains_cause_and_executable_selected_project_recovery(legacy):
    config, database = legacy
    before = snapshot(database)
    code, reply = invoke(config, "query", {"queries": [{"id": "r", "kind": "registry"}]})
    assert code == 2
    assert "schema 1" in reply["reason"]
    assert '{"operation":"migrate","input":{}}' in reply["reason"]
    assert "python -m poise requirements --config" in reply["reason"]
    assert snapshot(database) == before
    assert invoke(config)[0] == 0
    assert invoke(config, "query", {"queries": [{"id": "r", "kind": "registry"}]})[0] == 0
    assert old_rows(database)[2] == [(7,)]


@pytest.mark.parametrize("version", [1, 2])
def test_foreign_sqlite_header_does_not_establish_requirements_identity(legacy, version):
    config, database = legacy
    with connection(database) as db:
        for table in ("requirement_links", "requirement_requests", "requirements_meta", "requirements"):
            db.execute(f"DROP TABLE {table}")
        db.execute("CREATE TABLE unrelated(id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO unrelated(id) VALUES(?)", ("preserve",))
        db.execute(f"PRAGMA user_version={version}")
    before = snapshot(database)
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert "Requirements DB structure" in reply["reason"]
    assert snapshot(database) == before


@pytest.mark.parametrize("sql", [
    "DROP TABLE requirement_requests",
    "ALTER TABLE requirements RENAME COLUMN text TO wrong_text",
    "ALTER TABLE requirements_meta RENAME COLUMN revision TO wrong_revision",
    "ALTER TABLE requirement_links RENAME COLUMN application_id TO wrong_application",
])
def test_malformed_legacy_structure_rejects_before_any_ddl(legacy, sql):
    config, database = legacy
    with connection(database) as db:
        db.execute(sql)
    before = snapshot(database)
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert "Requirements DB structure" in reply["reason"]
    assert snapshot(database) == before


@pytest.mark.parametrize("table", ["domain_areas", "domain_entities", "domain_actions",
                                   "requirement_entities", "requirement_actions"])
def test_v2_replay_requires_all_supported_structures(legacy, table):
    config, database = migrate(legacy)
    with connection(database) as db:
        db.execute(f"DROP TABLE {table}")
    before = snapshot(database)
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert "Requirements DB structure" in reply["reason"]
    assert snapshot(database) == before


def test_v2_replay_rejects_wrong_dictionary_columns(legacy):
    config, database = migrate(legacy)
    with connection(database) as db:
        db.execute("ALTER TABLE domain_entities RENAME COLUMN code_name TO wrong_code")
    before = snapshot(database)
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert "Requirements DB structure" in reply["reason"]
    assert snapshot(database) == before


def test_migration_preserves_additional_unrelated_objects(legacy):
    config, database = legacy
    before = old_rows(database)
    with connection(database) as db:
        db.execute("CREATE TABLE extra(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO extra(id,value) VALUES(?,?)", ("keep", "Unrelated data."))
        db.execute("CREATE VIEW extra_view AS SELECT id,value FROM extra")
    assert invoke(config)[0] == 0
    assert invoke(config)[0] == 0
    assert old_rows(database) == before
    with connection(database) as db:
        assert db.execute("SELECT id,value FROM extra_view").fetchall() == [("keep", "Unrelated data.")]


@pytest.mark.parametrize("fixture", ["schema2_area_without_checks.sql", "schema2_area_nocase.sql"])
def test_v2_replay_rejects_missing_checks_or_case_insensitive_definition(legacy, fixture):
    config, database = migrate(legacy)
    with connection(database) as db:
        db.execute("DROP TABLE domain_areas")
        db.execute((Path(__file__).parent / "fixtures" / fixture).read_text())
    before = snapshot(database)
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert "Requirements DB structure" in reply["reason"]
    assert snapshot(database) == before


@pytest.mark.parametrize("statement", [
    "CREATE UNIQUE INDEX wrong_names ON domain_areas(business_name COLLATE NOCASE)",
    "CREATE UNIQUE INDEX wrong_names ON domain_areas(business_name COLLATE NOCASE) WHERE 1",
    "CREATE UNIQUE INDEX wrong_names ON domain_areas(lower(business_name))",
])
def test_v2_replay_rejects_case_insensitive_unique_index(legacy, statement):
    config, database = migrate(legacy)
    with connection(database) as db:
        db.execute(statement)
    before = snapshot(database)
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert "Requirements DB structure" in reply["reason"]
    assert snapshot(database) == before
