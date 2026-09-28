#!/usr/bin/env python3
"""Literal black-box verification for the standalone Execution shell."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "execution"
PROVIDER = APP / "bin" / "execution"
FIXTURE = APP / "fixtures" / "execution-config.json"
DOC = APP / "docs" / "shell-exe.md"


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
    path = config_dir / "execution.json"
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
    assert parsed(proc) == {"schema": "stride-execution/v1", "status": "rejected", "reason": reason}, parsed(proc)


def contract(task_id="0042"):
    return {
        "task_id": task_id,
        "requirements_snapshot_revision": 2,
        "process_ref": {"goal_type": "development", "revision": "a" * 64},
        "admission_id": "admit-0042",
        "workspace_id": "ws-0042",
        "stage_expectations": [
            {"stage_id": "implementation", "values": [], "files": [], "methods": []}
        ],
        "restart_policy_id": "rp-1",
        "methods": [],
        "evidence_plan": {
            "implementation": {"subject_methods": {}, "arguments": [], "review_arguments": []}
        },
    }


def contract_digest(task_id="0042"):
    raw = json.dumps(contract(task_id), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def setup_packet(request_id="setup-1", *, task_id="0042"):
    return {
        "schema": "stride-execution/v1",
        "operation": "apply",
        "input": {
            "request_id": request_id,
            "expected_revision": 0,
            "operations": [
                {"id": "setup", "kind": "setup_execution", "contract": contract(task_id)}
            ],
        },
    }


def applied(task_id="0042", *, replayed=False):
    return {
        "schema": "stride-execution/v1",
        "status": "applied",
        "revision": 1,
        "replayed": replayed,
        "results": [
            {
                "id": "setup",
                "kind": "setup_execution",
                "changed": True,
                "task_id": task_id,
                "generation": 1,
                "contract_revision": contract_digest(task_id),
            }
        ],
    }


def test_provider_executable():
    assert PROVIDER.is_file(), f"missing provider: {PROVIDER.relative_to(ROOT)}"
    assert os.access(PROVIDER, os.X_OK), "provider is not executable"


def test_isolated_setup_and_poison_neighbors():
    with tempfile.TemporaryDirectory(prefix="exe-shell-isolated-") as td:
        base = Path(td)
        config, _ = write_config(base)
        poison = base / "config" / "processes" / "analysis.json"
        poison.parent.mkdir(parents=True)
        poison.write_text("{not-json", encoding="utf-8")
        foreign = base / "foreign"
        foreign.mkdir()
        requirements = foreign / "requirements.sqlite"
        sequencing = foreign / "sequencing.sqlite"
        requirements.write_bytes(b"REQ-DB-SENTINEL\x00")
        sequencing.write_bytes(b"SEQ-DB-SENTINEL\x00")
        before = {str(p): sha(p) for p in (poison, requirements, sequencing)}
        proc = run_provider(config, setup_packet(), cwd=base)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert proc.stderr == "", proc.stderr
        assert parsed(proc) == applied(), parsed(proc)
        assert {str(p): sha(p) for p in (poison, requirements, sequencing)} == before
        state = base / "state"
        assert (state / "execution.sqlite").is_file()
        assert (state / "execution.lock").exists()


def test_two_configs_are_independent():
    with tempfile.TemporaryDirectory(prefix="exe-shell-two-configs-") as td:
        base = Path(td)
        a = base / "a"
        b = base / "b"
        ca, _ = write_config(a)
        cb, _ = write_config(b)
        first = run_provider(ca, setup_packet("setup-a"), cwd=base)
        second = run_provider(cb, setup_packet("setup-b"), cwd=base)
        assert first.returncode == second.returncode == 0, (first.stdout, first.stderr, second.stdout, second.stderr)
        assert parsed(first) == applied(), parsed(first)
        assert parsed(second) == applied(), parsed(second)
        assert (a / "state" / "execution.sqlite").resolve() != (b / "state" / "execution.sqlite").resolve()


def test_missing_required_config_is_fail_closed():
    with tempfile.TemporaryDirectory(prefix="exe-shell-missing-") as td:
        base = Path(td)
        def mutate(data):
            del data["paths"]["database"]
        config, _ = write_config(base, mutate)
        proc = run_provider(config, setup_packet(), cwd=base)
        assert_rejected(
            proc,
            "Execution config paths: required exact fields ['database', 'lock', 'state']; missing ['database']; unknown []",
        )
        assert not (base / "state").exists()


def test_configured_input_limit_is_enforced():
    with tempfile.TemporaryDirectory(prefix="exe-shell-limit-") as td:
        base = Path(td)
        def mutate(data):
            data["batch"]["max_input_bytes"] = 96
        config, _ = write_config(base, mutate)
        raw = compact(setup_packet("long-request-id-that-exceeds-the-small-configured-input-limit")) + "\n"
        assert len(raw.encode("utf-8")) > 96
        proc = run_provider(config, raw=raw, cwd=base)
        assert_rejected(proc, "Execution packet exceeds max_input_bytes")
        assert not (base / "state").exists()


def test_identifier_policy_is_required_and_validated():
    with tempfile.TemporaryDirectory(prefix="exe-shell-identifiers-") as td:
        base = Path(td)
        def mutate(data):
            data["identifiers"]["task"]["pattern"] = ""
        config, _ = write_config(base, mutate)
        proc = run_provider(config, setup_packet(), cwd=base)
        assert_rejected(proc, "identifiers.task.pattern: required nonempty string")
        assert not (base / "state").exists()


def test_setup_preserves_canonical_task_id():
    with tempfile.TemporaryDirectory(prefix="exe-shell-canonical-") as td:
        base = Path(td)
        config, _ = write_config(base)
        first = run_provider(config, setup_packet(task_id="0042"), cwd=base)
        assert first.returncode == 0, (first.stdout, first.stderr)
        query = run_provider(
            config,
            {
                "schema": "stride-execution/v1",
                "operation": "query",
                "input": {"queries": [{"id": "q1", "kind": "execution", "task_id": "0042"}]},
            },
            cwd=base,
        )
        assert query.returncode == 0, (query.stdout, query.stderr)
        assert query.stderr == "", query.stderr
        body = parsed(query)
        assert body == {
            "schema": "stride-execution/v1",
            "status": "read_only",
            "revision": 1,
            "results": [
                {
                    "id": "q1",
                    "value": {
                        "task_id": "0042",
                        "generation": 1,
                        "contract_revision": contract_digest("0042"),
                        "state": "draft",
                        "current_stage": None,
                        "iteration": 1,
                        "workspace_id": "ws-0042",
                        "admission_id": "admit-0042",
                    },
                }
            ],
        }, body


def test_apply_exact_replay_preserves_revision():
    with tempfile.TemporaryDirectory(prefix="exe-shell-replay-") as td:
        base = Path(td)
        config, _ = write_config(base)
        packet = setup_packet("same-request")
        first = run_provider(config, packet, cwd=base)
        second = run_provider(config, packet, cwd=base)
        assert first.returncode == second.returncode == 0
        assert parsed(first) == applied(replayed=False), parsed(first)
        assert parsed(second) == applied(replayed=True), parsed(second)


BEHAVIOR = [
    ("shell.provider", test_provider_executable),
    ("shell.isolated_setup", test_isolated_setup_and_poison_neighbors),
    ("shell.two_configs", test_two_configs_are_independent),
    ("shell.missing_required", test_missing_required_config_is_fail_closed),
    ("shell.input_limit", test_configured_input_limit_is_enforced),
    ("shell.identifiers", test_identifier_policy_is_required_and_validated),
    ("shell.canonical_task_id", test_setup_preserves_canonical_task_id),
    ("shell.replay", test_apply_exact_replay_preserves_revision),
]


def run_behavior():
    if not PROVIDER.is_file():
        return emit([], [{"reason": "missing provider: apps/execution/bin/execution", "test": "shell.provider"}], 1)
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
        ("docs.command", ["apps/execution/bin/execution", "--config"]),
        ("docs.config", ["stride-execution-config/v1", "max_input_bytes", "identifiers"]),
        ("docs.ownership", ["Execution", "Requirements", "Sequencing", "database", "lock"]),
        ("docs.failure", ["exit 2", "apps/execution/tests/test_shell.py", "--suite behavior"]),
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
