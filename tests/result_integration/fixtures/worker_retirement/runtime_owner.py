"""An explicit test-owned runtime, never a product process-management adapter."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from uuid import uuid4


def read(path):
    return json.loads(Path(path).read_text())


def append(root, event):
    with (root / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(event) + "\n")


def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError("fixture handshake timed out")
        time.sleep(0.005)


def worker(root, source):
    root.mkdir()
    (root / "ready").write_text(str(os.getpid()))
    while True:
        probe = root / "probe"
        if probe.exists():
            nonce = probe.read_text()
            payload = (source / "src/worker-app.txt").read_text()
            probe.unlink()
            pending = root / "reply.pending"
            pending.write_text(json.dumps({"nonce": nonce, "payload": payload}))
            pending.replace(root / "reply")
        if (root / "stop").exists():
            payload = (source / "src/worker-app.txt").read_text()
            (root / "stopped.json").write_text(json.dumps({"payload": payload}))
            return
        time.sleep(0.005)


def probe_worker(root):
    nonce = uuid4().hex
    pending = root / "probe.pending"
    pending.write_text(nonce)
    pending.replace(root / "probe")

    def received():
        path = root / "reply"
        return path.exists() and read(path)["nonce"] == nonce

    wait(received)
    assert read(root / "reply")["payload"] == "task-owned-app-v1\n"


def check(root):
    control = read(root / "control.json")
    probe_worker(root / "own-0")
    assert (Path.cwd() / "src/worker-app.txt").read_text() == "task-owned-app-v1\n"
    append(root, {"event": "check", "cwd": str(Path.cwd())})
    if control["mode"] == "check_failure":
        print("fresh-worker-check-failed", file=sys.stderr)
        return 19
    print("worker-guard-ok")
    return 0


def retire(root, coordinates):
    worktree, task, project, task_root, base, request_id = coordinates
    append(root, {"event": "retire_call", "cwd": str(Path.cwd()),
                  "worktree": worktree, "task": task, "project": project,
                  "task_root": task_root, "base": base, "request_id": request_id})
    selected = read(root / "owner.json")
    if worktree != selected["worktree"] or task != selected["task"]:
        print("fixture-runtime-owner-mismatch", file=sys.stderr)
        return 73
    assert str(Path.cwd()) == worktree
    target = subprocess.check_output(
        ["git", "-C", selected["primary"], "rev-parse", "HEAD"], text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert target == head, "retirement must follow confirmed publication"
    assert (Path(worktree) / "src/worker-app.txt").read_text() == "task-owned-app-v1\n"
    control = read(root / "control.json")
    if control["mode"] == "refuse":
        print("fixture-runtime-retirement-refused", file=sys.stderr)
        print("complete-retirement-output")
        return 71
    for item in selected["workers"]:
        state = root / item["name"]
        if (state / "stopped.json").exists():
            continue
        if (state / "busy").exists():
            print("fixture-runtime-work-in-flight", file=sys.stderr)
            return 75
        # pidfd observes actual process termination, including an unreaped child.
        # The PID comes only from this test's saved Popen handle.
        descriptor = os.pidfd_open(item["pid"])
        try:
            (state / "stop").touch()
            if not select.select([descriptor], [], [], 5)[0]:
                print("fixture-runtime-stop-unconfirmed", file=sys.stderr)
                return 74
        finally:
            os.close(descriptor)
        assert read(state / "stopped.json")["payload"] == "task-owned-app-v1\n"
        append(root, {"event": "stopped", "worker": item["name"], "request_id": request_id})
    print("fixture-runtime-retired")
    return 0


if __name__ == "__main__":
    operation, root, *arguments = sys.argv[1:]
    root = Path(root)
    if operation == "worker":
        worker(root, Path(arguments[0]))
        code = 0
    elif operation == "check":
        code = check(root)
    elif operation == "probe":
        probe_worker(root)
        code = 0
    elif operation == "prepare":
        worktree, task, project, task_root, base = arguments
        append(root, {"event": "prepare", "cwd": str(Path.cwd()), "worktree": worktree,
                      "task": task, "project": project, "task_root": task_root, "base": base})
        code = 0
    elif operation == "retire":
        code = retire(root, arguments)
    else:
        raise RuntimeError("unknown fixture operation")
    raise SystemExit(code)
