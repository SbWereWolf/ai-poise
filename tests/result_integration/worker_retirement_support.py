"""Transparent arrangements for real Git, external workers and the public API."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools

from .helpers import integration_guard_method, integration_input, prepare_completed_task, request

FIXTURES = Path(__file__).parent / "fixtures/worker_retirement"
OWNER = FIXTURES / "runtime_owner.py"


def wait_ready(process, directory):
    deadline = time.monotonic() + 5
    while not (directory / "ready").exists():
        assert process.poll() is None, "fixture worker exited before readiness"
        assert time.monotonic() < deadline, "fixture worker readiness timed out"
        time.sleep(0.005)


@dataclass
class Case:
    project: dict
    owner: Path
    tree: Path
    accepted: str
    packet: dict
    own: list
    foreign: subprocess.Popen
    configuration: Path

    def invoke(self):
        # Reconstruct the runtime on EVERY request: replay cannot use object memory.
        tools = WorkTools(WorkPoise(self.project["config_path"], "worker"))
        return tools.invoke(deepcopy(self.packet))

    def events(self, kind):
        path = self.owner / "events.jsonl"
        rows = [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]
        return [row for row in rows if row["event"] == kind]

    def cli(self):
        # A separate fixture process gets its caller through the real owner.
        # Never alter the current native binding or use the user's Task stores.
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "POISE_CALLER_BINDING")}
        source = Path(__file__).resolve().parents[2]
        environment.update(
            POISE_CONFIG=str(self.project["config_path"]),
            POISE_CALLER_BINDING=str(self.owner / "cli-caller.json"),
            PYTHONPATH=str(source / "src") + os.pathsep + str(source),
        )
        process = subprocess.run(
            [sys.executable, "-P", "-B", str(FIXTURES / "cli_entry.py"), "work"],
            input=json.dumps(self.packet), text=True, capture_output=True,
            env=environment, cwd=source, timeout=30,
        )
        (self.owner / "cli.stdout").write_text(process.stdout)
        (self.owner / "cli.stderr").write_text(process.stderr)
        envelope = json.loads(process.stdout)
        response = json.loads(Path(envelope["response_path"]).read_text())
        origin = json.loads(process.stderr.splitlines()[0])
        return process.returncode, response, origin

    def mode(self, selected):
        write_json(self.owner / "control.json", {"mode": selected})

    def assert_foreign_available(self):
        assert self.foreign.poll() is None
        result = subprocess.run(
            [sys.executable, str(OWNER), "probe", str(self.owner / "foreign")],
            capture_output=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr.decode()

    def assert_pending(self, result):
        assert result["status"] == "cleanup_pending", result
        assert result["phase"] == "cleanup_pending"
        assert result["publication"]["status"] == "confirmed"
        assert result["accepted_commit"] == self.accepted
        assert self.tree.is_dir()
        assert (self.tree / "src/worker-app.txt").read_text() == "task-owned-app-v1\n"
        assert git(self.project["app"], "rev-parse", "HEAD") == result["integration_head"]
        assert git(self.tree, "symbolic-ref", "--short", "HEAD") == "tasks/T1"
        self.assert_foreign_available()

    def assert_retired(self):
        assert [row["worker"] for row in self.events("stopped")] == ["own-0", "own-1"]
        for number, process in enumerate(self.own):
            assert process.wait(timeout=5) == 0
            stopped = json.loads((self.owner / f"own-{number}/stopped.json").read_text())
            assert stopped == {"payload": "task-owned-app-v1\n"}
        self.assert_foreign_available()


@contextmanager
def scenario(project, monkeypatch, *, declare_before=False):
    owner = project["root"] / "worker-owner"
    owner.mkdir()
    write_json(owner / "control.json", {"mode": "ok"})
    configuration = json.loads((FIXTURES / "preparation.json").read_text())
    configuration["parameters"] = {
        "python": sys.executable, "fixture": str(OWNER), "owner_root": str(owner),
    }
    config_path = write_json(owner / "preparation.json", configuration)
    monkeypatch.syspath_prepend(str(FIXTURES))
    if declare_before:
        project["cfg"]["worktree_preparation"] = {"configuration": str(config_path)}
        original = WorkTools.invoke

        def observe_preparation(self, packet):
            result = original(self, packet)
            if packet["operation"] == "bootstrap":
                # Observe the real public response; do not replace its effects.
                assert result["preparation"]["status"] == "ready", result["preparation"]
            return result

        monkeypatch.setattr(WorkTools, "invoke", observe_preparation)

    processes, streams, own = [], [], []

    def start(name, source):
        stream = (owner / f"{name}.output").open("wb")
        streams.append(stream)
        directory = owner / name
        process = subprocess.Popen(
            [sys.executable, str(OWNER), "worker", str(directory), str(source)],
            stdout=stream, stderr=subprocess.STDOUT,
        )
        processes.append(process)
        wait_ready(process, directory)
        return process

    def change(tree):
        (tree / "src/worker-app.txt").write_text("task-owned-app-v1\n")
        own.extend(start(f"own-{number}", tree) for number in range(2))
        foreign_source = owner / "foreign-source"
        (foreign_source / "src").mkdir(parents=True)
        (foreign_source / "src/worker-app.txt").write_text("task-owned-app-v1\n")
        start("foreign", foreign_source)
        write_json(owner / "owner.json", {
            "task": "T1", "worktree": str(tree), "primary": str(project["app"]),
            "workers": [{"name": f"own-{i}", "pid": p.pid} for i, p in enumerate(own)],
        })

    method = integration_guard_method()
    method.update(
        id="WORKER_GUARD",
        argv=[sys.executable, "-c", (FIXTURES / "check_entry.py").read_text()],
        environment={"WORKER_FIXTURE_SCRIPT": str(OWNER), "WORKER_FIXTURE_OWNER": str(owner)},
        stdout_contains=["worker-guard-ok"],
    )
    try:
        _, tree, accepted = prepare_completed_task(
            project, change, methods=[method], checks=["WORKER_GUARD"],
        )
        cfg = json.loads(project["config_path"].read_text())
        cfg["worktree_preparation"] = {"configuration": str(config_path)}
        write_json(project["config_path"], cfg)
        packet = request("integrate", integration_input(
            project, accepted, request_id="worker-retirement-1",
        ))
        yield Case(project, owner, tree, accepted, packet, own, processes[-1], config_path)
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()
        write_json(owner / "process-teardown.json", {
            "pids": [p.pid for p in processes], "exits": [p.returncode for p in processes],
        })
