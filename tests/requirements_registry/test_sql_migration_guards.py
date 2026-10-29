"""Actual actor/configured Task scope, not caller paths, authorizes publication."""

from copy import deepcopy
import subprocess

import pytest

from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from requirements_registry.sql_migration_support import applied, files, literal, owned, pair, start, state
from requirements_registry.test_sql_legacy_export import legacy, request


@pytest.fixture
def subject(project):
    return start(project)


def readable(subject):
    contract = literal("contract.json")
    assert owned(subject, contract["query"]) == contract["query_response"]


def test_unbound_actor_cannot_use_another_actors_task(subject):
    readable(subject)
    before, before_ids = state(subject.database), applied(subject.database)
    subject.tools = WorkTools(WorkPoise(subject.project["config_path"], "outside-task-session"))

    with pytest.raises(PoiseError, match="(?i)task|owner|bound"):
        owned(subject, literal("contract.json")["ordinary_create"])

    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == {}


def test_wrong_real_branch_refuses_before_publication(subject):
    readable(subject)
    before, before_ids = state(subject.database), applied(subject.database)
    subprocess.run(["git", "switch", "-c", "wrong-fixture-branch"],
                   cwd=subject.context["worktree"], check=True, capture_output=True)

    with pytest.raises(PoiseError, match="(?i)branch|worktree|owner"):
        owned(subject, literal("contract.json")["ordinary_create"])

    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == {}


@pytest.mark.parametrize("key", ["schema", "schema_directory", "data_directory", "initial_directory"])
def test_missing_required_migration_setting_refuses_explicitly(subject, key):
    readable(subject)
    before = state(subject.database)
    config = deepcopy(subject.project["cfg"])
    del config["requirements_migrations"][key]
    write_json(subject.project["config_path"], config)

    with pytest.raises(PoiseError, match="(?i)config|migration|schema|directory"):
        subject.tools = WorkTools(WorkPoise(subject.project["config_path"], "artisan-session"))
        owned(subject, literal("contract.json")["ordinary_create"])

    assert state(subject.database) == before
    assert files(subject) == {}


def test_unknown_config_key_is_ignored_but_unsupported_schema_refuses(subject):
    readable(subject)
    config = deepcopy(subject.project["cfg"])
    config["requirements_migrations"]["unknown_future_setting"] = "ignored"
    write_json(subject.project["config_path"], config)
    subject.tools = WorkTools(WorkPoise(subject.project["config_path"], "artisan-session"))
    readable(subject)
    before = state(subject.database)
    config["requirements_migrations"]["schema"] = "requirements-sql-unsupported"
    write_json(subject.project["config_path"], config)
    with pytest.raises(PoiseError, match="(?i)schema|version|unsupported"):
        subject.tools = WorkTools(WorkPoise(subject.project["config_path"], "artisan-session"))
        owned(subject, literal("contract.json")["ordinary_create"])
    assert state(subject.database) == before
    assert files(subject) == {}


@pytest.mark.parametrize("field", ["target", "output_directory"])
def test_export_requires_existing_comparison_input_and_actual_task_output_scope(subject, field):
    readable(subject)
    source = legacy(subject)
    packet = request(subject, source, "selected", ["A1"])
    foreign = subject.project["root"] / ("foreign.sqlite" if field == "target" else "other-task-migrations")
    packet["input"][field] = str(foreign)
    before = state(subject.database)

    with pytest.raises(PoiseError, match="(?i)target|comparison|exist|scope|directory|configured|task"):
        owned(subject, packet)

    assert state(subject.database) == before
    assert files(subject) == {}
    assert not foreign.exists()
    assert source.is_file()


def test_mutable_store_tracked_in_git_refuses_before_publication(subject):
    readable(subject)
    root = subject.project["root"]
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "add", str(subject.database.relative_to(root))],
                   cwd=root, check=True, capture_output=True)
    tracked = subprocess.run(["git", "ls-files", "--", str(subject.database.relative_to(root))],
                             cwd=root, check=True, capture_output=True, text=True)
    assert tracked.stdout.strip() == subject.database.relative_to(root).as_posix()
    before = state(subject.database)

    with pytest.raises(PoiseError, match="(?i)git|tracked|version"):
        owned(subject, literal("contract.json")["ordinary_create"])

    assert state(subject.database) == before
    assert files(subject) == {}
    assert subject.database.is_file()


def test_changed_published_bytes_cannot_be_hidden_by_success_receipt(subject):
    contract = literal("contract.json")
    assert owned(subject, contract["ordinary_create"]) == contract["ordinary_create_response"]
    before, before_ids = state(subject.database), applied(subject.database)
    forward = [path for path in subject.directory.glob("*.sql") if not path.name.endswith(".rollback.sql")]
    assert len(forward) == 1
    forward[0].write_bytes(forward[0].read_bytes() + b"SELECT 1;\n")
    modified = files(subject)

    with pytest.raises(PoiseError, match="(?i)content|bytes|migration|conflict"):
        owned(subject, contract["ordinary_create"])

    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == modified


@pytest.mark.parametrize("problem", ["same", "nested", "outside"])
def test_ambiguous_or_out_of_scope_source_directories_refuse(subject, problem):
    readable(subject)
    config = deepcopy(subject.project["cfg"])
    settings = config["requirements_migrations"]
    settings["schema_directory"] = {
        "same": settings["data_directory"],
        "nested": settings["data_directory"] + "/schema",
        "outside": "../foreign-schema",
    }[problem]
    write_json(subject.project["config_path"], config)
    before = state(subject.database)
    with pytest.raises(PoiseError, match="(?i)directory|overlap|scope|path|distinct"):
        subject.tools = WorkTools(WorkPoise(subject.project["config_path"], "artisan-session"))
        owned(subject, literal("contract.json")["ordinary_create"])
    assert state(subject.database) == before
    assert files(subject) == {}


def test_duplicate_migration_ids_across_declared_sources_refuse(subject):
    readable(subject)
    pair(subject, "0010_duplicate", "SELECT 1;\n", "SELECT 1;\n", source="schema")
    pair(subject, "0010_duplicate", "SELECT 2;\n", "SELECT 2;\n", source="data")
    before, before_files = state(subject.database), files(subject)
    with pytest.raises(PoiseError, match="(?i)duplicate|ambiguous|migration"):
        owned(subject, literal("protocol.json")["status_request"])
    assert state(subject.database) == before
    assert files(subject) == before_files


def test_untracking_preserves_database_and_allows_owned_migration_write(subject):
    readable(subject)
    root = subject.project["root"]
    relative = str(subject.database.relative_to(root))
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "add", relative], cwd=root, check=True, capture_output=True)
    assert subprocess.run(["git", "ls-files", "--", relative], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip() == relative
    before = state(subject.database)

    subprocess.run(["git", "rm", "--cached", "--", relative], cwd=root, check=True, capture_output=True)

    assert subject.database.is_file()
    assert state(subject.database) == before
    assert subprocess.run(["git", "ls-files", "--", relative], cwd=root, check=True,
                          capture_output=True, text=True).stdout == ""
    contract = literal("contract.json")
    assert owned(subject, contract["ordinary_create"]) == contract["ordinary_create_response"]
    assert len(files(subject)) == 2
