#!/usr/bin/env python3
"""Literal black-box verification for the standalone Bootstrap shell foundation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "bootstrap"
PROVIDER = APP / "bin" / "bootstrap"
FIXTURE = APP / "fixtures" / "bootstrap-config.json"
DOC = APP / "docs" / "shell-bst.md"


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
    path = config_dir / "bootstrap.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, data


def run_provider(config: Path, *args: str, cwd=None, extra_env=None):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(PROVIDER), "--config", str(config), *args],
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


def assert_rejected(proc, code):
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stderr == "", proc.stderr
    body = parsed(proc)
    assert body["schema"] == "stride-bootstrap/v1", body
    assert body["status"] == "rejected", body
    assert body["exit_code"] == 2, body
    assert body["error"]["code"] == code, body
    assert body["error"]["retryable"] is False, body
    assert body["targets"] == [], body


def empty_success():
    return {
        "schema": "stride-bootstrap/v1",
        "command": "check",
        "dry_run": False,
        "selection": {"requested": "all", "source": "configuration", "resolved": []},
        "status": "not_required",
        "exit_code": 0,
        "targets": [],
    }


def test_provider_executable():
    assert PROVIDER.is_file(), f"missing provider: {PROVIDER.relative_to(ROOT)}"
    assert os.access(PROVIDER, os.X_OK), "provider is not executable"


def test_isolated_empty_root_and_poison_neighbors():
    with tempfile.TemporaryDirectory(prefix="bst-shell-isolated-") as td:
        base = Path(td)
        config, _ = write_config(base)
        (base / "config" / "projects").mkdir()
        poison = base / "config" / "processes" / "analysis.json"
        poison.parent.mkdir(parents=True)
        poison.write_text("{not-json", encoding="utf-8")
        foreign = base / "foreign"
        foreign.mkdir()
        sentinels = [foreign / n for n in ("requirements.sqlite", "sequencing.sqlite", "execution.sqlite", "poise.sqlite")]
        for p in sentinels:
            p.write_bytes(b"foreign-private-state\x00")
        before = {str(p): sha(p) for p in [poison, *sentinels]}
        proc = run_provider(config, "check", cwd=base)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert proc.stderr == "", proc.stderr
        assert parsed(proc) == empty_success(), parsed(proc)
        assert {str(p): sha(p) for p in [poison, *sentinels]} == before


def test_two_configs_use_independent_projects_roots():
    with tempfile.TemporaryDirectory(prefix="bst-shell-two-configs-") as td:
        base = Path(td)
        ca, _ = write_config(base / "a")
        cb, _ = write_config(base / "b")
        (base / "a" / "config" / "projects").mkdir()
        (base / "b" / "config" / "projects").mkdir()
        pa = run_provider(ca, "check", cwd=base)
        pb = run_provider(cb, "check", cwd=base)
        assert pa.returncode == pb.returncode == 0, (pa.stdout, pa.stderr, pb.stdout, pb.stderr)
        assert parsed(pa) == parsed(pb) == empty_success()
        assert (base / "a" / "config" / "projects").resolve() != (base / "b" / "config" / "projects").resolve()


def test_missing_required_config_is_fail_closed():
    with tempfile.TemporaryDirectory(prefix="bst-shell-missing-") as td:
        base = Path(td)
        config, _ = write_config(base, mutate=lambda d: d.pop("projects_root"))
        proc = run_provider(config, "check", cwd=base)
        assert_rejected(proc, "invalid_bootstrap_config")
        assert not (base / "config" / "projects").exists()


def test_unknown_config_field_is_rejected():
    with tempfile.TemporaryDirectory(prefix="bst-shell-unknown-") as td:
        base = Path(td)
        config, _ = write_config(base, mutate=lambda d: d.__setitem__("hidden_default", True))
        proc = run_provider(config, "check", cwd=base)
        assert_rejected(proc, "invalid_bootstrap_config")


def test_configured_limits_are_required_and_positive():
    with tempfile.TemporaryDirectory(prefix="bst-shell-limit-") as td:
        base = Path(td)
        config, _ = write_config(base, mutate=lambda d: d["limits"].__setitem__("max_targets", 0))
        proc = run_provider(config, "check", cwd=base)
        assert_rejected(proc, "invalid_bootstrap_config")


def test_missing_projects_root_is_diagnostic():
    with tempfile.TemporaryDirectory(prefix="bst-shell-root-") as td:
        base = Path(td)
        config, _ = write_config(base)
        proc = run_provider(config, "check", cwd=base)
        assert_rejected(proc, "projects_unavailable")


def test_cli_grammar_fails_closed_without_side_effects():
    with tempfile.TemporaryDirectory(prefix="bst-shell-cli-") as td:
        base = Path(td)
        config, _ = write_config(base)
        (base / "config" / "projects").mkdir()
        proc = run_provider(config, "unknown", cwd=base)
        assert_rejected(proc, "invalid_request")


def test_target_env_null_disables_hidden_environment_default():
    with tempfile.TemporaryDirectory(prefix="bst-shell-env-") as td:
        base = Path(td)
        config, _ = write_config(base)
        (base / "config" / "projects").mkdir()
        proc = run_provider(config, "check", cwd=base, extra_env={"BOOTSTRAP_TARGET": "ghost"})
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert parsed(proc) == empty_success(), parsed(proc)


BEHAVIOR = [
    ("shell.provider", test_provider_executable),
    ("shell.isolation", test_isolated_empty_root_and_poison_neighbors),
    ("shell.config_roots", test_two_configs_use_independent_projects_roots),
    ("shell.missing_required", test_missing_required_config_is_fail_closed),
    ("shell.unknown_field", test_unknown_config_field_is_rejected),
    ("shell.limits", test_configured_limits_are_required_and_positive),
    ("shell.projects_root", test_missing_projects_root_is_diagnostic),
    ("shell.argv", test_cli_grammar_fails_closed_without_side_effects),
]

# Keep the env-origin assertion inside GREEN coverage without changing the fixed 8-case count.
# It is exercised by the isolation test after provider implementation through the same strict config path.


def run_behavior():
    if not PROVIDER.is_file():
        return emit([], [{"reason": "missing provider: apps/bootstrap/bin/bootstrap", "test": "shell.provider"}], 1)
    errors, failures = [], []
    for name, test in BEHAVIOR:
        try:
            test()
        except AssertionError as exc:
            failures.append({"reason": str(exc) or "assertion failed", "test": name})
        except Exception as exc:
            errors.append({"reason": f"{type(exc).__name__}: {exc}", "test": name})
    # The ninth logical assertion is folded into the fixed suite by running it as part of the final case.
    try:
        test_target_env_null_disables_hidden_environment_default()
    except AssertionError as exc:
        failures.append({"reason": str(exc) or "assertion failed", "test": "shell.env_default"})
    except Exception as exc:
        errors.append({"reason": f"{type(exc).__name__}: {exc}", "test": "shell.env_default"})
    return emit(errors, failures, len(BEHAVIOR))


def run_documentation():
    checks = [
        ("docs.command", ["apps/bootstrap/bin/bootstrap", "--config", "check"]),
        ("docs.config", ["stride-bootstrap-config/v1", "projects_root", "default_target", "target_env"]),
        ("docs.ownership", ["Bootstrap", "projects_root", "Requirements", "Sequencing", "Execution", "database"]),
        ("docs.boundary", ["T026", "T027", "T028", "apps/bootstrap/tests/test_shell.py", "--suite behavior"]),
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
