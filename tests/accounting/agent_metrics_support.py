"""Test-owned input publication and external observations, without a parser/sink."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sqlite3
import shlex
import sys
import time

from tests.hook_transport.architecture_support import GeneralScene, write


FIXTURES = Path(__file__).parent / "fixtures" / "agent_metrics"
SESSION = "00000000-0000-4000-8000-000000000161"


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def replace_tokens(value, tokens):
    if isinstance(value, str):
        for key, text in tokens.items():
            value = value.replace(key, str(text))
        return value
    if isinstance(value, list):
        return [replace_tokens(item, tokens) for item in value]
    if isinstance(value, dict):
        return {key: replace_tokens(item, tokens) for key, item in value.items()}
    return value


class MetricsScene:
    """Fixture setup uses public project owners, then actual native/CLI entries."""

    def __init__(self, project, parent, *, connected=False):
        self.parent, self.project = parent, project
        self.connected = connected
        self.general = GeneralScene(project, parent, "F16_METRICS_FIXTURE", connected=connected)
        self.general.publish()
        self.metrics = parent / "persistent-metrics"
        self.sources = parent / "native-owned-sources"
        self.sources.mkdir()
        self.session = SESSION
        self.transcript_path = self.sources / "session.jsonl"
        self.policy_path = parent / "metrics-policy.json"
        self.tokens = {"@METRICS@": self.metrics, "@SOURCE@": self.sources,
                       "@CHECKOUT@": project["app"], "@POLICY@": self.policy_path}
        self.policy = replace_tokens(literal("policy.json"), self.tokens)
        write(self.policy_path, self.policy)
        self.definition, self.events = self.general.native_definition(all_events=True)
        self.installation = None
        self.started = False
        self.calls = []
        self.python = sys.executable
        self.isolated = False

    def source(self, name="complete"):
        raw = (FIXTURES / (name + ".jsonl")).read_text(encoding="utf-8")
        text = replace_tokens(raw, self.tokens)
        target = self.transcript_path
        target.write_text(text, encoding="utf-8")
        return target

    def next_session(self, identity, source_filename):
        assert not self.started, "F16_FIXTURE_SESSION_MUST_END_BEFORE_THE_NEXT_START"
        self.session = identity
        self.tokens[SESSION] = identity
        self.transcript_path = self.sources / source_filename

    def add_source(self, specification):
        """Publish an explicit test-owned source, preserving the real parser boundary."""
        entry = replace_tokens(specification, self.tokens)
        assert not any(item["id"] == entry["id"] for item in self.policy["collection"]["sources"])
        self.policy["collection"]["sources"].append(entry)
        self.policy["accounting"]["sources"][entry["id"]] = "observed"
        write(self.policy_path, self.policy)

    def configure_metrics(self):
        handler = replace_tokens(literal("handler.json"), self.tokens)
        address = write(self.parent / "metrics-handler.json", handler)
        for kind in self.definition["routes"]:
            self.definition["routes"][kind] = [str(address), *self.definition["routes"][kind]]

    def install(self, *, metrics=True):
        from poise.infrastructure.hook_transport import HookService
        if metrics:
            self.configure_metrics()
        self.service = HookService(self.general.settings_path)
        # Only the known missing public capability is an intended admission RED.
        from poise.common import PoiseError
        try:
            request = literal("install-request.json")
            request.update(expected_revision=self.service.revision(), definition=self.definition)
            self.installation = self.service.install(request)
        except PoiseError as exc:
            if "Unregistered builtin handler" in str(exc):
                raise AssertionError("F16_METRICS_NATIVE_CAPABILITY_MISSING: " + str(exc)) from exc
            raise
        assert self.installation["status"] == "installed"

    def native(self, kind, *, gate=None, commit_exit=None, **changes):
        event = deepcopy(next(item for item in self.events if item["hook_event_name"] == kind))
        event.update(session_id=self.session, cwd=str(self.project["app"]),
                     transcript_path=str(self.transcript_path))
        if "turn_id" in event:
            event["turn_id"] = "turn-f16"
        event.update(changes)
        environment = {key: value for key, value in self.general.env.items()
                       if key not in {"POISE_CALLER_BINDING", "CODEX_SESSION_ID", "CODEX_THREAD_ID"}}
        # These are isolated fixture events, not the executor's live native context.
        environment["CODEX_THREAD_ID"] = self.session
        cli_arguments = ["hook", "--settings", str(self.general.settings_path),
                         "--definition", self.installation["definition_path"]]
        python_arguments = [self.python, *(["-I"] if self.isolated else []), "-B"]
        arguments = [*python_arguments, "-m", "poise", *cli_arguments]
        if gate is None and commit_exit is None:
            result = subprocess.run(arguments, input=json.dumps(event).encode(),
                                    capture_output=True, cwd=self.project["app"], env=environment)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        else:
            request = literal("driver-request.json")
            observer = Path(__file__).parents[1] / "hook_transport/fixtures/architecture/task_observer.py"
            request.update(argv=cli_arguments, packet=event, observer=str(observer), gate=gate,
                           commit_exit=commit_exit)
            result = subprocess.run([*python_arguments, str(FIXTURES / "cli_driver.py")],
                                    input=json.dumps(request).encode(), capture_output=True,
                                    cwd=self.project["app"], env=environment)
            assert result.returncode == 0 and not result.stderr, ("F16_GATE_CLIENT_FAILED", result.stderr)
            observed = json.loads(result.stdout)
            self.record({"kind": "gated-cli", **observed})
            assert observed["unexpected"] is None, observed
            code, stdout, stderr = observed["exit_code"], observed["stdout"].encode(), observed["stderr"].encode()
        witness = {"kind": "native", "event": event, "argv": arguments,
                   "exit_code": code, "stdout": stdout.decode(), "stderr": stderr.decode()}
        self.record(witness)
        assert code == 0, ("F16_NATIVE_DELIVERY_FAILED", witness)
        if kind == "SessionStart":
            self.started = True
        if kind == "SessionEnd":
            self.started = False
        return json.loads(stdout)

    def record(self, value):
        self.calls.append(value)
        with (self.parent / "client-witnesses.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

    def execute_tool(self, call_id, *, exit_code=0):
        argv = [sys.executable, "-B", str(FIXTURES / "tool_effect.py"),
                str(self.parent / "actual-tool-effects.txt"), str(exit_code)]
        inputs = {"tool_name": "Bash", "tool_use_id": call_id,
                  "tool_input": {"command": shlex.join(argv)}}
        self.native("PreToolUse", **inputs)
        actual = subprocess.run(argv, cwd=self.project["app"], capture_output=True)
        self.record({"kind": "real-tool", "argv": argv, "exit_code": actual.returncode,
                     "stdout": actual.stdout.decode(), "stderr": actual.stderr.decode()})
        response = {"stdout": actual.stdout.decode(), "stderr": actual.stderr.decode(),
                    "exit_code": actual.returncode}
        native = self.native("PostToolUse", **inputs, tool_response=response)
        return {"exit_code": actual.returncode, "stdout": actual.stdout,
                "stderr": actual.stderr, "native": native}

    def work(self, packet, *, binding=None):
        """The actual fixture SessionStart caller uses its own public hook-work entry."""
        if binding is None:
            binding = self.service.latest_binding(self.session, "primary")
        assert binding["external_session"] == self.session
        environment = {key: value for key, value in self.general.env.items()
                       if key not in {"POISE_CALLER_BINDING", "CODEX_SESSION_ID", "CODEX_THREAD_ID"}}
        environment["CODEX_THREAD_ID"] = self.session
        argv = [self.python, *(["-I"] if self.isolated else []), "-B", "-m", "poise", "hook-work",
                "--settings", str(self.general.settings_path), "--binding", binding["binding_path"]]
        actual = subprocess.run(argv, input=json.dumps(packet).encode(), capture_output=True,
                                cwd=self.project["app"], env=environment)
        self.record({"kind": "actual-fixture-native-work", "argv": argv, "request": packet,
                     "exit_code": actual.returncode, "stdout": actual.stdout.decode(),
                     "stderr": actual.stderr.decode()})
        assert actual.returncode == 0, self.calls[-1]
        value = json.loads(actual.stdout)
        if "response_path" in value:
            saved = Path(value["response_path"])
            value = json.loads(saved.read_text())
            self.record({"kind": "fixture-native-work-full-readback", "path": str(saved), "result": value})
        return value

    def cli(self, packet=None, *, audience="machine", fresh=True):
        request = replace_tokens(literal("query.json"), {SESSION: self.session}) if packet is None else deepcopy(packet)
        environment = dict(self.general.env)
        if fresh:
            # A different real client process and caller, with no Task claim.
            binding = write(self.parent / ("reader-" + str(len(self.calls)) + ".json"),
                            {"schema": "poise-caller-binding-1", "caller_id": "f16-reader-" + str(len(self.calls))})
            environment["POISE_CALLER_BINDING"] = str(binding)
        arguments = ["telemetry", "--config", str(self.general.path),
                     "--checkout", str(self.project["app"]), "--policy", str(self.policy_path),
                     "--audience", audience]
        observer = Path(__file__).parents[1] / "hook_transport/fixtures/architecture/task_observer.py"
        driver = literal("driver-request.json")
        driver.update(argv=arguments, packet=request, observer=str(observer))
        result = subprocess.run([self.python, *(["-I"] if self.isolated else []),
                                 "-B", str(FIXTURES / "cli_driver.py")],
                                input=json.dumps(driver).encode(), capture_output=True,
                                cwd=self.parent, env=environment)
        assert result.returncode == 0 and not result.stderr, (
            "F16_TEST_CLIENT_FAILED", result.returncode, result.stderr.decode())
        value = json.loads(result.stdout)
        self.record({"kind": "cli", "request": request, **value})
        assert value["unexpected"] is None, ("F16_UNEXPECTED_PRODUCT_EXCEPTION", value)
        return value

    def query(self, *, family=None, kind="facts", scope=None, period=None, allow_unavailable=False):
        request = literal("query.json")
        request["scope"]["id"] = self.session
        request["view"] = {"kind": kind, "family": family}
        if scope is not None:
            request["scope"] = scope
        if period is not None:
            request["period"] = period
        result = self.cli(request)
        assert result["reached"] == ["real-poise-module-entry"]
        if result["exit_code"] == 2 and "invalid choice: 'telemetry'" in result["stderr"]:
            raise AssertionError("F16_METRICS_CLI_ENTRY_MISSING: actual argparse rejected telemetry")
        if allow_unavailable and result["exit_code"] == 2 and result["stdout"]:
            unavailable = json.loads(result["stdout"])
            if unavailable.get("status") == "unavailable":
                assert unavailable.get("continuation"), unavailable
                if not self.connected:
                    assert result["sentinel"] == literal("expected-no-task.json"), result
                return None
        assert result["exit_code"] == 0, ("F16_METRICS_QUERY_FAILED", result)
        value = json.loads(result["stdout"])
        assert value["schema"] == "poise-agent-metrics-result-1"
        assert value["status"] == "read_only"
        assert value["scope"] == request["scope"]
        for key in ("from", "to"):
            assert value["period"][key] == request["period"][key]
        if not self.connected:
            assert result["sentinel"] == literal("expected-no-task.json"), result
        return value

    def wait_query(self, *, family=None, kind="facts", scope=None, predicate=None):
        deadline = time.monotonic() + 15
        last = None
        while time.monotonic() < deadline:
            last = self.query(family=family, kind=kind, scope=scope, allow_unavailable=True)
            if last is None:
                time.sleep(.02)
                continue
            if predicate(last) if predicate is not None else bool(last["metrics"]):
                return last
            time.sleep(.02)
        raise AssertionError(("F16_METRICS_COMMIT_NOT_OBSERVED", last, self.calls))

    def wait_measurements(self, family, expected, *, kind="facts", scope=None):
        def arrived(reply):
            for name, value in expected.items():
                rows = [row for row in reply["metrics"] if row["name"] == name]
                if len(rows) != 1:
                    return False
                row = rows[0]
                if ({"value": row["value"], "unit": row["unit"], "kind": row["kind"],
                     "state": row["availability"]["state"]} != value):
                    return False
            return True

        reply = self.wait_query(family=family, kind=kind, scope=scope, predicate=arrived)
        assert_measurements(reply, expected)
        return reply

    def operation(self, name):
        request = literal("operation.json")
        request.update(operation=name)
        request["scope"]["id"] = self.project["cfg"]["project"]
        observed = self.cli(request)
        if observed["exit_code"] == 2 and "invalid choice: 'telemetry'" in observed["stderr"]:
            raise AssertionError("F16_METRICS_CLI_ENTRY_MISSING: actual argparse rejected telemetry")
        assert observed["stdout"], ("F16_METRICS_OPERATION_NO_PUBLIC_RESULT", observed)
        result = json.loads(observed["stdout"])
        assert result["scope"] == request["scope"]
        return observed["exit_code"], result

    def wait_status(self, predicate):
        deadline = time.monotonic() + 15
        last = None
        while time.monotonic() < deadline:
            code, last = self.operation("status")
            if predicate(code, last):
                return code, last
            time.sleep(.02)
        raise AssertionError(("F16_DELIVERY_STATE_NOT_OBSERVED", last))

    def close(self):
        if self.started:
            self.native("SessionEnd")


def assert_measurements(reply, expected):
    """Compare literals; neither production serializer nor algorithm builds expected."""
    actual = {}
    for row in reply["metrics"]:
        if row["name"] in expected:
            assert row["name"] not in actual, ("F16_DUPLICATED_METRIC_FACT", row)
            if type(expected[row["name"]]["value"]) in {int, float}:
                assert type(row["value"]) in {int, float}, ("F16_BOOLEAN_AS_MEASUREMENT", row)
            if type(expected[row["name"]]["value"]) is bool:
                assert type(row["value"]) is bool, ("F16_BOOLEAN_TYPE_LOST", row)
            actual[row["name"]] = {"value": row["value"], "unit": row["unit"],
                                   "kind": row["kind"], "state": row["availability"]["state"]}
            if row["availability"]["state"] in {"unknown", "conflict", "partial", "unavailable", "not_applicable"}:
                assert row["availability"]["reason"], row
    assert actual == expected, "F16_METRIC_VALUES_UNITS_OR_AVAILABILITY"
    assert reply["provenance"], "F16_METRIC_PROVENANCE_MISSING"


def metadata_values(reply, name):
    """Read an exposed field without requiring metadata to become a metric row."""
    objects = []

    def visit(value):
        if isinstance(value, dict):
            objects.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for section in ("metrics", "provenance", "context", "coverage"):
        visit(reply.get(section))
    values = []
    for item in objects:
        if name in item:
            values.append(item[name])
        if item.get("name") == name and "value" in item:
            values.append(item["value"])
    return values


def assert_metadata(reply, expected):
    """Metadata may be context/provenance or a typed row, as the contract permits."""
    for key, value in expected.items():
        assert any(item == value and (type(value) is not bool or type(item) is bool)
                   for item in metadata_values(reply, key)), ("F16_PROVENANCE_FIELD_MISSING_OR_WRONG", key, value)


def context_view(reply, expected):
    """Select the declared exported entity context, never combine alternative rows."""
    field = literal("public-record-context.json")["metric_row_context_field"]
    rows = [row for row in reply["metrics"] if isinstance(row.get(field), dict)
            and all(row[field].get(key) == value for key, value in expected.items())]
    assert rows, ("F16_EXPORTED_RECORD_CONTEXT_MISSING", expected)
    return {**reply, "metrics": rows}


def assert_unavailable_metadata(reply, field, *, state, source):
    """An absent input attribute needs its own scoped reason, never a guessed value."""
    field_name = literal("public-record-context.json")["metadata_availability_field"]
    declarations = [item[field] for item in metadata_values(reply, field_name)
                    if isinstance(item, dict) and field in item]
    declarations += [row["availability"] for row in reply["metrics"]
                     if row["name"] == field and row["value"] is None]
    assert declarations, ("F16_MISSING_ATTRIBUTE_HAS_NO_EXPLICIT_AVAILABILITY", field)
    assert any(item.get("state") == state and item.get("reason") and item.get("source_id") == source
               for item in declarations), ("F16_ATTRIBUTE_AVAILABILITY_NOT_SOURCE_BOUND", field)
    values = [item for item in metadata_values(reply, field)
              if not any(item is declaration for declaration in declarations)]
    assert all(item is None for item in values), ("F16_INVENTED_MISSING_ATTRIBUTE", field)


def committed_observations(path):
    """Observe committed logical rows; SQLite WAL read marks are not new metrics."""
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        return db.execute("SELECT id,project,session_id,started_at,ended_at,data "
                          "FROM accounting_cycles ORDER BY id").fetchall()


class ProcessorGate:
    def __init__(self, parent):
        self.root = parent / "processor-gate"
        self.configuration = replace_tokens(literal("processor-gate.json"), {"@GATE@": self.root})

    def wait_entered(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.root.exists():
                files = sorted(self.root.glob("*.entered.json"))
                if files:
                    values = [json.loads(path.read_text()) for path in files]
                    assert all(value["pid"] > 0 for value in values)
                    return values
            time.sleep(.01)
        raise AssertionError("F16_REAL_PROCESSOR_BOUNDARY_NOT_REACHED")

    def release(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "release").write_text("release\n")

    def wait_terminal(self):
        import psutil
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            live = []
            for path in self.root.glob("*.entered.json"):
                pid = json.loads(path.read_text())["pid"]
                try:
                    process = psutil.Process(pid)
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        live.append(pid)
                except psutil.NoSuchProcess:
                    pass
            if not live:
                return
            time.sleep(.02)
        raise AssertionError(("F16_OWN_PROCESSORS_NOT_TERMINAL", live))
