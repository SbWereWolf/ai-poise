"""Integration subject: one mixed-query snapshot and optimistic writer arbitration."""
from copy import deepcopy
import fcntl
import json
import sqlite3
from threading import Barrier, Event, Thread, get_ident

from conftest import write_json
from poise.composition import requirements_tools
from poise.interfaces import requirements_registry as adapter
from requirements_registry.crud_support import arrange, invoke, literal, snapshot


def configured(project, tmp_path, monkeypatch):
    database, lock, config_path = arrange(project, tmp_path)
    config = project["config_path"]
    settings = json.loads(config.read_text(encoding="utf-8"))
    settings["limits"]["lock_seconds"] = 1
    write_json(config, settings)
    commands, resolved = requirements_tools(config_path)
    monkeypatch.setattr(adapter, "requirements_tools", lambda path: (commands, resolved))
    return database, lock, config_path, commands


def test_mixed_query_is_one_snapshot_while_writer_attempts_lock(project, tmp_path, monkeypatch):
    database, _, config, commands = configured(project, tmp_path, monkeypatch)
    mixed = literal("crud_mixed.json")
    faults = literal("crud_mixed_writer.json")
    before = snapshot(database)
    busy = Event()
    observed = Event()
    writer_started = False
    results, errors, trace = [], [], []
    reader_id = get_ident()
    original_connect = sqlite3.connect
    original_flock = fcntl.flock

    def writer():
        try:
            results.append(invoke(config, faults["request"]))
        except BaseException as exc:
            errors.append(exc)
        finally:
            observed.set()

    worker = Thread(target=writer)

    def observed_flock(handle, operation):
        try:
            return original_flock(handle, operation)
        except BlockingIOError:
            if get_ident() != reader_id:
                busy.set()
                observed.set()
            raise

    class ReaderConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal writer_started
            result = super().execute(sql, parameters)
            if get_ident() == reader_id:
                trace.append(sql)
                if sql.lstrip().upper().startswith("SELECT") and not writer_started:
                    writer_started = True
                    worker.start()
                    assert observed.wait(5), "independent writer neither attempted the lock nor finished"
            return result

    monkeypatch.setattr(fcntl, "flock", observed_flock)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: original_connect(*a, **kw, factory=ReaderConnection))
    try:
        expected = deepcopy(mixed["response"])
        del expected["status"]
        actual = commands.query(mixed["request"]["input"]["queries"])
        assert actual == expected
    finally:
        if writer_started:
            worker.join(5)
            assert not worker.is_alive()
    assert errors == []
    assert writer_started and busy.is_set()
    assert results == [(0, faults["success"])]
    assert trace.count("BEGIN IMMEDIATE") == 1
    assert trace.count("COMMIT") == 1
    assert trace.index("BEGIN IMMEDIATE") < next(i for i, sql in enumerate(trace) if sql.startswith("SELECT"))
    assert trace[-1] == "COMMIT"
    after = snapshot(database)
    assert after["revision"] == [(8,)]
    assert before["tables"]["requirements"] == after["tables"]["requirements"]
    # The competing command changed E1 only after the read batch released.
    with original_connect(database) as db:
        assert db.execute("SELECT definition FROM domain_entities WHERE id=?", ("E1",)).fetchone() == ("Changed.",)
    assert commands.query(mixed["request"]["input"]["queries"]) == faults["subsequent_query"]


def test_two_writers_with_one_revision_commit_only_one_intent(project, tmp_path, monkeypatch):
    database, _, config, _ = configured(project, tmp_path, monkeypatch)
    faults = literal("crud_faults.json")
    barrier = Barrier(2)
    results, errors = [], []

    def writer(identifier):
        try:
            packet = deepcopy(faults["request"])
            packet["input"]["request_id"] = identifier
            barrier.wait(5)
            results.append((identifier, packet, invoke(config, packet)))
        except BaseException as exc:
            errors.append(exc)

    workers = [Thread(target=writer, args=(identifier,)) for identifier in ("writer-a", "writer-b")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert errors == [] and len(results) == 2
    winners = [entry for entry in results if entry[2][0] == 0]
    losers = [entry for entry in results if entry[2][0] == 2]
    assert len(winners) == len(losers) == 1
    assert winners[0][2][1] == faults["success"]
    assert losers[0][2][1] == literal("crud_revision_conflict.json")
    state = snapshot(database)
    assert state["revision"] == [(8,)]
    assert [receipt[0] for receipt in state["receipts"]] == [winners[0][0]]
    assert state["events"] == [("requirements", "update", "A3")]
    assert invoke(config, winners[0][1]) == (0, faults["replay"])
    assert snapshot(database) == state
