#!/usr/bin/env python3
"""Literal black-box verification for the standalone Requirements shell."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "requirements"
PROVIDER = APP / "bin" / "requirements"
FIXTURE = APP / "fixtures" / "requirements-config.json"
DOC = APP / "docs" / "shell-req.md"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def emit(errors, failures, tests_run):
    print(compact({"errors": errors, "failures": failures, "tests_run": tests_run}))
    return 1 if errors or failures else 0


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def write_config(root: Path, mutate=None):
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    data = fixture()
    if mutate is not None:
        mutate(data)
    path = config_dir / "requirements.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, data


def run_provider(config: Path, packet=None, *, raw=None, cwd=None):
    if raw is None:
        raw = compact(packet) + "\n"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [str(PROVIDER), "--config", str(config)],
        input=raw,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd or ROOT),
        env=env,
        check=False,
    )


def parsed(proc):
    try:
        return json.loads(proc.stdout)
    except Exception as exc:
        raise AssertionError(f"stdout is not one JSON object: {proc.stdout!r}: {exc}") from exc


def assert_rejected(proc, reason):
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stderr == "", proc.stderr
    assert parsed(proc) == {"status": "rejected", "reason": reason}, parsed(proc)


def seed_packet(request_id="seed-1"):
    return {
        "operation": "apply",
        "input": {
            "request_id": request_id,
            "expected_revision": 0,
            "operations": [
                {
                    "kind": "put_requirement",
                    "requirement": {
                        "id": "SYS-1",
                        "level": "system",
                        "status": "current",
                        "text": "System one",
                    },
                },
                {
                    "kind": "put_requirement",
                    "requirement": {
                        "id": "APP-1",
                        "level": "application",
                        "status": "current",
                        "text": "Application one",
                    },
                },
                {"kind": "link", "system": "SYS-1", "application": "APP-1"},
            ],
        },
    }


def test_provider_executable():
    assert PROVIDER.is_file(), f"missing provider: {PROVIDER.relative_to(ROOT)}"
    assert os.access(PROVIDER, os.X_OK), "provider is not executable"


def test_isolated_query_and_poison_neighbors():
    with tempfile.TemporaryDirectory(prefix="req-shell-isolated-") as td:
        base = Path(td)
        config, _ = write_config(base)
        poison = base / "config" / "processes" / "analysis.json"
        poison.parent.mkdir(parents=True)
        poison.write_text("{not-json", encoding="utf-8")
        foreign = base / "foreign"
        foreign.mkdir()
        tasks = foreign / "tasks.sqlite"
        execution = foreign / "execution.sqlite"
        tasks.write_bytes(b"TASK-DB-SENTINEL\x00")
        execution.write_bytes(b"EXEC-DB-SENTINEL\x00")
        before = {str(p): sha(p) for p in (poison, tasks, execution)}
        proc = run_provider(
            config,
            {"operation": "query", "input": {"queries": [{"id": "r", "kind": "registry"}]}},
            cwd=base,
        )
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert proc.stderr == "", proc.stderr
        body = parsed(proc)
        assert body["status"] == "read_only" and body["revision"] == 0, body
        assert body["results"] == [{"id": "r", "value": {"requirements": {}, "links": []}}], body
        assert {str(p): sha(p) for p in (poison, tasks, execution)} == before
        state = base / "state"
        assert (state / "requirements.sqlite").is_file()
        assert (state / "requirements.lock").exists()


def test_two_configs_are_independent():
    with tempfile.TemporaryDirectory(prefix="req-shell-two-configs-") as td:
        base = Path(td)
        a = base / "a"
        b = base / "b"
        ca, _ = write_config(a)
        cb, _ = write_config(b)
        first = run_provider(ca, seed_packet("seed-a"), cwd=base)
        assert first.returncode == 0, (first.stdout, first.stderr)
        second = run_provider(
            cb,
            {"operation": "query", "input": {"queries": [{"id": "r", "kind": "registry"}]}},
            cwd=base,
        )
        assert second.returncode == 0, (second.stdout, second.stderr)
        body = parsed(second)
        assert body["revision"] == 0, body
        assert body["results"][0]["value"] == {"requirements": {}, "links": []}, body
        assert (a / "state" / "requirements.sqlite").read_bytes() != b""
        assert (b / "state" / "requirements.sqlite").read_bytes() != b""
        assert (a / "state" / "requirements.sqlite").resolve() != (b / "state" / "requirements.sqlite").resolve()


def test_missing_required_config_is_fail_closed():
    with tempfile.TemporaryDirectory(prefix="req-shell-missing-") as td:
        base = Path(td)
        def mutate(data):
            del data["paths"]["database"]
        config, _ = write_config(base, mutate)
        proc = run_provider(
            config,
            {"operation": "query", "input": {"queries": [{"id": "r", "kind": "registry"}]}},
            cwd=base,
        )
        assert_rejected(
            proc,
            "Requirements config paths: required exact fields ['database', 'lock', 'state']; missing ['database']; unknown []",
        )
        assert not (base / "state").exists()


def test_configured_input_limit_is_enforced():
    with tempfile.TemporaryDirectory(prefix="req-shell-limit-") as td:
        base = Path(td)
        def mutate(data):
            data["batch"]["max_input_bytes"] = 32
        config, _ = write_config(base, mutate)
        raw = compact({"operation": "query", "input": {"queries": [{"id": "registry-long", "kind": "registry"}]}}) + "\n"
        assert len(raw.encode("utf-8")) > 32
        proc = run_provider(config, raw=raw, cwd=base)
        assert_rejected(proc, "Requirements packet exceeds max_input_bytes")
        assert not (base / "state").exists()


def test_task_id_policy_is_required_and_validated():
    with tempfile.TemporaryDirectory(prefix="req-shell-taskids-") as td:
        base = Path(td)
        def mutate(data):
            data["task_ids"]["width"] = 0
        config, _ = write_config(base, mutate)
        proc = run_provider(
            config,
            {"operation": "query", "input": {"queries": [{"id": "r", "kind": "registry"}]}},
            cwd=base,
        )
        assert_rejected(proc, "task_ids width: required positive integer")
        assert not (base / "state").exists()


def test_apply_exact_replay_preserves_revision():
    with tempfile.TemporaryDirectory(prefix="req-shell-replay-") as td:
        base = Path(td)
        config, _ = write_config(base)
        packet = seed_packet("same-request")
        first = run_provider(config, packet, cwd=base)
        second = run_provider(config, packet, cwd=base)
        assert first.returncode == second.returncode == 0
        assert parsed(first) == {"status": "applied", "revision": 1, "replayed": False}, parsed(first)
        assert parsed(second) == {"status": "applied", "revision": 1, "replayed": True}, parsed(second)


def test_plan_task_preserves_requirement_chain():
    with tempfile.TemporaryDirectory(prefix="req-shell-plan-") as td:
        base = Path(td)
        config, _ = write_config(base)
        seeded = run_provider(config, seed_packet("seed-plan"), cwd=base)
        assert seeded.returncode == 0, (seeded.stdout, seeded.stderr)
        task_text = "Create a canonical Task from Requirements"
        proc = run_provider(
            config,
            {
                "operation": "query",
                "input": {
                    "queries": [
                        {
                            "id": "plan",
                            "kind": "plan_task",
                            "task_requirements": [{"text": task_text, "applications": ["APP-1"]}],
                        }
                    ]
                },
            },
            cwd=base,
        )
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        body = parsed(proc)
        value = body["results"][0]["value"]
        assert body["revision"] == 1 and value["status"] == "ready" and value["gaps"] == [], body
        assert value["snapshot"]["task_requirements"] == [{"text": task_text, "applications": ["APP-1"]}]
        assert value["snapshot"]["links"] == [{"system": "SYS-1", "application": "APP-1"}]
        assert [x["id"] for x in value["chains"][0]["applications"]] == ["APP-1"]
        assert [x["id"] for x in value["chains"][0]["systems"]] == ["SYS-1"]


BEHAVIOR = [
    ("shell.provider", test_provider_executable),
    ("shell.isolated_query", test_isolated_query_and_poison_neighbors),
    ("shell.two_configs", test_two_configs_are_independent),
    ("shell.missing_required", test_missing_required_config_is_fail_closed),
    ("shell.input_limit", test_configured_input_limit_is_enforced),
    ("shell.task_ids", test_task_id_policy_is_required_and_validated),
    ("shell.replay", test_apply_exact_replay_preserves_revision),
    ("shell.plan_task", test_plan_task_preserves_requirement_chain),
]


def run_behavior():
    # Registered RED predicate: absence of the production provider is the only
    # intended pre-implementation failure. Do not execute dependent cases yet.
    if not PROVIDER.is_file():
        return emit([], [{"reason": "missing provider: apps/requirements/bin/requirements", "test": "shell.provider"}], 1)
    errors, failures = [], []
    for name, test in BEHAVIOR:
        try:
            test()
        except AssertionError as exc:
            failures.append({"reason": str(exc) or "assertion failed", "test": name})
        except Exception as exc:
            errors.append({"reason": f"{type(exc).__name__}: {exc}", "test": name})
    return emit(errors, failures, len(BEHAVIOR))


def run_documentation():
    checks = [
        ("docs.command", ["apps/requirements/bin/requirements", "--config"]),
        ("docs.config", ["stride-requirements-config/v1", "max_input_bytes", "task_ids"]),
        ("docs.ownership", ["Requirements", "neighbor", "database", "lock"]),
        ("docs.failure", ["exit 2", "apps/requirements/tests/test_shell.py", "--suite behavior"]),
    ]
    errors, failures = [], []
    try:
        text = DOC.read_text(encoding="utf-8")
    except Exception as exc:
        return emit([{"reason": f"{type(exc).__name__}: {exc}", "test": "docs.file"}], [], 0)
    for name, needles in checks:
        missing = [needle for needle in needles if needle not in text]
        if missing:
            failures.append({"reason": f"missing literal(s): {','.join(missing)}", "test": name})
    return emit(errors, failures, len(checks))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, choices=("behavior", "documentation"))
    args = parser.parse_args()
    return run_behavior() if args.suite == "behavior" else run_documentation()


if __name__ == "__main__":
    raise SystemExit(main())
