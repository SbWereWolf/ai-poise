#!/usr/bin/env python3
"""T027 external Bootstrap contract tests; no imported application oracle."""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps/bootstrap"
PROVIDER = APP / "bin/bootstrap"
FIXTURES = APP / "fixtures/maintenance"
DOC = APP / "docs/bst-maintenance.md"
OBSERVATIONS = []
CURRENT = "setup"
SECRET = "T027-synthetic-private-token"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def slots(value, mapping):
    """Substitute exact fixture slots, not production parameter expressions."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [slots(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: slots(v, mapping) for k, v in value.items()}
    return value


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate protocol key")
        result[key] = value
    return result


class Case:
    def __init__(self, base):
        self.base = base
        self.config = base / "config/bootstrap.json"
        self.projects = self.config.parent / "projects"
        self.projects.mkdir(parents=True)
        self.records = {}
        write(self.config, fixture("bootstrap-config.json"))

    def configure(self, **limits):
        data = json.loads(self.config.read_text())
        data["limits"].update(limits)
        write(self.config, data)

    def profile(self, name="alpha", *, ready=True, catalogue=None, settings=None):
        assets = self.projects / (name + "-assets")
        assets.mkdir(exist_ok=True)
        probe = assets / "probe.py"
        shutil.copyfile(FIXTURES / "probe.py", probe)
        state, audit = self.base / "business" / name, self.base / "audit" / (name + ".jsonl")
        state.parent.mkdir(exist_ok=True)
        if ready:
            state.write_text("ready", encoding="utf-8")
        mapping = {"@PYTHON@": sys.executable, "@PROBE@": str(probe),
                   "@SETTINGS@": str(assets / "probe.json"), "@STATE@": str(state), "@AUDIT@": str(audit)}
        config = slots(fixture("probe-settings.json"), mapping)
        if settings:
            config.update(settings)
        write(assets / "probe.json", config)
        catalog = slots(fixture("catalogue.json") if catalogue is None else catalogue, mapping)
        write(assets / "requirements.json", catalog)
        record = fixture("project.json")
        record.update(id=name, catalog=name + "-assets/requirements.json")
        write(self.projects / (name + ".json"), record)
        result = {"assets": assets, "state": state, "audit": audit, "catalogue": catalog, "project": record,
                  "settings": config, "mapping": mapping, "name": name}
        self.records[name] = result
        return result

    def save(self, item):
        write(item["assets"] / "requirements.json", item["catalogue"])
        write(item["assets"] / "probe.json", item["settings"])
        write(self.projects / (item["name"] + ".json"), item["project"])

    def audit(self, name="alpha"):
        path = self.records[name]["audit"]
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def modes(self, name="alpha"):
        return [event["mode"] for event in self.audit(name)]

    def run(self, *args, env=None, flags=()):
        environment = os.environ.copy()
        for key in ("T027_TARGET", "T027_VALUE", "T027_INHERITED", "T027_NOT_INHERITED", "T027_EXPLICIT"):
            environment.pop(key, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = ""
        if env:
            environment.update(env)
        proc = subprocess.run([sys.executable, *flags, "-B", str(PROVIDER), "--config", str(self.config), *args],
                              cwd=self.base, env=environment, capture_output=True, timeout=20, check=False)
        OBSERVATIONS.append({"group": CURRENT, "arguments": list(args), "python_flags": list(flags),
                             "exit_code": proc.returncode, "stdout_hex": proc.stdout.hex(), "stderr_hex": proc.stderr.hex(),
                             "audits": {name: self.audit(name) for name in self.records},
                             "business": {name: item["state"].read_text() if item["state"].exists() else None
                                          for name, item in self.records.items()}})
        if proc.stderr or not proc.stdout.endswith(b"\n") or proc.stdout.count(b"\n") != 1:
            raise RuntimeError("Not one terminal UTF-8 JSON line with empty stderr")
        try:
            body = json.loads(proc.stdout.decode("utf-8"), object_pairs_hook=unique_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Invalid protocol output, not semantic feature RED") from exc
        if not isinstance(body, dict) or body.get("schema") != "stride-bootstrap/v1" or body.get("exit_code") != proc.returncode:
            raise RuntimeError("Unrecognized or inconsistent protocol")
        return body, proc


@contextmanager
def case():
    with tempfile.TemporaryDirectory(prefix="t027-maintenance-") as directory:
        current = Case(Path(directory))
        try:
            yield current
        finally:
            # Clean only a demonstrably test-owned leaked probe after a failed timeout guard.
            for name in current.records:
                for event in current.audit(name):
                    pid = event.get("child")
                    if not isinstance(pid, int):
                        continue
                    try:
                        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
                        if str(current.base).encode() in cmdline and b"probe.py" in cmdline:
                            os.kill(pid, signal.SIGKILL)
                    except (FileNotFoundError, ProcessLookupError):
                        pass


def normal(body, names):
    assert set(body) == {"schema", "command", "dry_run", "selection", "status", "exit_code", "targets"}
    assert body["selection"]["resolved"] == names
    assert [row["target"] for row in body["targets"]] == names
    for row in body["targets"]:
        assert set(row) == {"target", "config_digest", "status", "exit_code", "effects", "core", "error"}
    return body["targets"]


def single(body, status, effects="none", error=None):
    row = normal(body, ["alpha"])[0]
    expected_exit = {"ready": 0, "not_required": 0, "repaired": 0, "action_required": 3, "failed": 4}[status]
    assert body["status"] == row["status"] == status and body["exit_code"] == row["exit_code"] == expected_exit
    assert row["effects"] == effects
    if error is None:
        assert row["error"] is None
    else:
        assert row["error"]["code"] == error and row["error"]["retryable"] is False
    return row


def owner_report(row):
    core = row["core"]
    assert isinstance(core, dict), "full core report is missing"
    assert core["schema"] == "environment-maintenance/result/v1"
    assert core["status"] == row["status"] and core["exit_code"] == row["exit_code"]
    return core


def normalize_durations(value):
    if isinstance(value, list):
        return [normalize_durations(v) for v in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "duration_seconds":
                assert type(item) in (int, float) and math.isfinite(item) and item >= 0
                out[key] = 0
            else:
                out[key] = normalize_durations(item)
        return out
    return value


def maintenance_check():
    with case() as c:
        c.profile()
        body, _ = c.run("check", "--target", "alpha")
        code = body.get("targets", [{}])[0].get("error") or {}
        assert body["exit_code"] == 0 and body["status"] == "ready", (
            f"maintenance.check expected ready; observed exit={body['exit_code']},error={code.get('code')}")
        row = single(body, "ready")
        assert body["selection"] == {"requested": "alpha", "source": "argument", "resolved": ["alpha"]}
        assert c.modes() == ["check"], "ready check was not actually invoked exactly once"
        assert normalize_durations(owner_report(row)) == fixture("check-core.json"), "full owner report drift"


def kinds():
    for command, expected in (("infra", ["infra"]), ("deps", ["deps"]), ("check", ["infra", "deps"])):
        with case() as c:
            item = c.profile()
            second = copy.deepcopy(item["catalogue"]["requirements"][0])
            second.update(id="d", kind="deps")
            item["catalogue"]["requirements"].append(second)
            c.save(item)
            body, _ = c.run(command)
            core = owner_report(single(body, "ready"))
            assert [r["kind"] for r in core["requirements"]] == expected
            assert c.modes() == ["check"] * len(expected)


def readonly():
    for vector in fixture("cases.json")["readonly"]:
        with case() as c:
            item = c.profile(ready=False)
            item["catalogue"]["requirements"][0]["kind"] = "deps" if vector["command"] == "deps" else "infra"
            c.save(item)
            sentinel = c.base / "business/foreign.sqlite"
            sentinel.write_bytes(b"unchanged foreign bytes\0")
            before = {str(p): p.read_bytes() for p in c.projects.rglob("*") if p.is_file()}
            options = ["--dry-run"] if vector["dry_run"] else []
            body, _ = c.run(vector["command"], *options)
            core = owner_report(single(body, "action_required"))
            assert c.modes() == ["check"] and not item["state"].exists()
            assert sentinel.read_bytes() == b"unchanged foreign bytes\0"
            assert {str(p): p.read_bytes() for p in c.projects.rglob("*") if p.is_file()} == before
            assert ("planned_apply" in core["requirements"][0]) is vector["dry_run"]
            assert "apply" not in core["requirements"][0]


def repair():
    with case() as c:
        item = c.profile(ready=False)
        body, _ = c.run("infra")
        core = owner_report(single(body, "repaired", "applied"))
        assert c.modes() == fixture("cases.json")["sequence"] and item["state"].read_text() == "ready"
        assert core["requirements"][0]["final_check"]["status"] == "passed"


def truth_table():
    for vector in fixture("cases.json")["truth_table"]:
        with case() as c:
            c.profile(ready=False, settings={"apply_exit": vector["apply_exit"], "materialize": vector["materialize"]})
            body, _ = c.run("infra")
            row = single(body, vector["status"], "applied", None if vector["status"] == "repaired" else "maintenance_failed")
            core = owner_report(row)
            assert core["exit_code"] == vector["exit_code"]
            assert c.modes() == fixture("cases.json")["sequence"], "apply failure must still be followed by recheck"
            assert core["requirements"][0]["apply"]["exit_code"] == vector["apply_exit"]
            assert core["requirements"][0]["final_check"]["exit_code"] == (0 if vector["materialize"] else 1)

    interrupted_applies()


def recommendation():
    with case() as c:
        c.profile(ready=False)
        body, _ = c.run("check")
        requirement = owner_report(single(body, "action_required"))["requirements"][0]
        assert requirement["recommendation"] == "Operator repair required" and c.modes() == ["check"]


def partial_all():
    with case() as c:
        c.profile("alpha")
        (c.projects / "alpha.json").write_bytes(b"{broken")
        c.profile("beta", ready=False)
        c.profile("extra")
        body, _ = c.run("infra")
        rows = normal(body, ["alpha", "beta", "extra"])
        assert [r["status"] for r in rows] == ["failed", "repaired", "ready"]
        assert [r["effects"] for r in rows] == ["none", "applied", "none"]
        assert c.modes("alpha") == [] and c.modes("beta") == fixture("cases.json")["sequence"] and c.modes("extra") == ["check"]
        assert body["status"] == "failed" and body["exit_code"] == 4


def empty():
    with case() as c:
        body, _ = c.run("check")
        assert normal(body, []) == [] and body["status"] == "not_required" and body["exit_code"] == 0
        catalog = fixture("catalogue.json")
        catalog["requirements"] = []
        c.profile(catalogue=catalog)
        body, _ = c.run("deps")
        assert owner_report(single(body, "not_required"))["requirements"] == [] and c.modes() == []


def parameters():
    with case() as c:
        item = c.profile()
        item["catalogue"]["parameters"] = fixture("parameters.json")
        item["settings"]["echo"] = True
        item["catalogue"]["requirements"][0]["check"]["argv"] += ["${value}", "${number}", "${flag}"]
        c.save(item)
        body, _ = c.run("check")
        core = owner_report(single(body, "ready"))
        assert core["parameters"] == {"value": "default", "number": 1, "flag": False}
        item["project"]["values"] = {"value": "record"}
        item["project"]["parameter_values_files"] = ["alpha-assets/first.json", "alpha-assets/second.json"]
        write(item["assets"] / "first.json", {"value": "first"})
        write(item["assets"] / "second.json", {"value": "second"})
        c.save(item)
        body, _ = c.run("check")
        core = owner_report(single(body, "ready"))
        assert core["parameters"]["value"] == "second" and core["parameter_sources"]["value"] == "values"
        body, _ = c.run("check", env={"T027_VALUE": "environment"})
        core = owner_report(single(body, "ready"))
        assert core["parameters"]["value"] == "environment" and core["parameter_sources"]["value"] == "environment"
        body, _ = c.run("check", "--set", "value=first-cli", "--set", "value=last-cli", "--set", "number=7", "--set", "flag=true", env={"T027_VALUE": "environment"})
        core = owner_report(single(body, "ready"))
        assert core["parameters"] == {"value": "last-cli", "number": 7, "flag": True}
        assert core["parameter_sources"] == {"value": "cli", "number": "cli", "flag": "cli"}
        assert core["requirements"][0]["initial_check"]["stdout"] == "last-cli|7|true\n"


def invalid_parameters():
    for args in (("--set", "unknown=x"), ("--set", "number=true"), ("--set", "number=not-json")):
        with case() as c:
            item = c.profile()
            item["catalogue"]["parameters"] = fixture("parameters.json")
            c.save(item)
            body, _ = c.run("infra", *args)
            single(body, "failed", error="invalid_target_parameters")
            assert c.modes() == []
    with case() as c:
        item = c.profile()
        item["catalogue"]["parameters"] = fixture("parameters.json")
        c.save(item)
        c.profile("beta")
        body, _ = c.run("check", "--set", "value=declared-for-alpha")
        rows = normal(body, ["alpha", "beta"])
        assert rows[0]["status"] == "ready" and rows[1]["error"]["code"] == "invalid_target_parameters"
        assert c.modes("alpha") == ["check"] and c.modes("beta") == [] and body["exit_code"] == 4


def expansion():
    with case() as c:
        item = c.profile()
        item["catalogue"]["parameters"] = fixture("parameters.json")
        item["project"]["values"] = {"value": "${not-a-parameter}"}
        item["settings"]["echo"] = True
        item["catalogue"]["requirements"][0]["check"]["argv"].append("${value}")
        c.save(item)
        body, _ = c.run("check")
        assert owner_report(single(body, "ready"))["requirements"][0]["initial_check"]["stdout"] == "${not-a-parameter}\n"


def secrets():
    with case() as c:
        item = c.profile()
        item["catalogue"]["parameters"] = {"token": {"type": "string", "secret": True}}
        item["project"]["values"] = {"token": SECRET}
        item["settings"]["echo"] = True
        item["catalogue"]["requirements"][0]["check"]["argv"].append("${token}")
        c.save(item)
        body, proc = c.run("check")
        row = single(body, "ready")
        core = owner_report(row)
        assert row["config_digest"] is None and core["parameters"]["token"] == "[REDACTED]"
        assert core["requirements"][0]["initial_check"]["stdout"] == "[REDACTED]\n"
        assert SECRET.encode() not in proc.stdout + proc.stderr
        body, proc = c.run("check", "--set", SECRET + "=bad")
        single(body, "failed", error="invalid_target_parameters")
        assert SECRET.encode() not in proc.stdout + proc.stderr


def validation():
    with case() as c:
        item = c.profile(ready=False)
        invalid = copy.deepcopy(item["catalogue"]["requirements"][0])
        invalid["id"] = "later"
        invalid["apply"]["timeout"] = 0
        item["catalogue"]["requirements"].append(invalid)
        c.save(item)
        body, _ = c.run("infra")
        single(body, "failed", error="invalid_target_catalogue")
        assert c.modes() == [], "earlier valid requirement ran before complete validation"


def route(item):
    action = slots(fixture("catalogue.json"), item["mapping"])["requirements"][0]["check"]
    action["argv"][-1] = "repair"
    item["project"]["repairs"] = item["name"] + "-assets/repairs.json"
    for key in ("check", "apply", "recommendation"):
        item["catalogue"]["requirements"][0][key] = {"repair": "r"}
    return {"schema": "environment-maintenance/repairs/v1", "repairs": {"r": action}}


def missing_route():
    with case() as c:
        item = c.profile(ready=False)
        repairs = route(item)
        repairs["repairs"] = {}
        write(item["assets"] / "repairs.json", repairs)
        c.save(item)
        body, _ = c.run("infra")
        single(body, "failed", error="invalid_target_catalogue")
        assert c.modes() == []


def repair_router():
    with case() as c:
        item = c.profile(ready=False)
        write(item["assets"] / "repairs.json", route(item))
        c.save(item)
        body, _ = c.run("check")
        core = owner_report(single(body, "action_required"))
        assert c.modes() == ["check", "recommend"]
        assert core["requirements"][0]["recommendation"] == "Operator repair required"
        item["audit"].unlink()
        body, _ = c.run("infra")
        owner_report(single(body, "repaired", "applied"))
        assert c.modes() == fixture("cases.json")["sequence"]
        assert [event["request"]["mode"] for event in c.audit()] == fixture("cases.json")["sequence"]


def repair_cwd():
    for raw, value in ((None, None), ("relative", None), ("@ABSOLUTE@", None),
                       ("${where}", "relative"), ("${where}", "literal-${undeclared}")):
        with case() as c:
            item = c.profile()
            repairs = route(item)
            origin = item["assets"] / "repair-${literal}"
            origin.mkdir()
            item["project"]["repairs"] = "alpha-assets/repair-${literal}/repairs.json"
            absolute = c.base / "absolute"
            actual_raw = str(absolute) if raw == "@ABSOLUTE@" else raw
            if value is not None:
                item["catalogue"]["parameters"] = {"where": {"type": "string", "secret": False}}
                item["project"]["values"] = {"where": value}
            if actual_raw is not None:
                repairs["repairs"]["r"]["cwd"] = actual_raw
            expected = absolute if raw == "@ABSOLUTE@" else origin / (value if value is not None else raw or "")
            expected.mkdir(parents=True, exist_ok=True)
            write(origin / "repairs.json", repairs)
            c.save(item)
            body, _ = c.run("check")
            owner_report(single(body, "ready"))
            assert c.modes() == ["check"]
            assert c.audit()[0]["cwd"] == str(expected), "repair origin or one-pass cwd changed"


def catalogue_cwd():
    for raw in (None, "relative", "@ABSOLUTE@"):
        with case() as c:
            item = c.profile()
            origin = item["assets"] / "catalogue-${literal}"
            origin.mkdir()
            actual = c.base / "explicit" if raw == "@ABSOLUTE@" else origin / (raw or "")
            actual.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES / "probe.py", actual / "probe.py")
            write(actual / "probe.json", item["settings"])
            action = item["catalogue"]["requirements"][0]["check"]
            action["argv"] = [sys.executable, "probe.py", "probe.json", "check"]
            if raw is not None:
                action["cwd"] = str(actual) if raw == "@ABSOLUTE@" else raw
            item["project"]["catalog"] = "alpha-assets/catalogue-${literal}/requirements.json"
            c.save(item)
            write(origin / "requirements.json", item["catalogue"])
            body, _ = c.run("check")
            owner_report(single(body, "ready"))
            assert c.audit()[0]["cwd"] == str(actual), "frozen private file replaced catalogue cwd"


def frozen_snapshot():
    with case() as c:
        first = c.profile(ready=False)
        second = c.profile("beta")
        empty = fixture("catalogue.json")
        empty["requirements"] = []
        gamma = fixture("project.json")
        gamma.update(id="gamma", catalog="gamma-assets/requirements.json")
        first["settings"]["mutations"] = [
            {"path": str(second["assets"] / "requirements.json"), "value": empty},
            {"path": str(c.projects / "gamma-assets/requirements.json"), "value": empty},
            {"path": str(c.projects / "gamma.json"), "value": gamma}]
        c.save(first)
        body, _ = c.run("infra", "--target", "all")
        rows = normal(body, ["alpha", "beta"])
        assert [r["status"] for r in rows] == ["repaired", "ready"]
        assert c.modes("beta") == ["check"], "later target reread mutable catalogue"
        assert body["status"] == "repaired" and body["exit_code"] == 0
        body, _ = c.run("check", "--target", "all")
        rows = normal(body, ["alpha", "beta", "gamma"])
        assert rows[1]["status"] == rows[2]["status"] == "not_required"
        assert len(c.audit("beta")) == 1, "second invocation ignored newly changed input"


def action_timeout():
    for clipping in (False, True):
        with case() as c:
            item = c.profile()
            action = item["catalogue"]["requirements"][0]["check"]
            if clipping:
                item["settings"]["stdout_bytes"] = {"check": 32}
                action["max_output_bytes"] = 8
            else:
                item["settings"]["sleep"] = {"check": 2}
                action["timeout"] = 0.15
            c.save(item)
            body, _ = c.run("check")
            result = owner_report(single(body, "action_required"))["requirements"][0]["initial_check"]
            assert c.modes() == ["check"]
            if clipping:
                assert result["capture_complete"] is False and result["stdout"] == "X" * 8
                assert result["timed_out"] is False
            else:
                assert result["timed_out"] is True and result["status"] == "failed"


def target_timeout_readonly():
    for command, options in (("check", []), ("infra", ["--dry-run"])):
        with case() as c:
            item = c.profile(ready=False, settings={"sleep": {"check": 30}})
            item["catalogue"]["requirements"][0]["check"]["timeout"] = 30
            c.save(item)
            c.configure(target_timeout_seconds=0.7, run_timeout_seconds=5)
            body, _ = c.run(command, *options)
            assert single(body, "failed", error="target_timeout")["core"] is None
            assert c.modes() == ["check"] and not item["state"].exists()


def target_timeout_mutating():
    with case() as c:
        item = c.profile(ready=False, settings={"sleep": {"apply": 30}})
        item["catalogue"]["requirements"][0]["apply"]["timeout"] = 30
        c.save(item)
        c.configure(target_timeout_seconds=0.8, run_timeout_seconds=5)
        body, _ = c.run("infra")
        assert single(body, "failed", "unknown", "target_timeout")["core"] is None
        assert c.modes() == ["check", "apply"] and item["state"].exists()


def run_deadline():
    with case() as c:
        item = c.profile(settings={"sleep": {"check": 30}})
        item["catalogue"]["requirements"][0]["check"]["timeout"] = 30
        c.save(item)
        c.profile("beta")
        c.profile("extra")
        c.configure(target_timeout_seconds=5, run_timeout_seconds=0.7)
        body, _ = c.run("check", "--target", "all")
        rows = normal(body, ["alpha", "beta", "extra"])
        assert [r["status"] for r in rows] == ["failed", "not_started", "not_started"]
        assert rows[0]["error"]["code"] == "target_timeout"
        for row in rows[1:]:
            assert row["exit_code"] == 4 and row["effects"] == "none" and row["core"] is None
            assert row["error"]["code"] == "run_deadline_exceeded"
        assert body["status"] == "failed" and body["exit_code"] == 4
        assert c.modes() == ["check"] and not c.audit("beta") and not c.audit("extra")


def descendants():
    with case() as c:
        item = c.profile(ready=False, settings={"spawn": True, "sleep": {"check": 30}})
        item["catalogue"]["requirements"][0]["check"]["timeout"] = 30
        c.save(item)
        c.configure(target_timeout_seconds=0.8, run_timeout_seconds=5)
        body, _ = c.run("check")
        single(body, "failed", error="target_timeout")
        events = c.audit()
        assert len(events) == 1 and isinstance(events[0].get("child"), int), "no actual descendant was exercised"
        time.sleep(2.1)
        assert not item["state"].exists(), "owned new-session descendant survived target timeout"

    normal_child_lifetime()


def output_preflight():
    for limit in (1, 100):
        with case() as c:
            item = c.profile(ready=False)
            c.configure(max_output_bytes=limit)
            body, _ = c.run("infra")
            assert body["status"] == "rejected" and body["exit_code"] == 2
            assert body["error"]["code"] == "limit_exceeded" and body["targets"] == []
            assert c.modes() == [] and not item["state"].exists(), "effect before output capacity preflight"


def output_readonly():
    for command, options in (("check", []), ("infra", ["--dry-run"])):
        with case() as c:
            item = c.profile(settings={"stdout_bytes": {"check": 8000}})
            item["catalogue"]["requirements"][0]["check"]["max_output_bytes"] = 10000
            c.save(item)
            c.configure(max_output_bytes=2048)
            body, proc = c.run(command, *options)
            assert single(body, "failed", error="output_limit_exceeded")["core"] is None
            assert len(proc.stdout) <= 2048 and c.modes() == ["check"]


def output_mutating():
    with case() as c:
        item = c.profile(ready=False, settings={"stdout_bytes": {"apply": 8000}})
        item["catalogue"]["requirements"][0]["apply"]["max_output_bytes"] = 10000
        c.save(item)
        c.configure(max_output_bytes=2048)
        body, proc = c.run("infra")
        assert single(body, "failed", "unknown", "output_limit_exceeded")["core"] is None
        assert len(proc.stdout) <= 2048 and item["state"].exists()
        assert c.modes().count("apply") == 1, "overflow retried mutation"


def absent_core():
    with case() as c:
        c.profile()
        body, _ = c.run("check", flags=("-S",))
        assert single(body, "action_required", error="maintenance_unavailable")["core"] is None
        assert not c.audit(), "worker silently switched to another installed runtime"


def global_errors():
    for arguments, expected in ((["check", "--target", "absent"], "target_not_found"),
                                (["check", "--set", "broken"], "invalid_request"),
                                (["infra", "--extra"], "invalid_request")):
        with case() as c:
            c.profile()
            body, _ = c.run(*arguments)
            assert body["status"] == "rejected" and body["exit_code"] == 2 and body["targets"] == []
            assert body["error"]["code"] == expected and not c.audit()
    with case() as c:
        c.profile()
        c.configure(target_timeout_seconds=0)
        body, _ = c.run("infra")
        assert body["status"] == "rejected" and body["exit_code"] == 2 and not c.audit()


def aggregate():
    with case() as c:
        c.profile()
        beta = c.profile("beta", ready=False)
        beta["catalogue"]["requirements"][0]["kind"] = "deps"
        c.save(beta)
        body, _ = c.run("infra")
        rows = normal(body, ["alpha", "beta"])
        assert [r["status"] for r in rows] == ["ready", "not_required"] and body["status"] == "ready"
        assert body["exit_code"] == 0 and not c.audit("beta")
        body, _ = c.run("check")
        rows = normal(body, ["alpha", "beta"])
        assert [r["status"] for r in rows] == ["ready", "action_required"]
        assert body["status"] == "action_required" and body["exit_code"] == 3
        write(c.projects / "alpha.json", {"broken": True})
        body, _ = c.run("check")
        rows = normal(body, ["alpha", "beta"])
        assert [r["status"] for r in rows] == ["failed", "action_required"]
        assert body["status"] == "failed" and body["exit_code"] == 4


def environment():
    with case() as c:
        item = c.profile()
        item["catalogue"]["inherit_env"] = ["T027_INHERITED"]
        item["catalogue"]["requirements"][0]["check"]["env"] = {"T027_EXPLICIT": "action"}
        c.save(item)
        body, _ = c.run("check", env={"T027_INHERITED": "declared", "T027_NOT_INHERITED": "hidden",
                                      "T027_EXPLICIT": "host"})
        owner_report(single(body, "ready"))
        assert c.audit()[0]["environment"] == fixture("cases.json")["environment"]


def utf8():
    with case() as c:
        item = c.profile(settings={"echo": True})
        item["catalogue"]["requirements"][0]["check"]["argv"].append("Проверка")
        c.save(item)
        expected = None
        for encoding in ("ascii", "latin-1", "utf-8"):
            body, proc = c.run("check", env={"PYTHONIOENCODING": encoding, "PYTHONUTF8": "1",
                                           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
            core = owner_report(single(body, "ready"))
            assert core["requirements"][0]["initial_check"]["stdout"] == "Проверка\n"
            assert "Проверка".encode("utf-8") in proc.stdout
            normalized = normalize_durations(body)
            if expected is None:
                expected = normalized
            assert normalized == expected


def owner_boundary():
    calls = []
    for path in APP.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith(("poise", "apps.")) for a in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("poise", "apps."))
                if node.module == "environment_maintenance.engine":
                    aliases.update(a.asname or a.name for a in node.names if a.name == "execute")
        calls.extend((path.name, node.lineno) for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in aliases)
    assert len(calls) == 1, "missing single public core owner, or duplicated execute loop"
    assert (APP / "requirements.txt").read_text().strip() == "environment-maintenance==0.1.1"
    assert not (APP / "environment_maintenance").exists(), "copied standalone core instead of dependency"


def interrupted_applies():
    # Additional cases under the permanent truth-table group, not a replacement
    # for any of the four completed-apply success/failure combinations.
    for mechanism in ("timeout", "signal"):
        with case() as c:
            item = c.profile(ready=False, settings={"sleep": {"apply": 2}})
            action = item["catalogue"]["requirements"][0]["apply"]
            if mechanism == "timeout":
                action["timeout"] = 0.15
            else:
                action["argv"] = [sys.executable, "-c", "import os,signal;os.kill(os.getpid(),signal.SIGTERM)"]
            c.save(item)
            body, _ = c.run("infra")
            core = owner_report(single(body, "failed", "unknown", "maintenance_failed"))
            applied = core["requirements"][0]["apply"]
            assert applied["exit_code"] < 0 and applied["timed_out"] is (mechanism == "timeout")
            assert core["requirements"][0]["final_check"] is not None


def normal_child_lifetime():
    # A trusted successful apply may create finite external work. Normal return
    # must preserve the existing core's behavior, not introduce hidden cleanup.
    with case() as c:
        item = c.profile(ready=False)
        child_script = item["assets"] / "lifetime.py"
        shutil.copyfile(FIXTURES / "lifetime.py", child_script)
        item["catalogue"]["requirements"][0]["apply"]["argv"] = [
            sys.executable, str(child_script), str(item["state"]), str(item["audit"]), "apply"]
        c.save(item)
        body, _ = c.run("infra")
        owner_report(single(body, "repaired", "applied"))
        assert c.modes() == ["check", "apply", "check"]
        assert isinstance(c.audit()[1]["child"], int)
        until = time.monotonic() + 2
        while time.monotonic() < until and item["state"].read_text() != "late effect":
            time.sleep(0.02)
        assert item["state"].read_text() == "late effect", "normal completion cancelled authorized child work"


def documentation(group):
    text = DOC.read_text(encoding="utf-8")
    assert any("а" <= char.lower() <= "я" for char in text), "human documentation is not Russian"
    for token in fixture("cases.json")["documentation"][group]:
        assert token in text, f"missing maintenance documentation contract: {token}"


def documentation_links():
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/check_documentation.py"),
                             "--checkout", str(ROOT), "--changed", "apps/bootstrap/docs/bst-maintenance.md"],
                            cwd=ROOT, capture_output=True, timeout=20)
    assert result.returncode == 0 and not result.stderr, "documentation link validation failed"
    assert json.loads(result.stdout)["errors"] == []


BEHAVIOR = [
    ("maintenance.check", maintenance_check), ("maintenance.kinds", kinds),
    ("maintenance.readonly", readonly), ("maintenance.repair", repair),
    ("maintenance.truth_table", truth_table), ("maintenance.recommendation", recommendation),
    ("maintenance.partial_all", partial_all), ("maintenance.empty", empty),
    ("maintenance.parameters", parameters), ("maintenance.invalid_parameters", invalid_parameters),
    ("maintenance.expansion", expansion), ("maintenance.secrets", secrets),
    ("maintenance.validation", validation), ("maintenance.missing_route", missing_route),
    ("maintenance.repair_router", repair_router), ("maintenance.repair_cwd", repair_cwd),
    ("maintenance.catalogue_cwd", catalogue_cwd), ("maintenance.frozen_snapshot", frozen_snapshot),
    ("maintenance.action_timeout", action_timeout), ("maintenance.target_timeout_readonly", target_timeout_readonly),
    ("maintenance.target_timeout_mutating", target_timeout_mutating), ("maintenance.run_deadline", run_deadline),
    ("maintenance.descendants", descendants), ("maintenance.output_preflight", output_preflight),
    ("maintenance.output_readonly", output_readonly), ("maintenance.output_mutating", output_mutating),
    ("maintenance.absent_core", absent_core), ("maintenance.global_errors", global_errors),
    ("maintenance.aggregate", aggregate), ("maintenance.environment", environment),
    ("maintenance.utf8", utf8), ("maintenance.owner_boundary", owner_boundary)]
DOCS = [("docs.command", lambda: documentation("command")), ("docs.owner", lambda: documentation("owner")),
        ("docs.result", lambda: documentation("result")), ("docs.links", documentation_links)]


def main():
    global CURRENT
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("red", "behavior", "documentation"), required=True)
    args = parser.parse_args()
    errors, failures = [], []
    checks = BEHAVIOR[:1] if args.suite == "red" else BEHAVIOR if args.suite == "behavior" else DOCS
    assert len(BEHAVIOR) == 32 and len(DOCS) == 4
    preflight = None
    if args.suite != "documentation":
        try:
            probe = subprocess.run([sys.executable, "-I", "-B", "-c",
                "from importlib.metadata import version; from environment_maintenance.engine import execute; "
                "assert callable(execute); print(version('environment-maintenance'))"], capture_output=True, timeout=10)
            preflight = {"exit_code": probe.returncode, "stdout_hex": probe.stdout.hex(), "stderr_hex": probe.stderr.hex()}
            if probe.returncode or probe.stdout != b"0.1.1\n" or probe.stderr:
                raise RuntimeError("selected runtime must provide environment-maintenance 0.1.1")
        except Exception as exc:
            errors.append({"reason": str(exc), "test": "runtime.preflight"})
            checks = []
    for CURRENT, check in checks:
        try:
            check()
        except AssertionError as exc:
            failures.append({"reason": str(exc) or "assertion failed", "test": CURRENT})
        except Exception as exc:
            errors.append({"reason": type(exc).__name__ + ": " + str(exc), "test": CURRENT})
    if args.suite != "documentation" and os.environ.get("POISE_RUN_OUTPUT_DIR"):
        path = Path(os.environ["POISE_RUN_OUTPUT_DIR"]) / "maintenance-observations.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"schema": "bootstrap-maintenance-test-observations/v1", "suite": args.suite,
                       "runtime_preflight": preflight, "observations": OBSERVATIONS}, stream,
                      ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    summary = {"errors": errors, "failures": failures, "tests_run": len(checks)}
    sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    return int(bool(errors or failures))


if __name__ == "__main__":
    raise SystemExit(main())
