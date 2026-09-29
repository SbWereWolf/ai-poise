#!/usr/bin/env python3
"""Literal black-box verification for the standalone Poise shell foundation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "poise"
PROVIDER = APP / "bin" / "poise"
FIXTURE = APP / "fixtures" / "poise-config.json"
DOC = APP / "docs" / "shell-poi.md"


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
    path = config_dir / "poise.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, data


def status_packet():
    return {"schema": "stride-poise/v1", "operation": "session.status", "input": {}}


def run_provider(config: Path, packet=None, *, raw=None, cwd=None,
                 binary=False, env_overrides=None):
    if raw is None:
        raw = compact(packet) + "\n"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_overrides is not None:
        env.update(env_overrides)
    return subprocess.run(
        [str(PROVIDER), "--config", str(config)],
        input=raw.encode("utf-8") if binary else raw,
        text=not binary,
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


def error(code, *, details):
    return {
        "schema": "stride-poise/v1",
        "status": "rejected",
        "error": {"code": code, "message": {
            "session_not_found": "Session was not found",
            "invalid_json": "Invalid JSON packet",
            "invalid_packet": "Invalid Poise packet",
            "limit_exceeded": "Configured limit exceeded",
        }[code], "retryable": False, "details": details},
    }


def assert_rejected(proc, code, *, details):
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stderr == "", proc.stderr
    assert parsed(proc) == error(code, details=details), parsed(proc)


def test_provider_executable():
    assert PROVIDER.is_file(), f"missing provider: {PROVIDER.relative_to(ROOT)}"
    assert os.access(PROVIDER, os.X_OK), "provider is not executable"


def test_isolated_status_and_poison_neighbors():
    with tempfile.TemporaryDirectory(prefix="poi-shell-isolated-") as td:
        base = Path(td)
        config, _ = write_config(base)
        poison = base / "config" / "processes" / "analysis.json"
        poison.parent.mkdir(parents=True)
        poison.write_text("{not-json", encoding="utf-8")
        foreign = base / "foreign"
        foreign.mkdir()
        sentinels = [foreign / n for n in ("requirements.sqlite", "sequencing.sqlite", "execution.sqlite", "orchestrator.sqlite")]
        for p in sentinels:
            p.write_bytes(b"foreign-private-state\x00")
        before = {str(p): sha(p) for p in [poison, *sentinels]}
        proc = run_provider(config, status_packet(), cwd=base)
        assert_rejected(proc, "session_not_found", details={})
        assert {str(p): sha(p) for p in [poison, *sentinels]} == before
        assert not (base / "config" / "state").exists()


def test_two_configs_are_independent():
    with tempfile.TemporaryDirectory(prefix="poi-shell-two-configs-") as td:
        base = Path(td)
        ca, _ = write_config(base / "a")
        cb, _ = write_config(base / "b")
        pa = run_provider(ca, status_packet(), cwd=base)
        pb = run_provider(cb, status_packet(), cwd=base)
        assert_rejected(pa, "session_not_found", details={})
        assert_rejected(pb, "session_not_found", details={})
        assert not (base / "a" / "config" / "state").exists()
        assert not (base / "b" / "config" / "state").exists()


def test_missing_required_config_is_fail_closed():
    with tempfile.TemporaryDirectory(prefix="poi-shell-missing-") as td:
        base = Path(td)
        config, _ = write_config(base, mutate=lambda d: d.pop("state_root"))
        proc = run_provider(config, status_packet(), cwd=base)
        assert_rejected(proc, "invalid_packet", details={"path": "/config/state_root"})
        assert not (base / "config" / "state").exists()


def test_unknown_config_field_is_rejected():
    with tempfile.TemporaryDirectory(prefix="poi-shell-unknown-") as td:
        base = Path(td)
        config, _ = write_config(base, mutate=lambda d: d.__setitem__("hidden_default", True))
        proc = run_provider(config, status_packet(), cwd=base)
        assert_rejected(proc, "invalid_packet", details={"path": "/config"})


def test_packet_limit_is_configured():
    with tempfile.TemporaryDirectory(prefix="poi-shell-limit-") as td:
        base = Path(td)
        config, _ = write_config(base, mutate=lambda d: d["limits"].update(packet_bytes=32))
        proc = run_provider(config, raw='{"schema":"stride-poise/v1","operation":"session.status","input":{"padding":"' + ('x' * 200) + '"}}\n', cwd=base)
        assert_rejected(proc, "limit_exceeded", details={"limit": "packet_bytes"})


def test_invalid_json_is_diagnostic():
    with tempfile.TemporaryDirectory(prefix="poi-shell-json-") as td:
        base = Path(td)
        config, _ = write_config(base)
        proc = run_provider(config, raw="{not-json\n", cwd=base)
        assert_rejected(proc, "invalid_json", details={})


def test_unknown_operation_is_fail_closed():
    with tempfile.TemporaryDirectory(prefix="poi-shell-operation-") as td:
        base = Path(td)
        config, _ = write_config(base)
        packet = {"schema": "stride-poise/v1", "operation": "session.ensure", "input": {}}
        proc = run_provider(config, packet, cwd=base)
        assert_rejected(proc, "invalid_packet", details={"path": "/operation"})
        packet = {"schema": "unknown-é", "operation": "session.status", "input": {}}
        expected = json.loads(
            (APP / "fixtures" / "unsupported-schema.response.json").read_text(encoding="utf-8")
        )
        for encoding in ("latin-1", "ascii"):
            proc = run_provider(
                config, packet, cwd=base, binary=True,
                env_overrides={"PYTHONIOENCODING": encoding},
            )
            assert proc.returncode == 2, (encoding, proc.returncode, proc.stdout, proc.stderr)
            assert proc.stderr == b"", (encoding, proc.stderr)
            assert proc.stdout.endswith(b"\n"), (encoding, proc.stdout)
            assert proc.stdout.count(b"\n") == 1, (encoding, proc.stdout)
            try:
                decoded = proc.stdout.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise AssertionError(
                    f"{encoding}: stdout is not UTF-8: {proc.stdout!r}"
                ) from exc
            assert json.loads(decoded) == expected, (encoding, proc.stdout)


BEHAVIOR = [
    ("shell.provider", test_provider_executable),
    ("shell.isolation", test_isolated_status_and_poison_neighbors),
    ("shell.config_roots", test_two_configs_are_independent),
    ("shell.missing_required", test_missing_required_config_is_fail_closed),
    ("shell.unknown_field", test_unknown_config_field_is_rejected),
    ("shell.packet_limit", test_packet_limit_is_configured),
    ("shell.invalid_json", test_invalid_json_is_diagnostic),
    ("shell.operation", test_unknown_operation_is_fail_closed),
]


def run_behavior():
    if not PROVIDER.is_file():
        return emit([], [{"reason": "missing provider: apps/poise/bin/poise", "test": "shell.provider"}], 1)
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
        ("docs.command", ["apps/poise/bin/poise", "--config", "session.status"]),
        ("docs.config", ["stride-poise-config/v1", "state_root", "session_store", "lock_path", "results_root"]),
        ("docs.ownership", ["Poise", "Requirements", "Sequencing", "Execution", "private", "Session"]),
        ("docs.future", ["T060", "T061", "T062", "apps/poise/tests/test_shell.py", "--suite behavior"]),
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
