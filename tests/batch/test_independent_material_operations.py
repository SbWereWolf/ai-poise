"""Real process overlaps: independent Task material I/O never holds other Tasks."""
from copy import deepcopy
import multiprocessing
import os
from pathlib import Path
import queue
import sqlite3
import threading
import traceback

import pytest

from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from .helpers import bootstrap, configure, request, text_artifact
from .test_artifact_recovery import recover


@pytest.fixture
def legacy(project):
    configure(project)
    owner, context = start_task(project, "T1", "recovery-owner")
    publish(owner, "source material\n", "source.txt")
    old_root = Path(context["task_root"])
    owner.invoke(request("release", {
        "task_id": "T1", "request_id": "release-source",
        "expected_version": owner.runtime.task_queries.record("T1")["_version"],
        "reason": "Read the released source through its configured destination.",
    }))
    project["cfg"]["paths"]["standalone_tasks"] = "relocated"
    write_json(project["config_path"], project["cfg"])
    tools = WorkTools(WorkPoise(project["config_path"], "recovery-reader"))
    records = tools.runtime.store.artifact_records("T1")
    return {"runtime": tools.runtime, "tools": tools, "records": records,
            "old_root": old_root, "new_root": tools.runtime.state / "relocated" / "T1",
            "args": {"request_id": "recover-source", "task_id": "T1",
                     "expected_version": tools.runtime.task_queries.record("T1")["_version"],
                     "artifact_ids": [record["id"] for record in records],
                     "source_roots": {"task": str(old_root)},
                     "reason": "Move registered source bytes to configured destination.",
                     "authorization": "The fixture operator authorized this exact source relocation."}}


SOURCE = Path(__file__).resolve().parents[2] / "src"


class RecoveryWriterHeld(RuntimeError):
    pass


def quiescent_fork():
    # All fixture operations have returned. Drain their actual async telemetry
    # workers before fork, rather than inheriting another thread's locked state.
    for thread in threading.enumerate():
        if thread is not threading.current_thread():
            thread.join(15)
            if thread.is_alive():
                pytest.fail("ARRANGEMENT: fixture background worker did not finish before fork")
    return multiprocessing.get_context("fork")


def start_task(project, identifier, actor):
    """Public fixture creation; expected data is always supplied by the test."""
    tools = WorkTools(WorkPoise(project["config_path"], actor))
    contract = deepcopy(project["task"])
    contract["id"] = identifier
    context = bootstrap(tools, dict(project, task=contract))
    return tools, context


def publish(tools, text="B material\n", name="independent.txt"):
    return tools.invoke(request("artifacts", {
        "items": [text_artifact(path=name, text=text)],
    }))


def worker(operation, output):
    try:
        import poise
        assert Path(poise.__file__).resolve().is_relative_to(SOURCE.resolve())
        output.put(("ok", operation()))
    except BaseException as exc:
        output.put(("error", type(exc).__name__, str(exc), traceback.format_exc(), isinstance(exc, PoiseError)))


def outcome(channel):
    try:
        return channel.get(timeout=15)
    except queue.Empty:
        pytest.fail("WORKER-WATCHDOG: no terminal worker result; this is not product RED")


def isolated_result(operation):
    context = quiescent_fork()
    output = context.Queue()
    process = context.Process(target=worker, args=(operation, output))
    process.start()
    try:
        return outcome(output)
    finally:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("WORKER-WATCHDOG: isolated operation did not stop")


def paused_overlap(first, second, intercept):
    """Intercept actual file I/O in A only. No mocked lock, UoW or authority."""
    context = quiescent_fork()
    paused, resume = context.Event(), context.Event()
    a_output, b_output = context.Queue(), context.Queue()

    def run_first():
        fired = False

        def pause():
            nonlocal fired
            if not fired:
                fired = True
                paused.set()
                if not resume.wait(30):
                    raise RuntimeError("WORKER-WATCHDOG: parent did not release file I/O")

        with pytest.MonkeyPatch.context() as patch:
            intercept(patch, pause)
            return first()

    a = context.Process(target=worker, args=(run_first, a_output))
    b = context.Process(target=worker, args=(second, b_output))
    a_result = None
    try:
        a.start()
        if not paused.wait(15):
            a_result = outcome(a_output)
            pytest.fail(f"ARRANGEMENT: A did not reach file effect: {a_result}")
        b.start()
        b_result = outcome(b_output)
        assert a.is_alive(), "ARRANGEMENT: A must remain paused until B returned"
    finally:
        resume.set()
        # Drain before join: a successful bootstrap can return more than a pipe
        # buffer. Joining its Queue feeder first would deadlock the test itself.
        try:
            if a.pid is not None and a_result is None:
                a_result = outcome(a_output)
        finally:
            stuck = []
            for process in (a, b):
                if process.pid is not None:
                    process.join(20)
                    if process.is_alive():
                        process.terminate()
                        process.join(5)
                        stuck.append(process.pid)
            if stuck:
                pytest.fail("WORKER-WATCHDOG: stuck worker, not product RED")
    return a_result, b_result


def pause_link(destination):
    def intercept(patch, pause):
        original = os.link

        def linked(source, target, *args, **kwargs):
            if Path(target) == destination:
                pause()
            return original(source, target, *args, **kwargs)

        patch.setattr(os, "link", linked)
    return intercept


def assert_material_success(result):
    if result[0] != "ok" and result[1] == "LockUnavailable":
        pytest.fail("INDEPENDENT-MATERIAL-BLOCKED: unrelated Task waited for material I/O", pytrace=False)
    assert result[0] == "ok", result
    assert result[1]["status"] == "artifacts_created"
    assert len(result[1]["artifact_paths"]) == 1
    assert Path(result[1]["artifact_paths"][0]).read_bytes() == b"B material\n"


@pytest.mark.parametrize("operation", ["publication", "recovery"])
def test_independent_task_material_operations_progress(project, legacy, operation):
    # The recovery fixture has a genuinely released Task and registered files.
    b, _ = start_task(project, "B", "independent-B")
    if operation == "recovery":
        record = legacy["records"][0]
        destination = legacy["new_root"] / Path(record["path"]).relative_to(legacy["old_root"])
        first = lambda: recover(legacy)
    else:
        a, context = start_task(project, "A", "publisher-A")
        destination = Path(context["task_root"]) / "artifacts" / "a.txt"
        first = lambda: publish(a, "A material\n", "a.txt")
    a_result, b_result = paused_overlap(first, lambda: publish(b), pause_link(destination))
    assert_material_success(b_result)
    assert a_result[0] == "ok", a_result
    assert destination.is_file()
    with sqlite3.connect(b.runtime.state / b.runtime.paths["database"]) as db:
        assert db.execute("SELECT count(*) FROM artifacts WHERE owner=? AND scope=?",
                          ("B", "task")).fetchone()[0] == 1


@pytest.mark.parametrize("other", ["winner\n", "different\n"])
def test_same_destination_has_one_immutable_winner(project, other):
    configure(project)
    a, context = start_task(project, "T1", "winner-owner")
    # Primitive publication has no Task transition; prepare both before either
    # effect, using the actual composed factory and exact same owned destination.
    factory = a.resources.factory()
    first = factory.prepare([text_artifact(path="winner.txt", text="winner\n")])
    second = factory.prepare([text_artifact(path="winner.txt", text=other)])
    target = Path(context["task_root"]) / "artifacts" / "winner.txt"
    def competing_publication():
        paths = factory.materialize(second)
        return paths, target.stat().st_ino

    left, right = paused_overlap(lambda: factory.materialize(first),
                                competing_publication, pause_link(target))
    if right[0] != "ok" and right[1] == "LockUnavailable":
        pytest.fail("DESTINATION-WINNER-BLOCKED: shared file lock serializes destination contenders", pytrace=False)
    assert right[0] == "ok", right
    paths, winner_inode = right[1]
    assert target.read_bytes() == other.encode()
    assert target.stat().st_ino == winner_inode
    if other == "winner\n":
        assert left == ("ok", paths)
    else:
        assert left[0] == "error" and left[4] is True
        assert any(word in left[2].lower() for word in ("conflict", "different", "changed"))
    inode = target.stat().st_ino
    assert factory.materialize(second) == [str(target)]
    assert target.stat().st_ino == inode


def test_partial_publication_retry_preserves_prior_effects(project, monkeypatch):
    configure(project)
    tools, context = start_task(project, "T1", "partial-owner")
    packet = request("artifacts", {"items": [
        text_artifact(path="one.txt", text="first\n"),
        text_artifact(path="two.txt", text="second\n"),
    ]})
    root = Path(context["task_root"]) / "artifacts"
    original = os.link

    def fault(source, target, *args, **kwargs):
        if Path(target) == root / "two.txt":
            raise OSError("INJECTED-SECOND-PUBLICATION")
        return original(source, target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", fault)
        with pytest.raises(PoiseError, match="INJECTED-SECOND-PUBLICATION"):
            tools.invoke(packet)
    assert (root / "one.txt").read_bytes() == b"first\n"
    inode = (root / "one.txt").stat().st_ino
    result = tools.invoke(packet)
    assert result["status"] == "artifacts_created"
    assert (root / "one.txt").stat().st_ino == inode
    assert (root / "two.txt").read_bytes() == b"second\n"
    with sqlite3.connect(tools.runtime.state / tools.runtime.paths["database"]) as db:
        assert db.execute("SELECT count(*) FROM artifacts WHERE owner=?", ("T1",)).fetchone()[0] == 2


def test_recovery_rechecks_snapshot_after_file_effect(legacy, monkeypatch):
    # Arrange a selected-record revision in this disposable test DB while the
    # file effect is in progress; final linking must observe its stale snapshot.
    h = legacy["runtime"]
    original = os.link
    fired = False

    def change_version(source, target, *args, **kwargs):
        nonlocal fired
        result = original(source, target, *args, **kwargs)
        if not fired and Path(target).is_relative_to(legacy["new_root"]):
            fired = True
            # Separate actual connection makes a long write transaction visible;
            # this is fault arrangement, not the product oracle or managed DB.
            try:
                with sqlite3.connect(h.state / h.paths["database"], timeout=0) as db:
                    db.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T1",))
            except sqlite3.OperationalError as exc:
                if str(exc) == "database is locked":
                    raise RecoveryWriterHeld("File publication retained a write transaction") from exc
                raise
        return result

    before = h.store.artifact_records("T1")
    with monkeypatch.context() as patch:
        patch.setattr(os, "link", change_version)
        try:
            with pytest.raises(PoiseError, match="(?i)version|snapshot|conflict"):
                recover(legacy)
        except RecoveryWriterHeld:
            pytest.fail("RECOVERY-WRITER-HELD: file publication retained a write transaction", pytrace=False)
    assert fired
    assert h.store.artifact_records("T1") == before
    assert any(path.is_file() for path in legacy["new_root"].rglob("*"))
