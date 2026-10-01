#!/usr/bin/env python3
"""Permanent profile guards, migrated at T027 to actual empty-catalogue execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "bootstrap"
PROVIDER = APP / "bin" / "bootstrap"
FIXTURES = APP / "fixtures" / "profiles"
DOC = APP / "docs" / "bst-profiles.md"
TARGET_ENV = "T026_TEST_TARGET"
SECRET = "T026-private-value-never-emit"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


@contextmanager
def workspace():
    with tempfile.TemporaryDirectory(prefix="bst-profiles-") as td:
        base = Path(td)
        # A root test runner must be able to launch real unprivileged access checks.
        base.chmod(0o755)
        config = base / "config" / "bootstrap.json"
        write_json(config, fixture("bootstrap-config.json"))
        projects = config.parent / "projects"
        projects.mkdir()
        yield base, config, projects


def configure(config, **fields):
    data = json.loads(config.read_text(encoding="utf-8"))
    limits = fields.pop("limits", None)
    data.update(fields)
    if limits:
        data["limits"].update(limits)
    write_json(config, data)


def add_profile(projects, name="alpha", *, changes=None, catalogue=None):
    record = fixture("alpha.json")
    record.update(id=name, catalog=name + "-assets/requirements.json")
    if changes:
        record.update(changes)
    path = projects / (name + ".json")
    write_json(path, record)
    write_json(projects / (name + "-assets") / "requirements.json", catalogue or fixture("catalogue.json"))
    return path


def run(config, *args, env=None, unprivileged=False):
    if not PROVIDER.is_file() or not os.access(PROVIDER, os.X_OK):
        raise RuntimeError("provider missing or not executable; this is not semantic RED")
    environment = os.environ.copy()
    environment.pop(TARGET_ENV, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        environment.update(env)
    privileges = {}
    if unprivileged and os.geteuid() == 0:
        privileges = {"user": 65534, "group": 65534, "extra_groups": []}
    return subprocess.run(
        [sys.executable, "-B", str(PROVIDER), "--config", str(config), *args],
        cwd=config.parent.parent, env=environment, capture_output=True, timeout=8,
        check=False, **privileges,
    )


def response(proc):
    # Infrastructure, malformed protocol and crashes cannot impersonate expected RED.
    if proc.stderr:
        raise RuntimeError("provider stderr was not empty")
    if not proc.stdout.endswith(b"\n") or proc.stdout.count(b"\n") != 1:
        raise RuntimeError("provider did not emit one complete JSON line")
    try:
        body = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("provider output was not strict UTF-8 JSON") from exc
    if not isinstance(body, dict) or body.get("schema") != "stride-bootstrap/v1":
        raise RuntimeError("provider protocol schema was not recognized")
    if body.get("exit_code") != proc.returncode:
        raise RuntimeError("process exit and reported exit differed")
    return body


def rejected(proc, code):
    body = response(proc)
    assert proc.returncode == 2 and body["status"] == "rejected", "global rejection not returned"
    assert set(body) == {"schema", "status", "exit_code", "error", "targets"}, "global envelope drift"
    assert body["targets"] == [], "global rejection retained targets"
    assert body["error"]["code"] == code, "wrong global error code: " + code
    assert body["error"]["retryable"] is False, "unexpected retryable global error"
    return body


def selected(proc, names, *, requested="all", source="configuration", command="check", dry_run=False):
    body = response(proc)
    assert set(body) == {"schema", "command", "dry_run", "selection", "status", "exit_code", "targets"}, "normal envelope drift"
    assert body["command"] == command and body["dry_run"] is dry_run, "command or dry-run changed"
    assert body["selection"] == {"requested": requested, "source": source, "resolved": names}, "wrong target selection"
    assert [row["target"] for row in body["targets"]] == names, "wrong target result order"
    for row in body["targets"]:
        assert set(row) == {"target", "config_digest", "status", "exit_code", "effects", "core", "error"}, "target envelope drift"
        assert row["effects"] == "none", "profile fixture caused a business effect"
    return body


def qualified(proc, names, **kwargs):
    body = selected(proc, names, **kwargs)
    assert proc.returncode == 0 and body["status"] == "not_required", "empty catalogue did not use actual maintenance core"
    for row in body["targets"]:
        assert row["status"] == "not_required" and row["exit_code"] == 0
        assert row["error"] is None
        core = row["core"]
        assert core["schema"] == "environment-maintenance/result/v1" and core["status"] == "not_required"
        assert core["exit_code"] == 0 and core["requirements"] == []
    return body


def failed(proc, name, code):
    body = selected(proc, [name], requested=name, source="argument")
    assert proc.returncode == 4 and body["status"] == "failed", "invalid target did not fail"
    row = body["targets"][0]
    assert row["status"] == "failed" and row["exit_code"] == 4
    assert row["error"]["code"] == code, "wrong target error: " + code
    assert row["error"]["retryable"] is False
    assert row["config_digest"] is None, "incomplete/invalid input had a digest"
    return body


def file_inventory(root):
    """Independent input/sentinel fingerprints, never production snapshots."""
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            result[str(path.relative_to(root))] = ("link", os.readlink(path))
        elif path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            result[str(path.relative_to(root))] = "directory"
    return result


def profiles_selected():
    with workspace() as (_, config, projects):
        add_profile(projects)
        before = file_inventory(projects)
        proc = run(config, "check", "--target", "alpha")
        body = response(proc)
        assert body == fixture("selected-result.json") and proc.returncode == 0 and file_inventory(projects) == before, \
            "selected profile was not reported without effects"


def precedence():
    with workspace() as (_, config, projects):
        add_profile(projects)
        add_profile(projects, "beta")
        configure(config, target_env=TARGET_ENV, default_target="beta")
        for command in ("infra", "deps", "check"):
            for value in ("beta", "", "../unsafe"):
                qualified(run(config, command, "--target", "alpha", env={TARGET_ENV: value}),
                            ["alpha"], requested="alpha", source="argument", command=command)
            qualified(run(config, command, env={TARGET_ENV: "alpha"}), ["alpha"], requested="alpha", source="environment", command=command)
            qualified(run(config, command), ["beta"], requested="beta", command=command)
            rejected(run(config, command, env={TARGET_ENV: ""}), "invalid_selector")
        configure(config, target_env=None)
        qualified(run(config, "check", "--dry-run", env={TARGET_ENV: "alpha"}), ["beta"], requested="beta", dry_run=True)
        configure(config, target_env=TARGET_ENV, default_target="all")
        qualified(run(config, "check", env={TARGET_ENV: "all"}), ["alpha", "beta"], source="environment")


def discovery():
    with workspace() as (_, config, projects):
        add_profile(projects, "zeta")
        add_profile(projects, "alpha")
        (projects / "ignored.txt").write_bytes(b"not-json")
        nested = projects / "directory.json"
        nested.mkdir()
        (nested / "broken.json").write_bytes(b"{")
        qualified(run(config, "check"), ["alpha", "zeta"])
        add_profile(projects, "new-target")
        qualified(run(config, "check"), ["alpha", "new-target", "zeta"])
        (projects / "zeta.json").unlink()
        qualified(run(config, "check"), ["alpha", "new-target"])


def explicit_isolation():
    with workspace() as (_, config, projects):
        add_profile(projects)
        (projects / "broken.json").write_bytes(b"{not-json")
        qualified(run(config, "check", "--target", "alpha"), ["alpha"], requested="alpha", source="argument")


def selector_errors():
    with workspace() as (_, config, projects):
        add_profile(projects)
        configure(config, target_env=TARGET_ENV)
        rejected(run(config, "check", "--target", "missing"), "target_not_found")
        rejected(run(config, "check", env={TARGET_ENV: "missing"}), "target_not_found")
        configure(config, default_target="missing")
        rejected(run(config, "check"), "target_not_found")
        for target in ("", ".", "..", "../alpha", "x/y", "x\\y"):
            rejected(run(config, "check", "--target", target), "invalid_selector")
        for args in (("check", "--target"), ("check", "--target", "alpha", "--target", "alpha"),
                     ("check", "--set", "=secret"), ("check", "--unknown", SECRET), ("check", SECRET)):
            proc = run(config, *args)
            rejected(proc, "invalid_request")
            assert SECRET.encode() not in proc.stdout + proc.stderr


def projects_root():
    with workspace() as (base, config, projects):
        projects.rmdir()
        rejected(run(config, "check"), "projects_unavailable")
        projects.write_bytes(b"not a directory")
        rejected(run(config, "check"), "projects_unavailable")
        projects.unlink()
        projects.mkdir()
        projects.chmod(0)
        try:
            rejected(run(config, "check", unprivileged=True), "projects_unavailable")
        finally:
            projects.chmod(0o755)
        elsewhere = base / "elsewhere"
        elsewhere.mkdir()
        projects.rmdir()
        projects.symlink_to(elsewhere, target_is_directory=True)
        rejected(run(config, "check"), "projects_unavailable")


def record_shape():
    mutations = [lambda d: d.pop("values"), lambda d: d.update(unexpected=True),
                 lambda d: d.update(schema="other/v1"), lambda d: d.update(id="beta"),
                 lambda d: d.update(id="all"), lambda d: d.update(catalog=None),
                 lambda d: d.update(repairs=False), lambda d: d.update(values=[]),
                 lambda d: d.update(parameter_values_files="x")]
    with workspace() as (_, config, projects):
        for mutation in mutations:
            record_path = add_profile(projects)
            data = fixture("alpha.json")
            mutation(data)
            write_json(record_path, data)
            failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_config")
        (projects / "alpha.json").write_text("[]\n", encoding="utf-8")
        failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_config")


def strict_json():
    with workspace() as (_, config, projects):
        path = add_profile(projects)
        valid = path.read_bytes()
        for invalid in (b'{"id":"alpha","id":"alpha"}', b'{"x":NaN}', b'{"x":Infinity}',
                        b'{"x":1e999}', b'{"x":"\\ud800"}', b"\xff", b"\xef\xbb\xbf" + valid,
                        b'{"x":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'):
            path.write_bytes(invalid)
            failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_config")
        path.write_bytes(valid)
        catalogue = projects / "alpha-assets/requirements.json"
        catalogue.write_bytes(b'{"parameters":{},"parameters":{},"requirements":[]}')
        failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_catalogue")
        write_json(catalogue, fixture("catalogue.json"))
        data = fixture("alpha.json")
        data["parameter_values_files"] = ["values.json"]
        write_json(path, data)
        (projects / "values.json").write_bytes(b'{"x":1e999}')
        failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_parameters")
        # The same strictness applies to Bootstrap's own envelope, without disclosing tokens.
        original_config = config.read_bytes()
        config.write_bytes(b'{"' + SECRET.encode() + b'":1,"' + SECRET.encode() + b'":2}')
        proc = run(config, "check")
        rejected(proc, "invalid_bootstrap_config")
        assert SECRET.encode() not in proc.stdout + proc.stderr
        config.write_bytes(original_config)
        configure(config, limits={"target_timeout_seconds": 10 ** 400})
        rejected(run(config, "check"), "invalid_bootstrap_config")


def asset_shape():
    with workspace() as (_, config, projects):
        path = add_profile(projects)
        catalogue = projects / "alpha-assets/requirements.json"
        for invalid in ([], {}, {"schema": "other", "parameters": {}, "requirements": []},
                        {"schema": "environment-maintenance/requirements/v1", "parameters": [], "requirements": []},
                        {"schema": "environment-maintenance/requirements/v1", "parameters": {}, "requirements": {}}):
            write_json(catalogue, invalid)
            failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_catalogue")
        write_json(catalogue, fixture("catalogue.json"))
        record = fixture("alpha.json")
        record["repairs"] = "repairs.json"
        write_json(path, record)
        for invalid in ([], {"schema": "other", "repairs": {}}, {"schema": "environment-maintenance/repairs/v1", "repairs": []}):
            write_json(projects / "repairs.json", invalid)
            failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_catalogue")
        write_json(projects / "repairs.json", {"schema": "environment-maintenance/repairs/v1", "repairs": {}})
        qualified(run(config, "check", "--target", "alpha"), ["alpha"], requested="alpha", source="argument")
        record["parameter_values_files"] = ["values.json"]
        write_json(path, record)
        write_json(projects / "values.json", [])
        failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_parameters")


def safe_paths():
    with workspace() as (base, config, projects):
        record = add_profile(projects)
        foreign = base / "outside.json"
        write_json(foreign, fixture("catalogue.json"))
        before = foreign.read_bytes()
        for path in (str(foreign), "../outside.json", "alpha-assets/../alpha-assets/requirements.json", "x\x00y"):
            add_profile(projects, changes={"catalog": path})
            failed(run(config, "check", "--target", "alpha"), "alpha", "unsafe_target_config")
        add_profile(projects)
        record.unlink()
        record.symlink_to(foreign)
        failed(run(config, "check", "--target", "alpha"), "alpha", "unsafe_target_config")
        record.unlink()
        os.mkfifo(record)
        failed(run(config, "check", "--target", "alpha"), "alpha", "unsafe_target_config")
        record.unlink()
        add_profile(projects)
        cat = projects / "alpha-assets/requirements.json"
        cat.unlink()
        cat.symlink_to(foreign)
        failed(run(config, "check", "--target", "alpha"), "alpha", "unsafe_target_config")
        cat.unlink()
        os.mkfifo(cat)
        failed(run(config, "check", "--target", "alpha"), "alpha", "unsafe_target_config")
        cat.unlink()
        (projects / "alpha-assets").rmdir()
        (projects / "alpha-assets").symlink_to(base, target_is_directory=True)
        failed(run(config, "check", "--target", "alpha"), "alpha", "unsafe_target_config")
        assert foreign.read_bytes() == before


def asset_access():
    with workspace() as (_, config, projects):
        add_profile(projects)
        cat = projects / "alpha-assets/requirements.json"
        data = cat.read_bytes()
        cat.unlink()
        failed(run(config, "check", "--target", "alpha"), "alpha", "target_input_unavailable")
        cat.write_bytes(data)
        cat.chmod(0)
        try:
            failed(run(config, "check", "--target", "alpha", unprivileged=True), "alpha", "target_input_unavailable")
        finally:
            cat.chmod(0o644)
        record = projects / "alpha.json"
        record.chmod(0)
        try:
            failed(run(config, "check", "--target", "alpha", unprivileged=True), "alpha", "target_input_unavailable")
        finally:
            record.chmod(0o644)


def mixed():
    with workspace() as (_, config, projects):
        add_profile(projects, "alpha")
        (projects / "broken.json").write_bytes(b"{")
        proc = run(config, "check")
        body = selected(proc, ["alpha", "broken"])
        assert proc.returncode == 4 and body["status"] == "failed"
        assert body["targets"][0]["status"] == "not_required"
        assert body["targets"][1]["error"]["code"] == "invalid_target_config"
        # A broken first target also must not suppress the later valid one.
        (projects / "broken.json").rename(projects / "aaa.json")
        body = selected(run(config, "check"), ["aaa", "alpha"])
        assert body["targets"][0]["status"] == "failed" and body["targets"][1]["status"] == "not_required"


def count_budget():
    with workspace() as (_, config, projects):
        add_profile(projects)
        add_profile(projects, "beta")
        configure(config, limits={"max_targets": 1})
        rejected(run(config, "check"), "limit_exceeded")
        qualified(run(config, "check", "--target", "alpha"), ["alpha"], requested="alpha", source="argument")


def byte_budget():
    with workspace() as (_, config, projects):
        record = add_profile(projects)
        cat = projects / "alpha-assets/requirements.json"
        # Count independent fixture input bytes, not a production-derived result.
        budget = record.stat().st_size + cat.stat().st_size
        configure(config, limits={"max_target_bytes": budget})
        qualified(run(config, "check", "--target", "alpha"), ["alpha"], requested="alpha", source="argument")
        configure(config, limits={"max_target_bytes": budget - 1})
        failed(run(config, "check", "--target", "alpha"), "alpha", "target_input_unavailable")
        configure(config, limits={"max_target_bytes": budget + 100})
        data = fixture("alpha.json")
        data["parameter_values_files"] = ["alpha-assets/values.json"]
        write_json(record, data)
        write_json(projects / "alpha-assets/values.json", {"padding": "v" * 500})
        failed(run(config, "check", "--target", "alpha"), "alpha", "target_input_unavailable")
        add_profile(projects, "beta")
        proc = run(config, "check")
        body = selected(proc, ["alpha", "beta"])
        assert proc.returncode == 4 and body["targets"][1]["status"] == "not_required"


def output_budget():
    with workspace() as (_, config, projects):
        add_profile(projects)
        expected = fixture("selected-result.json")
        size = len((json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        configure(config, limits={"max_output_bytes": size})
        proc = run(config, "check", "--target", "alpha")
        assert response(proc) == expected and len(proc.stdout) == size, "normal compact output boundary differs"
        configure(config, limits={"max_output_bytes": size - 1})
        proc = run(config, "check", "--target", "alpha")
        body = selected(proc, ["alpha"], requested="alpha", source="argument")
        row = body["targets"][0]
        assert body["status"] == row["status"] == "failed" and proc.returncode == 4
        assert row["error"]["code"] == "output_limit_exceeded" and row["core"] is None
        assert len(proc.stdout) <= size - 1 and row["effects"] == "none"
        configure(config, limits={"max_output_bytes": 1})
        rejected(run(config, "check", "--target", "alpha"), "limit_exceeded")
        assert not (config.parent / "MUST_NOT_EXIST").exists()


def snapshot_digest():
    with workspace() as (_, config, projects):
        record = add_profile(projects)
        cat = projects / "alpha-assets/requirements.json"
        body = qualified(run(config, "check", "--target", "alpha"), ["alpha"], requested="alpha", source="argument")
        assert body["targets"][0]["config_digest"] == "dbbea48f607930988c72b7fc18e3272fa3d8fb2d725d54454385789032d61445"
        for path in (record, cat):
            data = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps(dict(reversed(list(data.items()))), separators=(",", ":")), encoding="utf-8")
        body = qualified(run(config, "check", "--target", "alpha"), ["alpha"], requested="alpha", source="argument")
        assert body["targets"][0]["config_digest"] == "dbbea48f607930988c72b7fc18e3272fa3d8fb2d725d54454385789032d61445"
        data = fixture("alpha.json")
        data["parameter_values_files"] = ["first.json", "second.json"]
        catalogue = fixture("catalogue.json")
        catalogue["parameters"] = {"x": {"type": "integer", "secret": False}}
        write_json(cat, catalogue)
        write_json(record, data)
        write_json(projects / "first.json", {"x": 1})
        write_json(projects / "second.json", {"x": 2})
        first = response(run(config, "check", "--target", "alpha"))["targets"][0]["config_digest"]
        data["parameter_values_files"].reverse()
        write_json(record, data)
        second = response(run(config, "check", "--target", "alpha"))["targets"][0]["config_digest"]
        assert isinstance(first, str) and len(first) == 64 and isinstance(second, str) and len(second) == 64 and first != second


def secrets():
    with workspace() as (_, config, projects):
        add_profile(projects, catalogue=fixture("secret-catalogue.json"), changes={"values": {"token": SECRET}})
        for args in (("check",), ("deps", "--set", "token=" + SECRET), ("infra", "--dry-run")):
            proc = run(config, *args)
            body = qualified(proc, ["alpha"], command=args[0], dry_run="--dry-run" in args)
            assert body["targets"][0]["config_digest"] is None
            assert SECRET.encode() not in proc.stdout + proc.stderr
        cat = fixture("catalogue.json")
        for declaration in (None, "unknown", {"type": "string", "secret": "false"}):
            cat["parameters"] = {"token": declaration}
            write_json(projects / "alpha-assets/requirements.json", cat)
            proc = run(config, "check", "--target", "alpha")
            failed(proc, "alpha", "invalid_target_parameters")
            assert SECRET.encode() not in proc.stdout + proc.stderr
        # The core declares omitted secret=False. Keep conservative digest suppression,
        # without silently changing the parameter owner's public-value policy.
        cat["parameters"] = {"token": {"type": "string"}}
        write_json(projects / "alpha-assets/requirements.json", cat)
        proc = run(config, "check")
        body = qualified(proc, ["alpha"])
        assert body["targets"][0]["config_digest"] is None
        assert body["targets"][0]["core"]["parameters"]["token"] == SECRET
        # Secret values in schema/parser failures are not copied into diagnostics.
        record = fixture("alpha.json")
        record[SECRET] = True
        write_json(projects / "alpha.json", record)
        proc = run(config, "check", "--target", "alpha")
        failed(proc, "alpha", "invalid_target_config")
        assert SECRET.encode() not in proc.stdout + proc.stderr


def no_effects():
    with workspace() as (base, config, projects):
        add_profile(projects, catalogue=fixture("side-effect-catalogue.json"))
        foreign = base / "foreign" / "sequencing.sqlite"
        foreign.parent.mkdir()
        foreign.write_bytes(b"private-state\x00")
        poison = config.parent / "processes" / "analysis.json"
        poison.parent.mkdir()
        poison.write_bytes(b"not-json")
        before = file_inventory(base)
        for command in ("check", "infra", "deps"):
            for options in ((), ("--dry-run",), ("--set", "x=" + SECRET)):
                proc = run(config, command, *options)
                body = selected(proc, ["alpha"], command=command, dry_run="--dry-run" in options)
                assert proc.returncode == 4 and body["status"] == "failed"
                assert body["targets"][0]["error"]["code"] == "invalid_target_catalogue"
                assert file_inventory(base) == before, "invalid catalogue changed input/foreign state or executed a command"
        other = base / "other" / "bootstrap.json"
        write_json(other, fixture("bootstrap-config.json"))
        other_projects = other.parent / "projects"
        other_projects.mkdir()
        add_profile(other_projects, "beta")
        qualified(run(other, "check"), ["beta"])
        failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_catalogue")


def utf8():
    with workspace() as (base, config, projects):
        add_profile(projects, "цель")
        unicode_dir = base / "настройки"
        config.parent.rename(unicode_dir)
        config = unicode_dir / "bootstrap.json"
        first = None
        for encoding in ("ascii", "latin-1", "utf-8"):
            proc = run(config, "check", "--target", "цель", env={"PYTHONIOENCODING": encoding, "LC_ALL": "C.UTF-8", "PYTHONUTF8": "1"})
            qualified(proc, ["цель"], requested="цель", source="argument")
            assert "цель".encode("utf-8") in proc.stdout
            first = proc.stdout if first is None else first
            assert proc.stdout == first, "stdout bytes depend on ambient encoding"


def examples():
    example_root = APP / "examples" / "projects"
    assert (example_root / "alpha.json").is_file(), "separate example profile missing"
    assert (example_root / "alpha-assets" / "requirements.json").is_file(), "example asset missing"
    ignore = subprocess.run(["git", "check-ignore", "--no-index", "apps/bootstrap/config/projects/operator.json"], cwd=ROOT, capture_output=True, timeout=8)
    assert ignore.returncode == 0, "operational default projects path is not ignored"
    tracked = subprocess.run(["git", "ls-files", "--", "apps/bootstrap/config/projects"], cwd=ROOT, capture_output=True, timeout=8)
    assert tracked.returncode == 0 and not tracked.stdout, "operational profiles tracked in Git"
    with workspace() as (_, config, projects):
        # Test-owned explicit copy; the application must not create or overwrite it.
        shutil.copytree(example_root, projects, dirs_exist_ok=True)
        before = file_inventory(projects)
        qualified(run(config, "check"), ["alpha"])
        assert file_inventory(projects) == before
        (projects / "alpha.json").write_bytes(b"{")
        failed(run(config, "check", "--target", "alpha"), "alpha", "invalid_target_config")
        assert (projects / "alpha.json").read_bytes() == b"{", "example silently overwrote operational data"


BEHAVIOR = [
    ("profiles.selected", profiles_selected), ("profiles.precedence", precedence),
    ("profiles.discovery", discovery), ("profiles.explicit_isolation", explicit_isolation),
    ("profiles.selector_errors", selector_errors), ("profiles.projects_root", projects_root),
    ("profiles.record_shape", record_shape), ("profiles.strict_json", strict_json),
    ("profiles.asset_shape", asset_shape), ("profiles.safe_paths", safe_paths),
    ("profiles.asset_access", asset_access), ("profiles.mixed", mixed),
    ("profiles.count_budget", count_budget), ("profiles.byte_budget", byte_budget),
    ("profiles.output_budget", output_budget), ("profiles.snapshot_digest", snapshot_digest),
    ("profiles.secrets", secrets), ("profiles.no_effects", no_effects),
    ("profiles.utf8", utf8), ("profiles.examples", examples),
]


def docs_selection():
    text = DOC.read_text(encoding="utf-8")
    for needle in ("--target", "target_env", "default_target", "infra", "deps", "check", "projects/", "all"):
        assert needle in text, "selection documentation missing: " + needle
    assert any("а" <= ch.lower() <= "я" for ch in text), "human documentation must be Russian"


def docs_profiles():
    text = DOC.read_text(encoding="utf-8")
    for needle in ("stride-bootstrap-project/v1", "parameter_values_files", "repairs", "catalog", "values", "config_digest", "max_target_bytes", "max_targets", "max_output_bytes", "examples"):
        assert needle in text, "profile documentation missing: " + needle


def docs_boundary():
    text = DOC.read_text(encoding="utf-8")
    for needle in ("T026", "T027", "T028", "action_required", "maintenance_unavailable", "unsafe_target_config", "target_input_unavailable", "secret", "UTF-8", "--suite behavior", "--suite documentation"):
        assert needle in text, "slice boundary documentation missing: " + needle
    assert "bst-profiles.md" in (APP / "docs" / "shell-bst.md").read_text(encoding="utf-8"), "historical shell page lacks current-contract pointer"


def docs_links():
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/check_documentation.py"), "--checkout", str(ROOT), "--changed", "apps/bootstrap/docs/bst-profiles.md"], cwd=ROOT, capture_output=True, timeout=30)
    assert proc.returncode == 0 and not proc.stderr, "maintained documentation link/anchor audit failed"
    assert json.loads(proc.stdout)["errors"] == [], "broken documentation links"


DOCUMENTATION = [("docs.selection", docs_selection), ("docs.profiles", docs_profiles),
                 ("docs.boundary", docs_boundary), ("docs.links", docs_links)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("red", "behavior", "documentation"), required=True)
    args = parser.parse_args()
    checks = BEHAVIOR[:1] if args.suite == "red" else (BEHAVIOR if args.suite == "behavior" else DOCUMENTATION)
    errors, failures = [], []
    for name, test in checks:
        try:
            test()
        except AssertionError as exc:
            failures.append({"reason": str(exc) or "assertion failed", "test": name})
        except Exception as exc:
            errors.append({"reason": type(exc).__name__ + ": " + str(exc), "test": name})
    result = {"errors": errors, "failures": failures, "tests_run": len(checks)}
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    return 1 if errors or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
