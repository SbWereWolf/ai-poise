#!/usr/bin/env python3
"""T028 external contract tests with an independent disposable database owner."""
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
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps/bootstrap"
PROVIDER = APP / "bin/bootstrap"
FIXTURES = APP / "fixtures/owner_commands"
DOC = APP / "docs/bst-db-delegation.md"
CURRENT = "setup"
OBSERVATIONS = []
SECRET = "T028-private-synthetic-secret"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def slots(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [slots(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: slots(v, mapping) for k, v in value.items()}
    return value


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate protocol key")
        result[key] = value
    return result


class Case:
    def __init__(self, base):
        self.base = base
        self.config = base / "config/bootstrap.json"
        self.projects = self.config.parent / "projects"
        self.projects.mkdir(parents=True)
        self.records = {}
        write(self.config, fixture("bootstrap.json"))

    def profile(self, name="alpha", ready=False):
        assets = self.projects / (name + "-assets")
        binding_dir = assets / "bindings"
        binding_dir.mkdir(parents=True)
        owner = assets / "owner.py"
        shutil.copyfile(FIXTURES / "owner.py", owner)
        db = self.base / "business" / (name + ".sqlite")
        audit = self.base / "audit" / (name + ".jsonl")
        config = assets / "owner-settings.json"
        mapping = {"@PYTHON@": sys.executable, "@OWNER@": str(owner), "@SETTINGS@": str(config),
                   "@DATABASE@": str(db), "@AUDIT@": str(audit)}
        project = fixture("project.json")
        project.update(id=name, catalog=name + "-assets/catalogue.json", repairs=name + "-assets/bindings/commands.json")
        item = {"name": name, "assets": assets, "binding_dir": binding_dir, "database": db,
                "audit": audit, "owner": owner, "config": config, "project": project,
                "catalogue": fixture("catalogue.json"), "bindings": slots(fixture("bindings.json"), mapping),
                "settings": slots(fixture("settings.json"), mapping)}
        self.records[name] = item
        self.save(item)
        if ready:
            seeded = subprocess.run([sys.executable, "-B", str(owner), str(config), "seed"], capture_output=True, timeout=10)
            if seeded.returncode or seeded.stderr:
                raise RuntimeError("fixture owner seed failed")
        return item

    def save(self, item):
        write(item["assets"] / "catalogue.json", item["catalogue"])
        write(item["binding_dir"] / "commands.json", item["bindings"])
        write(item["config"], item["settings"])
        write(self.projects / (item["name"] + ".json"), item["project"])

    def audit(self, name="alpha"):
        path = self.records[name]["audit"]
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def modes(self, name="alpha"):
        return [x["mode"] for x in self.audit(name)]

    def run(self, *args, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("T028_")}
        environment.update(PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1")
        environment.update(env or {})
        proc = subprocess.run([sys.executable, "-B", str(PROVIDER), "--config", str(self.config), *args],
                              cwd=self.base, env=environment, capture_output=True, timeout=30, check=False)
        OBSERVATIONS.append({"group": CURRENT, "base": str(self.base), "arguments": list(args),
                             "exit_code": proc.returncode, "stdout_hex": proc.stdout.hex(), "stderr_hex": proc.stderr.hex(),
                             "audits": {n: self.audit(n) for n in self.records},
                             "databases": {n: hashlib.sha256(x["database"].read_bytes()).hexdigest()
                                           if x["database"].exists() else None for n, x in self.records.items()}})
        if proc.stderr or not proc.stdout.endswith(b"\n") or proc.stdout.count(b"\n") != 1:
            raise RuntimeError("expected one complete JSON line with empty stderr, not feature failure")
        try:
            body = json.loads(proc.stdout.decode("utf-8"), object_pairs_hook=pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("invalid wire protocol, not expected RED") from exc
        if body.get("schema") != "stride-bootstrap/v1" or body.get("exit_code") != proc.returncode:
            raise RuntimeError("inconsistent public protocol")
        return body, proc


@contextmanager
def case():
    base = Path(tempfile.mkdtemp(prefix="t028-owner-"))
    current = Case(base)
    try:
        yield current
    except BaseException:
        OBSERVATIONS.append({"group": CURRENT, "preserved_failed_fixture": str(base)})
        raise
    else:
        shutil.rmtree(base)


def rows(body, names):
    assert set(body) == {"schema", "command", "dry_run", "selection", "status", "exit_code", "targets"}
    assert body["selection"]["resolved"] == names
    assert [x["target"] for x in body["targets"]] == names
    for row in body["targets"]:
        assert set(row) == {"target", "config_digest", "status", "exit_code", "effects", "core", "error"}
    return body["targets"]


def single(body, status, effects="none", error=None):
    row = rows(body, ["alpha"])[0]
    code = {"repaired": 0, "ready": 0, "failed": 4, "action_required": 3}[status]
    assert body["status"] == row["status"] == status and body["exit_code"] == row["exit_code"] == code
    assert row["effects"] == effects, row["effects"]
    if error is None:
        assert row["error"] is None
    else:
        assert row["error"]["code"] == error
    return row


def core(row):
    result = row["core"]
    assert result["schema"] == "environment-maintenance/result/v1"
    assert result["status"] == row["status"] and result["exit_code"] == row["exit_code"]
    return result


def normalized(value):
    if isinstance(value, list):
        return [normalized(v) for v in value]
    if isinstance(value, dict):
        out = {}
        for key, v in value.items():
            if key == "duration_seconds":
                assert type(v) in (int, float) and math.isfinite(v) and v >= 0
                out[key] = 0
            else:
                out[key] = normalized(v)
        return out
    return value


def binding(item):
    return item["bindings"]["repairs"]["db.prepare"]


def prepare():
    with case() as c:
        item = c.profile()
        body, _ = c.run("infra", "--target", "alpha")
        err = body.get("targets", [{}])[0].get("error") or {}
        assert body["exit_code"] == 0 and body["status"] == "repaired", (
            f"owner.prepare expected public owner preparation; observed exit={body['exit_code']},error={err.get('code')}")
        assert normalized(core(single(body, "repaired", "applied"))) == fixture("prepared-core.json")
        assert c.modes() == ["check", "apply", "check"] and item["database"].is_file()
        assert all(x["stdin"] == "" for x in c.audit()), "repair wire protocol leaked into public CLI"


def already_ready():
    with case() as c:
        item = c.profile(ready=True)
        before = item["database"].read_bytes()
        single(c.run("infra")[0], "ready")
        assert c.modes() == ["check"] and item["database"].read_bytes() == before


def check_only():
    with case() as c:
        item = c.profile()
        row = single(c.run("check")[0], "action_required")
        assert c.modes() == ["check"] and not item["database"].exists()
        req = core(row)["requirements"][0]
        assert req["recommendation"] == "Use the public owner command" and "apply" not in req


def dry_run():
    for command in ("infra", "deps", "check"):
        with case() as c:
            item = c.profile()
            item["catalogue"]["requirements"][0]["kind"] = "deps" if command == "deps" else "infra"
            c.save(item)
            req = core(single(c.run(command, "--dry-run")[0], "action_required"))["requirements"][0]
            assert c.modes() == ["check"] and not item["database"].exists()
            assert req["planned_apply"]["argv"][-1] == "apply" and "apply" not in req


def argv_env():
    with case() as c:
        item = c.profile()
        item["catalogue"]["parameters"] = {"word": {"type": "string", "default": "default", "env": "T028_PARAM"}}
        item["catalogue"]["inherit_env"] = ["T028_INHERITED"]
        for mode in ("check", "apply"):
            binding(item)[mode]["argv"] += ["space in arg", "${word}", "; do-not-run"]
            binding(item)[mode]["env"]["T028_EXPLICIT"] = "${word}"
        c.save(item)
        single(c.run("infra", "--set", "word=cli value", env={"T028_PARAM": "env value", "T028_INHERITED": "inherited", "T028_HIDDEN": "absent"})[0], "repaired", "applied")
        for event in c.audit():
            assert event["args"] == [event["mode"], "space in arg", "cli value", "; do-not-run"]
            assert event["env"] == {"T028_EXPLICIT": "cli value", "T028_INHERITED": "inherited"}
            assert event["stdin"] == "" and event["cwd"] == str(item["binding_dir"])


def apply_failure():
    for failure in (7, "timeout"):
        with case() as c:
            item = c.profile()
            if failure == "timeout":
                item["settings"]["apply_sleep"] = 2
                binding(item)["apply"]["timeout"] = 0.15
            else:
                item["settings"]["apply_exit"] = failure
            c.save(item)
            row = single(c.run("infra")[0], "failed", "unknown" if failure == "timeout" else "applied", "maintenance_failed")
            req = core(row)["requirements"][0]
            assert req["apply"]["timed_out"] is (failure == "timeout")
            if failure != "timeout":
                assert req["apply"]["exit_code"] == failure
            assert req["final_check"]["exit_code"] == 3 and not item["database"].exists()
            assert c.modes() == ["check", "apply", "check"]


def final_failure():
    with case() as c:
        item = c.profile()
        item["settings"]["final_fail"] = True
        c.save(item)
        req = core(single(c.run("infra")[0], "failed", "applied", "maintenance_failed"))["requirements"][0]
        assert req["apply"]["exit_code"] == 0 and req["final_check"]["exit_code"] == 9
        assert item["database"].is_file() and c.modes() == ["check", "apply", "check"]


def corrupt_preserved():
    with case() as c:
        item = c.profile()
        item["database"].parent.mkdir()
        original = b"test-owned corrupt SQLite bytes\0do not replace"
        item["database"].write_bytes(original)
        req = core(single(c.run("infra")[0], "failed", "applied", "maintenance_failed"))["requirements"][0]
        assert req["initial_check"]["exit_code"] == req["final_check"]["exit_code"] == 11
        assert req["apply"]["exit_code"] == 12 and item["database"].read_bytes() == original
        assert c.modes() == ["check", "apply", "check"]


def replay():
    with case() as c:
        item = c.profile()
        single(c.run("infra")[0], "repaired", "applied")
        before = item["database"].read_bytes()
        single(c.run("infra")[0], "ready")
        assert c.modes() == ["check", "apply", "check", "check"] and item["database"].read_bytes() == before


def missing_executable():
    with case() as c:
        item = c.profile()
        binding(item)["apply"]["argv"] = [str(c.base / "absent-owner")]
        c.save(item)
        req = core(single(c.run("infra")[0], "failed", "unknown", "maintenance_failed"))["requirements"][0]
        assert req["apply"]["exit_code"] is None and req["apply"]["error"]
        assert c.modes() == ["check", "check"] and not item["database"].exists()


def invalid(c, item):
    c.save(item)
    row = single(c.run("infra")[0], "failed", "none", "invalid_target_catalogue")
    assert row["core"] is None
    assert not c.audit() and not item["database"].exists()


def invalid_actions():
    invalids = [{"repair": "db.prepare"}, {"argv": [], "timeout": 1}, {"argv": ["a"], "timeout": True},
                {"argv": ["a"], "timeout": 0}, {"argv": ["a"], "timeout": 3601},
                {"argv": ["a"], "timeout": 1, "sql": "forbidden"},
                {"argv": ["a"], "timeout": 1, "env": {"A=B": "bad"}},
                {"argv": ["a\0b"], "timeout": 1}, {"argv": ["a"], "timeout": 1, "cwd": []}]
    for action in invalids:
        with case() as c:
            item = c.profile()
            binding(item)["apply"] = action
            invalid(c, item)


def missing_route():
    for mismatch in (False, True):
        with case() as c:
            item = c.profile()
            if mismatch:
                item["catalogue"]["requirements"][0]["apply"] = {"repair": "other"}
            else:
                item["bindings"]["repairs"] = {}
            invalid(c, item)


def invalid_bindings():
    for variant in ("top_extra", "missing_apply", "recommend_dict", "blank_rec", "empty_id", "nul_id", "unknown_schema"):
        with case() as c:
            item = c.profile()
            if variant == "top_extra": item["bindings"]["private_schema"] = "forbidden"
            elif variant == "missing_apply": del binding(item)["apply"]
            elif variant == "recommend_dict": binding(item)["recommendation"] = {"repair": "db.prepare"}
            elif variant == "blank_rec": binding(item)["recommendation"] = "  "
            elif variant in ("empty_id", "nul_id"):
                item["bindings"]["repairs"]["" if variant == "empty_id" else "bad\0id"] = copy.deepcopy(binding(item))
            else: item["bindings"]["schema"] = "unknown"
            invalid(c, item)


def unused_binding():
    for bad in ("structure", "placeholder", "expanded_env"):
        with case() as c:
            item = c.profile()
            unused = copy.deepcopy(binding(item))
            if bad == "structure": unused["apply"] = {"repair": "unused"}
            elif bad == "placeholder": unused["apply"]["argv"].append("${undefined}")
            else:
                item["catalogue"]["parameters"] = {"bad": {"type": "string", "default": "bad\0value"}}
                unused["check"]["env"]["T028_EXPLICIT"] = "${bad}"
            item["bindings"]["repairs"]["unused"] = unused
            c.save(item)
            expected = "invalid_target_parameters" if bad == "expanded_env" else "invalid_target_catalogue"
            single(c.run("infra")[0], "failed", "none", expected)
            assert not c.audit() and not item["database"].exists()


def partial_all():
    with case() as c:
        item = c.profile()
        c.profile("beta")
        binding(item)["check"] = {"repair": "db.prepare"}
        c.save(item)
        body, _ = c.run("infra", "--target", "all")
        first, second = rows(body, ["alpha", "beta"])
        assert body["status"] == "failed" and body["exit_code"] == 4
        assert first["error"]["code"] == "invalid_target_catalogue" and first["effects"] == "none"
        assert second["status"] == "repaired" and second["effects"] == "applied"
        assert c.modes() == [] and c.modes("beta") == ["check", "apply", "check"]


def freeze():
    with case() as c:
        alpha = c.profile()
        beta = c.profile("beta")
        beta_path = beta["binding_dir"] / "commands.json"
        alpha["settings"]["rewrite"] = [str(beta_path), '{"schema":"invalidated after snapshot"}']
        c.save(alpha)
        body, _ = c.run("infra")
        assert body["status"] == "repaired" and all(x["status"] == "repaired" for x in rows(body, ["alpha", "beta"]))
        assert c.modes("beta") == ["check", "apply", "check"]
        body, _ = c.run("check", "--target", "beta")
        assert body["targets"][0]["error"]["code"] == "invalid_target_catalogue"
        assert c.modes("beta") == ["check", "apply", "check"]


def cwd():
    for choice in ("relative", "absolute", "omitted"):
        with case() as c:
            item = c.profile()
            check_dir = item["binding_dir"] / "check space"
            apply_dir = c.base / "absolute apply"
            check_dir.mkdir(); apply_dir.mkdir()
            if choice == "relative":
                binding(item)["check"]["cwd"] = "check space"
                binding(item)["apply"]["cwd"] = "../bindings/check space"
                expected = [str(check_dir)] * 3
            elif choice == "absolute":
                binding(item)["check"]["cwd"] = str(check_dir)
                binding(item)["apply"]["cwd"] = str(apply_dir)
                expected = [str(check_dir), str(apply_dir), str(check_dir)]
            else: expected = [str(item["binding_dir"])] * 3
            c.save(item)
            single(c.run("infra")[0], "repaired", "applied")
            assert [x["cwd"] for x in c.audit()] == expected
    # Ordinary direct commands keep catalogue-relative cwd even with the new bindings.
    with case() as c:
        item = c.profile(ready=True)
        for field in ("check", "apply"):
            item["catalogue"]["requirements"][0][field] = copy.deepcopy(binding(item)[field])
        item["catalogue"]["requirements"][0]["recommendation"] = "Direct recommendation"
        c.save(item)
        single(c.run("check")[0], "ready")
        assert c.audit()[0]["cwd"] == str(item["assets"])


def secrets():
    with case() as c:
        item = c.profile(ready=True)
        item["catalogue"]["parameters"] = {"token": {"type": "string", "secret": True, "default": SECRET}}
        binding(item)["check"]["argv"].append("${token}")
        item["settings"]["echo"] = SECRET
        c.save(item)
        body, proc = c.run("check")
        row = single(body, "ready")
        assert row["config_digest"] is None and SECRET.encode() not in proc.stdout
        assert c.audit()[0]["args"][-1] == SECRET
        report = core(row)
        assert report["parameters"]["token"] == "[REDACTED]"
        assert "[REDACTED]" in report["requirements"][0]["initial_check"]["stdout"]


def literal_expansion():
    with case() as c:
        item = c.profile()
        literal = "literal-${undefined}"
        destination = item["binding_dir"] / literal
        destination.mkdir()
        item["catalogue"]["parameters"] = {"value": {"type": "string", "default": literal}}
        for mode in ("check", "apply"):
            binding(item)[mode].update(cwd="${value}")
            binding(item)[mode]["argv"].append("${value}")
            binding(item)[mode]["env"]["T028_EXPLICIT"] = "${value}"
        binding(item)["recommendation"] = "Run ${value}"
        c.save(item)
        req = core(single(c.run("check")[0], "action_required"))["requirements"][0]
        assert req["recommendation"] == "Run " + literal
        single(c.run("infra")[0], "repaired", "applied")
        assert all(x["cwd"] == str(destination) and x["args"][-1] == literal
                   and x["env"]["T028_EXPLICIT"] == literal for x in c.audit())


def boundaries():
    paths = [APP / "shell.py", APP / "maintenance_worker.py", APP / "owner_commands.py"]
    assert all(p.is_file() for p in paths), "app-owned adapter is missing"
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert all(not (m == "sqlite3" or m.startswith(("poise.", "apps.stride", "apps.requirements", "psycopg", "sqlalchemy"))) for m in modules), "private database/domain import"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(sql in node.value.upper() for sql in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "PRAGMA ")), "database schema leaked into adapter"
            if path.name == "owner_commands.py" and isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                assert not name.startswith(("subprocess.", "sqlite3.", "os.system", "os.exec", "os.spawn")), "second executor in projection"
    assert "environment_maintenance.engine" in (APP / "maintenance_worker.py").read_text()


def documentation(group):
    text = DOC.read_text(encoding="utf-8")
    assert any("а" <= char.lower() <= "я" for char in text), "Russian human documentation required"
    for token in fixture("docs.json")[group]:
        assert token in text, f"missing owner delegation documentation: {token}"


def links():
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/check_documentation.py"),
                           "--checkout", str(ROOT), "--changed", "apps/bootstrap/docs/bst-db-delegation.md"],
                          cwd=ROOT, capture_output=True, timeout=20)
    assert proc.returncode == 0 and not proc.stderr and json.loads(proc.stdout)["errors"] == []


BEHAVIOR = [("owner.prepare", prepare), ("owner.already_ready", already_ready), ("owner.check", check_only),
            ("owner.dry_run", dry_run), ("owner.argv_env", argv_env), ("owner.apply_failure", apply_failure),
            ("owner.final_failure", final_failure), ("owner.corrupt_preserved", corrupt_preserved),
            ("owner.replay", replay), ("owner.missing_executable", missing_executable),
            ("owner.invalid_actions", invalid_actions), ("owner.missing_route", missing_route),
            ("owner.invalid_bindings", invalid_bindings), ("owner.unused_binding", unused_binding),
            ("owner.partial_all", partial_all), ("owner.freeze", freeze), ("owner.cwd", cwd),
            ("owner.secrets", secrets), ("owner.literal_expansion", literal_expansion), ("owner.boundaries", boundaries)]
DOCS = [("docs.schema", lambda: documentation("schema")), ("docs.contract", lambda: documentation("contract")),
        ("docs.safety", lambda: documentation("safety")), ("docs.links", links)]


def main():
    global CURRENT
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("red", "behavior", "documentation"), required=True)
    args = parser.parse_args()
    checks = BEHAVIOR[:1] if args.suite == "red" else BEHAVIOR if args.suite == "behavior" else DOCS
    assert len(BEHAVIOR) == 20 and len(DOCS) == 4
    errors, failures = [], []
    preflight = None
    if args.suite != "documentation":
        try:
            proc = subprocess.run([sys.executable, "-I", "-B", "-c",
                "import sqlite3; from importlib.metadata import version; from environment_maintenance.engine import execute; "
                "assert callable(execute); print(version('environment-maintenance'))"], capture_output=True, timeout=10)
            preflight = {"exit_code": proc.returncode, "stdout_hex": proc.stdout.hex(), "stderr_hex": proc.stderr.hex()}
            if proc.returncode or proc.stderr or proc.stdout != b"0.1.1\n":
                raise RuntimeError("pinned engine/SQLite unavailable in selected runtime")
        except Exception as exc:
            errors.append({"reason": str(exc), "test": "runtime.preflight"})
            checks = []
    for CURRENT, test in checks:
        try:
            test()
        except AssertionError as exc:
            failures.append({"reason": str(exc) or "assertion failed", "test": CURRENT})
        except Exception as exc:
            errors.append({"reason": type(exc).__name__ + ": " + str(exc), "test": CURRENT})
    if args.suite != "documentation" and os.environ.get("POISE_RUN_OUTPUT_DIR"):
        output = Path(os.environ["POISE_RUN_OUTPUT_DIR"]) / "owner-observations.json"
        with output.open("x", encoding="utf-8") as stream:
            json.dump({"schema": "bootstrap-owner-observations/v1", "suite": args.suite,
                       "runtime_preflight": preflight, "observations": OBSERVATIONS}, stream,
                      ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    summary = {"errors": errors, "failures": failures, "tests_run": len(checks)}
    sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    return int(bool(errors or failures))


if __name__ == "__main__":
    raise SystemExit(main())
