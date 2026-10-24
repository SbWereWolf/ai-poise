"""A01: real child launch versus completion, isolated response and capture."""
import json

from tests.hook_transport.architecture_support import Chain, WorkerBarrier


def test_async_launch_order_and_isolated_snapshot(project, tmp_path):
    marker = "ARCH_FIX_ASYNC_ORDER"
    chain = Chain(project, tmp_path, marker)
    with WorkerBarrier() as barrier:
        path = chain.configuration("async-order", [
            chain.command("A", mode="async", barrier=barrier.address),
            chain.command("B", mode="async", barrier=barrier.address),
            chain.command("sync")])
        chain.install([path])
        result = chain.invoke()
        assert result == {"systemMessage": "seed|sync"}, marker
        assert barrier.arrived(2) == {"A", "B"}, marker
        _, receipt = chain.delivery()
        # Launch order is the owner's acknowledgement order, not OS scheduling
        # of writes made by already-started subprocesses.
        assert [row["configuration"]["handler_id"] for row in receipt["invocations"]] == [
            "A", "B", "sync"], marker
        barrier.release("B")
        assert not list(chain.effects.glob("*.result.json")) == [], marker
        before_a = [json.loads(p.read_text()) for p in chain.effects.glob("*.result.json")]
        assert not any(v.get("effects", {}).get("completed") == "A" for v in before_a), marker
        barrier.release("A")
        workers = chain.terminal_workers(2)
        assert all(row["status"] == "completed" for row in workers), marker
        inputs = chain.inputs()
        assert len(inputs) == 3, marker
        by_name = {row["configuration"]["handler_id"]: row for row in inputs}
        assert by_name["A"]["response"] == by_name["B"]["response"] == {}, marker
        assert by_name["A"]["invocation_id"] != by_name["B"]["invocation_id"], marker
        assert chain.delivery()[1]["response"] == result, marker


def test_async_outlives_callback_and_keeps_capture(project, tmp_path):
    marker = "ARCH_FIX_ASYNC_LIFETIME"
    chain = Chain(project, tmp_path, marker)
    with WorkerBarrier() as barrier:
        path = chain.configuration("lifetime", [
            chain.command("worker", mode="async", barrier=barrier.address),
            chain.command("sync")])
        chain.install([path])
        response = chain.invoke_hook_process()
        assert response == {"systemMessage": "seed|sync"}, marker
        assert barrier.arrived(1) == {"worker"}, marker
        # Callback has actually returned while the child cannot yet finish.
        assert not any(json.loads(p.read_text()).get("effects", {}).get("completed") == "worker"
                       for p in chain.effects.glob("*.result.json")), marker
        barrier.release("worker")
        assert chain.terminal_workers(1)[0]["status"] == "completed", marker
        chain.capture_contains(b"fixture stderr: worker\r\n")
        chain.capture_contains(b'"completed": "worker"')
        assert chain.delivery()[1]["response"] == response, marker


def test_launch_failure_stops_later_handlers(project, tmp_path):
    marker = "ARCH_FIX_ASYNC_LAUNCH"
    chain = Chain(project, tmp_path, marker)
    path = chain.configuration("launch-failure", [chain.command("one"),
        chain.command("missing", mode="async", executable=str(tmp_path / "no-executable")),
        chain.command("later")])
    chain.install([path])
    chain.invoke()
    chain.assert_order(["one"])
    _, receipt = chain.delivery()
    assert receipt["status"] == "failed", marker
    assert receipt["response"] == {"systemMessage": "seed|one"}, marker
    assert any(row["configuration"]["handler_id"] == "missing"
               and row["status"] == "failed" for row in receipt["invocations"]), marker
    assert not any(row["configuration"]["handler_id"] == "later"
                   for row in receipt["invocations"]), marker


def test_later_worker_failure_preserves_response(project, tmp_path):
    marker = "ARCH_FIX_ASYNC_WORKER"
    chain = Chain(project, tmp_path, marker)
    with WorkerBarrier() as barrier:
        path = chain.configuration("late-failure", [
            chain.command("broken", mode="async", failure="nonzero", barrier=barrier.address),
            chain.command("sync")])
        chain.install([path])
        response = chain.invoke()
        assert response == {"systemMessage": "seed|sync"}, marker
        assert barrier.arrived(1) == {"broken"}, marker
        barrier.release("broken")
        workers = chain.terminal_workers(1)
        assert workers[0]["status"] == "failed", marker
        chain.capture_contains(b"ACTUAL_WORKER_FAILURE\r\n")
        assert chain.delivery()[1]["response"] == response, marker
        assert any(row["label"] == "sync" for row in chain.order()), marker


def test_later_sync_stop_keeps_started_async(project, tmp_path):
    marker = "ARCH_FIX_ASYNC_STOP"
    chain = Chain(project, tmp_path, marker)
    with WorkerBarrier() as barrier:
        path = chain.configuration("later-stop", [
            chain.command("worker", mode="async", barrier=barrier.address),
            chain.command("stop", action="stop"), chain.command("later")])
        chain.install([path])
        response = chain.invoke()
        assert response == {"systemMessage": "seed|stop"}, marker
        assert barrier.arrived(1) == {"worker"}, marker
        barrier.release("worker")
        assert chain.terminal_workers(1)[0]["status"] == "completed", marker
        assert not any(row["label"] == "later" for row in chain.order()), marker
        assert chain.delivery()[1]["response"] == response, marker


def test_async_result_cannot_change_native_response(project, tmp_path):
    marker = "ARCH_FIX_ASYNC_RESPONSE"
    chain = Chain(project, tmp_path, marker)
    for problem in ("response", "control", "wrong-id"):
        parent = tmp_path / problem
        parent.mkdir()
        chain.parent, chain.effects = parent, parent / "handler-effects"
        chain.session = "architecture-async-" + problem
        with WorkerBarrier() as barrier:
            path = chain.configuration("bad-async-" + problem, [
                chain.command("broken", mode="async", failure=problem, barrier=barrier.address),
                chain.command("sync")])
            chain.install([path])
            response = chain.invoke()
            assert response == {"systemMessage": "seed|sync"}, marker
            assert barrier.arrived(1) == {"broken"}, marker
            barrier.release("broken")
            assert chain.terminal_workers(1)[0]["status"] == "failed", marker
            assert chain.delivery()[1]["response"] == response, marker
            chain.capture_contains(b"poise-hook-async-result-1")
            chain.finish()
