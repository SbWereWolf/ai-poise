"""Real external handler clients and observations for architecture checks.

Subject: public HookService install/event/retry. Helpers arrange fixtures, record
actual calls and read files; they never execute the handler chain themselves.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import time
from threading import Event
from uuid import UUID

from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookService
from tests.hook_transport.helpers import event


FIXTURES = Path(__file__).parent / "fixtures/architecture"


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def trace(marker, boundary, **values):
    root = os.environ.get("POISE_RUN_OUTPUT_DIR")
    group = os.environ.get("ARCH_FIX_GROUP")
    if root and group is not None:
        path = Path(root) / (group + ".subject.jsonl")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"marker": marker, "boundary": boundary, **values},
                                    ensure_ascii=False) + "\n")


class Chain:
    def __init__(self, project, parent, marker):
        self.marker = marker
        self.session = "architecture-fixture"
        self.parent = parent
        self.effects = parent / "handler-effects"
        self.general = GeneralScene(project, parent, marker)
        self.general.publish()
        self.service = HookService(self.general.settings_path)
        self.definition = literal("definition.json")
        self.definition["events"].append(next(
            row["descriptor"] for row in literal("native-events.json")
            if row["descriptor"]["event"] == "SessionEnd"))
        self.definition["routes"]["SessionEnd"] = []
        self.installation = None
        self.configs = []
        self.last_event_id = None
        self.last_event_name = None

    def command(self, label, *, mode="sync", action="continue", failure=None,
                barrier=None, executable=None):
        options = literal("handler-options.json")
        options.update(label=label, folder=str(self.effects), action=action,
                       failure=failure, barrier=barrier)
        path = write(self.parent / (label + ".options.json"), options)
        value = literal("handler-configuration.json")["handlers"][0]
        value["id"] = label
        value["mode"] = mode
        value["implementation"]["argv"] = [
            executable or sys.executable, "-B", str(FIXTURES / "handler.py"), str(path)
        ]
        if mode == "async":
            value["execution"]["launch_seconds"] = 5
        return value

    def configuration(self, name, handlers, *, fixture="first-config.json"):
        value = literal(fixture)
        value.update(id=name, handlers=handlers)
        path = write(self.parent / (name + ".json"), value)
        self.configs.append(path)
        return str(path)

    def install(self, paths, *, event_name="SessionStart"):
        if event_name == "PostToolUse":
            descriptors = literal("native-events.json")
            self.definition["events"] = [row["descriptor"] for row in descriptors
                                         if row["descriptor"]["event"] in (
                                             "SessionStart", "PreToolUse", "PostToolUse")]
            self.definition["routes"] = {row["event"]: [] for row in self.definition["events"]}
        self.definition["routes"][event_name] = paths
        trace(self.marker, "public_install_enter", definition=self.definition)
        try:
            self.installation = self.service.install({
                "request_id": "architecture-install-" + self.parent.name,
                "expected_revision": self.service.revision(),
                "definition": deepcopy(self.definition),
            })
        except PoiseError as exc:
            trace(self.marker, "public_install_refusal", reason=str(exc))
            raise AssertionError(
                self.marker + ": actual new-chain configuration admission refused: " + str(exc)
            ) from exc
        assert self.installation["status"] == "installed", self.marker
        trace(self.marker, "public_install_complete", result=self.installation)

    def invoke(self, extra=None, *, event_name="SessionStart"):
        packet = event(event_name, session=self.session)
        packet["cwd"] = str(self.general.project["app"])
        if extra:
            packet.update(deepcopy(extra))
        trace(self.marker, "public_event_enter", event=packet)
        result = self.service.event(self.installation["definition_path"], packet)
        binding = self.service.latest_binding(self.session, "primary")
        with self.service.registry.read_transaction() as db:
            self.last_event_id = db.execute(
                "SELECT max(id) FROM hook_events WHERE binding=? AND event=?",
                (binding["session_id"], event_name)).fetchone()[0]
        self.last_event_name = event_name
        trace(self.marker, "public_event_return", response=result)
        return result

    def finish(self):
        """Release the real native binding before a later configuration case."""
        packet = event("SessionEnd", session=self.session)
        packet["cwd"] = str(self.general.project["app"])
        result = self.service.event(self.installation["definition_path"], packet)
        trace(self.marker, "actual_subcase_session_end", event=packet, response=result)

    def invoke_hook_process(self):
        """The real versioned native entry exits before the held worker is released."""
        packet = event(session=self.session)
        packet["cwd"] = str(self.general.project["app"])
        environment = {k: v for k, v in self.general.env.items()
                       if k not in ("POISE_CALLER_BINDING", "CODEX_SESSION_ID", "CODEX_THREAD_ID")}
        environment["CODEX_THREAD_ID"] = self.session
        argv = [sys.executable, "-B", "-m", "poise", "hook", "--settings",
                str(self.service.settings.path), "--definition", self.installation["definition_path"]]
        trace(self.marker, "actual_hook_process_enter", argv=argv, event=packet)
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=self.general.project["app"],
                                   env=environment)
        try:
            stdout, stderr = process.communicate(json.dumps(packet, ensure_ascii=False).encode(), timeout=15)
        except BaseException:
            process.kill()
            process.communicate()
            raise
        assert process.returncode == 0 and stderr == b"", (self.marker, process.returncode, stdout, stderr)
        result = json.loads(stdout)
        binding = self.service.latest_binding(self.session, "primary")
        with self.service.registry.read_transaction() as db:
            self.last_event_id = db.execute(
                "SELECT max(id) FROM hook_events WHERE binding=? AND event=?",
                (binding["session_id"], "SessionStart")).fetchone()[0]
        self.last_event_name = "SessionStart"
        trace(self.marker, "actual_hook_process_exited", pid=process.pid,
              actual_exit_code=process.returncode, stdout=stdout.decode(), stderr=stderr.decode())
        return result

    def inputs(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self.effects.glob("*.input.json"))]

    def order(self):
        path = self.effects / "order.jsonl"
        return [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]

    def assert_order(self, expected):
        rows = self.order()
        assert [row["label"] for row in rows] == expected, self.marker
        ids = [row["invocation_id"] for row in rows]
        assert len(set(ids)) == len(expected), self.marker
        for value in ids:
            UUID(value)
        assert len(self.inputs()) == len(expected), self.marker
        trace(self.marker, "actual_handlers_observed", invocations=rows)
        return self.inputs()

    def delivery(self):
        matches = []
        for p in self.service.settings.observations.rglob("*.json"):
            try:
                value = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                continue
            if (isinstance(value, dict) and value.get("schema") == "poise-hook-event-delivery-2"
                    and value.get("event_receipt", {}).get("event_id") == self.last_event_id):
                matches.append((p, value))
        assert len(matches) == 1, (self.marker, "real saved dispatch", matches)
        path, value = matches[0]
        receipt = value["event_receipt"]
        assert receipt["event"] == self.last_event_name, self.marker
        if self.last_event_name == "SessionStart":
            assert receipt["event_id"] == receipt["native_start_event_id"], self.marker
        else:
            assert receipt["event_id"] > receipt["native_start_event_id"], self.marker
        assert receipt["project_id"] and receipt["binding_id"], self.marker
        trace(self.marker, "saved_dispatch_read", path=str(path), result=value)
        return path, value

    def terminal_workers(self, expected):
        # Time only bounds observation of actual saved terminal predicates. It
        # neither orders workers nor determines success from elapsed duration.
        deadline = time.monotonic() + 15
        last = None
        while time.monotonic() < deadline:
            _, last = self.delivery()
            items = last["invocations"]
            terminal = [row for row in items if row.get("mode") == "async"
                        and row.get("status") in ("completed", "failed")]
            if len(terminal) == expected:
                trace(self.marker, "actual_worker_terminal", invocations=terminal)
                return terminal
            Event().wait(0.01)
        raise AssertionError((self.marker, "no actual terminal worker receipt", last))

    def capture_contains(self, expected):
        values = [p.read_bytes() for p in self.service.settings.observations.rglob("*")
                  if p.is_file()]
        assert any(expected in value for value in values), (self.marker, expected)

    def assert_complete_worker_capture(self, worker):
        """Read this invocation's existing common execution receipt and raw files.

        The execution mapping is the existing hook delivery/RegisteredCheckRunner
        capture contract. No directory search, preview, author-side result file,
        alternate handler log or newly generated receipt supplies these bytes.
        """
        packets = [value for value in self.inputs()
                   if value["mode"] == "async" and value["configuration"]["handler_id"] == "worker"]
        assert len(packets) == 1, self.marker
        packet = packets[0]
        assert worker["invocation_id"] == packet["invocation_id"], self.marker
        assert worker["configuration"]["handler_id"] == "worker", self.marker
        assert packet["event_receipt"] == self.delivery()[1]["event_receipt"], self.marker
        assert worker["status"] == "completed", self.marker
        execution = worker["execution"]
        assert execution["actual_exit_code"] == 0, self.marker
        assert execution["capture_complete"] is True, self.marker
        assert execution["timed_out"] is execution["cancelled"] is False, self.marker
        assert execution["termination"] == {
            "schema": "run-termination-1", "child_reaped": True,
            "process_group_stopped": True, "capture_complete": True}, self.marker
        stdout_path, stderr_path = Path(execution["stdout"]), Path(execution["stderr"])
        assert stdout_path.is_absolute() and stderr_path.is_absolute(), self.marker
        assert stdout_path != stderr_path and stdout_path.is_file() and stderr_path.is_file(), self.marker
        stdout, stderr = stdout_path.read_bytes(), stderr_path.read_bytes()
        expected = (FIXTURES / "worker-complete.stdout.template.txt").read_bytes().replace(
            b"@INVOCATION@", packet["invocation_id"].encode("ascii"))
        assert stdout == expected, (self.marker, "complete owned stdout", stdout)
        assert stderr == b"fixture stderr: worker\r\n", (self.marker, "complete owned stderr", stderr)
        # Decode only the actual complete raw stream; the independent fixture
        # supplies all expected fields except the observed runtime identity.
        actual_result, expected_result = json.loads(stdout), json.loads(expected)
        assert actual_result == expected_result, self.marker
        assert actual_result["schema"] == "poise-hook-async-result-1", self.marker
        assert actual_result["invocation_id"] == worker["invocation_id"], self.marker
        assert actual_result["effects"] == {"completed": "worker"}, self.marker
        trace(self.marker, "complete_owned_worker_capture", invocation_id=worker["invocation_id"],
              execution=execution, stdout=stdout.decode(), stderr=stderr.decode(), result=actual_result)


class WorkerBarrier:
    """Actual TCP rendezvous, with bounded IO rather than timing as an oracle."""
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(15)
        self.address = list(self.listener.getsockname())
        self.connections = {}

    def arrived(self, count):
        for _ in range(count):
            connection, _ = self.listener.accept()
            connection.settimeout(15)
            label = b""
            while not label.endswith(b"\n"):
                chunk = connection.recv(1)
                assert chunk, "worker disconnected before barrier"
                label += chunk
            self.connections[label.decode().strip()] = connection
        return set(self.connections)

    def release(self, label):
        connection = self.connections.pop(label)
        connection.sendall(b"R")
        assert connection.recv(1) == b"D", "real worker did not finish its effect"
        connection.close()

    def close(self):
        for connection in self.connections.values():
            connection.close()
        self.listener.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class GeneralScene:
    """Publish a general fixture through its real existing configuration owner.

    Concurrency revision comes from the existing complete Task-config observation
    before first transition, then from actual owner receipts. No filtered document
    or production output creates an expected behavioural answer.
    """
    def __init__(self, project, parent, marker, *, connected=False, settings_path=None,
                 hook_settings=None, installation_id="architecture-hooks"):
        from poise.composition import project_config_tools
        from tests.projects.test_update import project_revision
        self.project, self.parent, self.marker = project, parent, marker
        self.path = project["root"] / "general-profile.json"
        self.settings_path = (project["root"] / "general-hook-settings.json"
                              if settings_path is None else Path(settings_path))
        self.state = project["root"] / "general-runtime"
        self.settings_owner = project["cfg"]["runtime_resources"]["project_setup"]
        self.owner = project_config_tools(self.settings_owner)
        self.revision = project_revision(project["config_path"])
        self.tokens = {"@PROJECT@": project["cfg"]["project"],
                      "@CHECKOUT@": str(project["app"]), "@ROOT@": str(project["root"]),
                      "@TASK_CONFIG@": str(project["config_path"]),
                      "@PROFILE@": str(self.path), "@STATE@": str(self.state),
                      "@PROJECT_SETUP@": self.settings_owner,
                      "@PYTHON@": sys.executable,
                      "@SOURCE@": str(Path(__file__).resolve().parents[2] / "src")}
        self.profile = self.fill(literal("general-profile.json"))
        self.profile["runtime_resources"]["hooks"] = [{
            "installation_id": installation_id, "settings_path": str(self.settings_path)}]
        if connected:
            self.profile["task_adapter"] = self.fill(literal("task-profile-reference.json"))
            old = project["cfg"]["paths"]
            self.profile["paths"].update({k: old[k] for k in ("database", "lock", "runtime", "runs",
                                                                  "stdout", "stderr", "response")})
            self.state = project["root"] / old["state"]
            self.profile["paths"]["state"] = str(self.state)
        raw_settings = self.fill(literal("general-hook-settings.json")) if hook_settings is None else deepcopy(hook_settings)
        raw_settings["project_config"] = str(self.path)
        write(self.settings_path, raw_settings)
        self.initial_task_bytes = project["config_path"].read_bytes()
        self.calls = []
        self.binding = write(parent / "general-caller.json", {
            "schema": "poise-caller-binding-1", "caller_id": "architecture-general-caller"})
        removed = {"CODEX_SESSION_ID", "CODEX_THREAD_ID", "POISE_CALLER_BINDING", "POISE_SESSION", "POISE_CONFIG"}
        self.env = {k: v for k, v in os.environ.items() if k not in removed}
        self.env.update(POISE_CALLER_BINDING=str(self.binding), PYTHONPATH=self.tokens["@SOURCE@"],
                        PYTHONDONTWRITEBYTECODE="1")

    def fill(self, value):
        if isinstance(value, str):
            for token, replacement in self.tokens.items():
                value = value.replace(token, replacement)
            return value
        if isinstance(value, list):
            return [self.fill(v) for v in value]
        if isinstance(value, dict):
            return {k: self.fill(v) for k, v in value.items()}
        return value

    def transition_packet(self, request_id, profile=None, revision=None):
        packet = self.fill(literal("profile-transition.json"))
        packet.update(request_id=request_id, expected_revision=self.revision if revision is None else revision,
                      profile=deepcopy(self.profile if profile is None else profile))
        return packet

    def publish(self, *, request_id="architecture-profile-initial"):
        packet = self.transition_packet(request_id)
        trace(self.marker, "public_profile_transition_enter", request=packet)
        try:
            value = self.owner.apply(packet)
        except PoiseError as exc:
            trace(self.marker, "public_profile_transition_refusal", reason=str(exc))
            raise AssertionError(self.marker + ": real general-profile publication refused: " + str(exc)) from exc
        assert value["status"] == "updated", self.marker
        assert value["config_path"] == str(self.project["config_path"]), self.marker
        assert value["harness_config_path"] == str(self.path), self.marker
        assert self.project["config_path"].read_bytes() == self.initial_task_bytes, self.marker
        assert json.loads(self.path.read_text()) == self.profile, self.marker
        self.revision = value["revision"]
        trace(self.marker, "public_profile_transition_complete", result=value)
        return packet, value

    def invoke(self, operation, *, packet=None, policy=None, environment=None, checkout=None,
               allow_task=False, **extra):
        value = {"operation": operation, "config": str(self.path),
                 "checkout": str(self.project["app"] if checkout is None else checkout),
                 "packet": packet, "policy": None if policy is None else str(policy), **extra}
        cp = subprocess.run([sys.executable, "-B", str(FIXTURES / "general_driver.py")],
                            input=json.dumps(value, ensure_ascii=False).encode(), capture_output=True,
                            env=self.env if environment is None else environment, cwd=self.parent,
                            timeout=40, check=False)
        assert cp.returncode == 0 and cp.stderr == b"", (self.marker, cp.returncode, cp.stderr)
        result = json.loads(cp.stdout)
        self.calls.append(result)
        trace(self.marker, "isolated_public_entry", operation=operation, result=result)
        assert result["exit_code"] == 0, (self.marker, result)
        if not allow_task:
            assert result["sentinel"] == {"task_imports": [], "poise_constructions": 0,
                                          "current_task_calls": 0}, (self.marker, result)
        return result

    def native_definition(self, *, all_events=False, task=False):
        value = literal("definition.json")
        descriptors = literal("native-events.json")
        value["events"] = [row["descriptor"] for row in descriptors
                           if all_events or row["descriptor"]["event"] in ("SessionStart", "SessionEnd")]
        value["routes"] = {row["event"]: [] for row in value["events"]}
        if task:
            from tests.hook_transport.helpers import command_probe
            handler = literal("handler-configuration.json")["handlers"][0]
            handler.update(id="task-context", implementation={"kind": "builtin", "name": "task-context"},
                           options=literal("task-context-options.json"))
            path = write(self.parent / "task-context-handlers.json", {
                "schema": "hook-handler-configuration-1", "id": "task-context", "handlers": [handler]})
            for kind in ("SessionStart", "UserPromptSubmit", "Stop"):
                if kind in value["routes"]:
                    value["routes"][kind] = [str(path)]
            readiness = literal("handler-configuration.json")["handlers"][0]
            readiness.update(id="capability-summary", implementation={"kind": "builtin", "name": "capability-summary"},
                             options={"probes": [command_probe()], "summary": json.loads(
                                 self.settings_path.read_text())["capability_summary"]})
            start = write(self.parent / "task-start-handlers.json", {
                "schema": "hook-handler-configuration-1", "id": "task-start", "handlers": [readiness, handler]})
            value["routes"]["SessionStart"] = [str(start)]
            cleanup = literal("handler-configuration.json")["handlers"][0]
            cleanup.update(id="task-runtime-cleanup", implementation={"kind": "builtin", "name": "task-runtime-cleanup"},
                           options={})
            end = write(self.parent / "task-end-handlers.json", {
                "schema": "hook-handler-configuration-1", "id": "task-end", "handlers": [cleanup]})
            value["routes"]["SessionEnd"] = [str(end)]
        events = [self.fill(row["payload"]) for row in descriptors
                  if row["descriptor"]["event"] in value["routes"]]
        return value, events

    def common_builtin_route(self):
        """Real existing reader/context policies and an external lifecycle client."""
        from tests.content_reader.helpers import Scenario
        from tests.context_resume.helpers import fixture as context_fixture
        from tests.command_intake.helpers import fixture as command_fixture
        from tests.hook_transport.helpers import command_probe
        scenario = Scenario({**self.project, "cfg": self.profile, "config_path": self.path}, self.parent)
        scenario.write("docs/sample.txt", b"A01\nA02\nA03\nA04\n")
        scenario.write("src/billing/invoice.py", b"VALUE = 1\n")
        vector = context_fixture()
        scenario.write(".agents/skills/core/SKILL.md", vector["core_text"].encode())
        scenario.write(".agents/skills/domain/SKILL.md", vector["domain_text"].encode())
        instructions = write(self.parent / "builtin-instructions.json", vector["instruction_policy"])
        catalog = write(self.parent / "builtin-catalog.json", vector["instruction_catalog"])
        command = command_fixture("policy.json")
        command["registry"]["retention"]["max_pending_age_seconds"] = 60
        command_path = write(self.parent / "builtin-command-policy.json", command)
        scenario.data["command_intake"] = {"policy": str(command_path)}
        scenario.save_policy()
        options = deepcopy(vector["general_context"])
        options["reader_policy"] = str(scenario.policy)
        options["instructions"].update(catalog=str(catalog), root=str(self.project["app"]),
                                        policy=str(instructions))
        assert options["work_owner"] == options["check_owner"] == {"enabled": False}
        context_path = write(self.parent / "builtin-context-policy.json", options)
        witnesses = self.parent / "builtin-lifecycle-witnesses"
        settings_raw = json.loads(self.settings_path.read_text())
        root = (self.settings_path.parent / settings_raw["root"]).resolve()
        consumer_input = write(self.parent / "builtin-consumer-input.json", {
            "config": str(self.path), "checkout": str(self.project["app"]),
            "reader_policy": str(scenario.policy),
            "database": str(root / settings_raw["state"] / settings_raw["database"]),
            "witnesses": str(witnesses)})
        consumer = self.fill(literal("builtin-consumer-options.json"))
        consumer["argv"] = [sys.executable, "-B", str(FIXTURES / "native_consumer.py"), str(consumer_input)]

        def builtin(name, handler_options):
            value = literal("handler-configuration.json")["handlers"][0]
            value.update(id=name, implementation={"kind": "builtin", "name": name}, options=handler_options)
            return value

        ready = builtin("capability-summary", {"probes": [command_probe()],
                                                "summary": settings_raw["capability_summary"]})
        lifecycle = builtin("lifecycle-consumer", {"consumer": consumer})
        context = builtin("general-context", {"policy_path": str(context_path)})
        intake = builtin("command-intake", {"policy_path": str(scenario.policy)})
        definition, events = self.native_definition(all_events=True)
        next(row for row in definition["events"] if row["event"] == "SessionStart")["context_limit"] = 4096
        routes = {
            "SessionStart": [ready, lifecycle, context],
            "PreToolUse": [intake], "PostToolUse": [intake],
            "PreCompact": [context], "PostCompact": [context],
            "SessionEnd": [context, lifecycle],
        }
        for event_name, handlers in routes.items():
            path = write(self.parent / ("builtin-" + event_name + ".json"), {
                "schema": "hook-handler-configuration-1", "id": "builtin-" + event_name,
                "handlers": handlers})
            definition["routes"][event_name] = [str(path)]
        for payload in events:
            if payload["hook_event_name"] in ("PreToolUse", "PostToolUse"):
                payload["tool_input"]["command"] = (
                    "cd -- " + shlex.quote(str(self.project["app"])) + " && sed -n '1,2p' docs/sample.txt")
                payload["turn_id"] = "architecture-turn"
        restored_start = deepcopy(events[0])
        restored_start["source"] = "compact"
        events.insert(6, restored_start)
        inputs = {"reader_policy": str(scenario.policy), "witnesses": str(witnesses),
                  "summary_file": settings_raw["capability_summary"]["file"],
                  "checkpoint_file": options["checkpoint_filename"]}
        return definition, events, inputs
