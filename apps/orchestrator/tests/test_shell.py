#!/usr/bin/env python3
"""Literal black-box verification for the standalone Orchestrator shell."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "orchestrator"
PROVIDER = APP / "bin" / "orchestrator"
FIXTURE = APP / "fixtures" / "orchestrator-config.json"
DOC = APP / "docs" / "shell-orc.md"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def emit(errors, failures, tests_run):
    print(compact({"errors": errors, "failures": failures, "tests_run": tests_run}))
    return 1 if errors or failures else 0


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def helper(path: Path, tag: str):
    path.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
packet=json.loads(sys.stdin.read())
counter=os.environ.get('COUNTER_FILE')
if counter:
    p=pathlib.Path(counter); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a',encoding='utf-8') as f: f.write('call\\n')
print(json.dumps({'schema':'peer/v1','status':'ok','tag':os.environ.get('TAG',''), 'packet':packet},ensure_ascii=False,separators=(',',':')))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def write_config(root: Path, helper_path: Path | None = None, *, tag="A", mutate=None, state_name="state"):
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    data = fixture()
    data["paths"]["state"] = f"../{state_name}"
    if helper_path is not None:
        data["commands"] = {
            "peer.echo": {
                "protocol": "owner-json-v1",
                "argv": [sys.executable, str(helper_path)],
                "cwd": ".",
                "env": {"TAG": tag},
                "timeout_seconds": 2.0,
                "accepted_exit_codes": [0],
            }
        }
    if mutate is not None:
        mutate(data)
    path = config_dir / "orchestrator.json"
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
    assert parsed(proc) == {"schema": "stride-orchestrator/v1", "status": "rejected", "reason": reason}, parsed(proc)


def invoke_packet(request_id="request-a", *, value="hello"):
    return {
        "schema": "stride-orchestrator/v1",
        "operation": "invoke",
        "input": {
            "request_id": request_id,
            "command_id": "peer.echo",
            "packet": {"schema": "peer-input/v1", "value": value},
        },
    }


def query_commands():
    return {
        "schema": "stride-orchestrator/v1",
        "operation": "query",
        "input": {"queries": [{"id": "commands", "kind": "commands"}]},
    }


def assert_delivered(proc, *, tag, replayed=False, value="hello"):
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stderr == "", proc.stderr
    body = parsed(proc)
    assert body["schema"] == "stride-orchestrator/v1", body
    assert body["status"] == "delivered", body
    assert body["request_id"] == "request-a", body
    assert body["replayed"] is replayed, body
    assert body["result"] == {
        "command_id": "peer.echo",
        "exit_code": 0,
        "packet": {"schema": "peer/v1", "status": "ok", "tag": tag, "packet": {"schema": "peer-input/v1", "value": value}},
        "disposition": "returned",
    }, body
    return body


def test_provider_executable():
    assert PROVIDER.is_file(), f"missing provider: {PROVIDER.relative_to(ROOT)}"
    assert os.access(PROVIDER, os.X_OK), "provider is not executable"


def test_isolated_invoke_and_poison_neighbors():
    with tempfile.TemporaryDirectory(prefix="orc-shell-isolated-") as td:
        base = Path(td)
        h = helper(base / "peer.py", "isolated")
        config, _ = write_config(base, h, tag="isolated")
        foreign = base / "foreign"
        foreign.mkdir()
        poisons = [foreign / name for name in ("requirements.sqlite", "sequencing.sqlite", "execution.sqlite", "poise.sqlite")]
        for p in poisons:
            p.write_bytes(b"foreign-private-state")
        before = {p: sha(p) for p in poisons}
        proc = run_provider(config, invoke_packet(), cwd=base)
        assert_delivered(proc, tag="isolated")
        assert (base / "state" / "orchestrator.sqlite").is_file()
        assert {p: sha(p) for p in poisons} == before


def test_two_configs_replace_command_without_source_edit():
    with tempfile.TemporaryDirectory(prefix="orc-shell-configs-") as td:
        base = Path(td)
        ha = helper(base / "peer-a.py", "A")
        hb = helper(base / "peer-b.py", "B")
        ca, _ = write_config(base / "a", ha, tag="A", state_name="state-a")
        cb, _ = write_config(base / "b", hb, tag="B", state_name="state-b")
        source_before = sha(PROVIDER)
        pa = run_provider(ca, invoke_packet("request-a"), cwd=base)
        pb = run_provider(cb, invoke_packet("request-a"), cwd=base)
        assert_delivered(pa, tag="A")
        assert_delivered(pb, tag="B")
        assert sha(PROVIDER) == source_before
        assert (base / "a" / "state-a" / "orchestrator.sqlite").is_file()
        assert (base / "b" / "state-b" / "orchestrator.sqlite").is_file()


def test_missing_required_config_is_fail_closed():
    with tempfile.TemporaryDirectory(prefix="orc-shell-missing-") as td:
        base = Path(td)
        h = helper(base / "peer.py", "A")
        config, _ = write_config(base, h, mutate=lambda d: d.pop("commands"))
        proc = run_provider(config, invoke_packet(), cwd=base)
        assert_rejected(
            proc,
            "Orchestrator config: required exact fields ['batch', 'commands', 'lock', 'paths', 'read', 'schema']; missing ['commands']; unknown []",
        )
        assert not (base / "state").exists()


def test_configured_input_limit_is_enforced():
    with tempfile.TemporaryDirectory(prefix="orc-shell-limit-") as td:
        base = Path(td)
        h = helper(base / "peer.py", "A")
        config, _ = write_config(base, h, mutate=lambda d: d["batch"].update(max_input_bytes=32))
        proc = run_provider(config, raw='{"schema":"stride-orchestrator/v1","operation":"invoke","input":{"padding":"' + ('x' * 200) + '"}}\n', cwd=base)
        assert_rejected(proc, "Orchestrator packet exceeds max_input_bytes")
        assert not (base / "state").exists()


def test_command_descriptor_is_required_and_validated():
    with tempfile.TemporaryDirectory(prefix="orc-shell-descriptor-") as td:
        base = Path(td)
        h = helper(base / "peer.py", "A")
        def mutate(data):
            del data["commands"]["peer.echo"]["timeout_seconds"]
        config, _ = write_config(base, h, mutate=mutate)
        proc = run_provider(config, invoke_packet(), cwd=base)
        assert_rejected(
            proc,
            "Orchestrator command peer.echo: required exact fields ['accepted_exit_codes', 'argv', 'cwd', 'env', 'protocol', 'timeout_seconds']; missing ['timeout_seconds']; unknown []",
        )
        assert not (base / "state").exists()


def test_commands_query_hides_descriptor_details():
    with tempfile.TemporaryDirectory(prefix="orc-shell-query-") as td:
        base = Path(td)
        h = helper(base / "peer-secret.py", "SECRET-TAG")
        config, _ = write_config(base, h, tag="SECRET-TAG")
        proc = run_provider(config, query_commands(), cwd=base)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert proc.stderr == "", proc.stderr
        body = parsed(proc)
        assert body["status"] == "read_only", body
        value = body["results"][0]["value"]
        assert value["command_ids"] == ["peer.echo"], value
        text = compact(body)
        assert str(h) not in text
        assert "SECRET-TAG" not in text
        assert "argv" not in text and "env" not in text


def test_exact_replay_does_not_reinvoke_child():
    with tempfile.TemporaryDirectory(prefix="orc-shell-replay-") as td:
        base = Path(td)
        h = helper(base / "peer.py", "A")
        counter = base / "counter.txt"
        def mutate(data):
            data["commands"]["peer.echo"]["env"]["COUNTER_FILE"] = str(counter)
        config, _ = write_config(base, h, tag="A", mutate=mutate)
        packet = invoke_packet("request-a")
        first = run_provider(config, packet, cwd=base)
        second = run_provider(config, packet, cwd=base)
        b1 = assert_delivered(first, tag="A", replayed=False)
        b2 = assert_delivered(second, tag="A", replayed=True)
        assert b1["revision"] == b2["revision"] == 1
        assert counter.read_text(encoding="utf-8").splitlines() == ["call"]
        conflict = run_provider(config, invoke_packet("request-a", value="changed"), cwd=base)
        assert_rejected(conflict, "Orchestrator request replay conflicts with saved request")
        assert counter.read_text(encoding="utf-8").splitlines() == ["call"]


BEHAVIOR = [
    ("shell.provider", test_provider_executable),
    ("shell.isolated_invoke", test_isolated_invoke_and_poison_neighbors),
    ("shell.command_replacement", test_two_configs_replace_command_without_source_edit),
    ("shell.missing_required", test_missing_required_config_is_fail_closed),
    ("shell.input_limit", test_configured_input_limit_is_enforced),
    ("shell.command_descriptor", test_command_descriptor_is_required_and_validated),
    ("shell.commands_query", test_commands_query_hides_descriptor_details),
    ("shell.replay", test_exact_replay_does_not_reinvoke_child),
]


def run_behavior():
    if not PROVIDER.is_file():
        return emit([], [{"reason": "missing provider: apps/orchestrator/bin/orchestrator", "test": "shell.provider"}], 1)
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
        ("docs.command", ["apps/orchestrator/bin/orchestrator", "--config"]),
        ("docs.config", ["stride-orchestrator-config/v1", "max_input_bytes", "commands", "timeout_seconds"]),
        ("docs.ownership", ["Orchestrator", "Requirements", "Sequencing", "Execution", "database", "lock", "public command"]),
        ("docs.boundary", ["owner-json-v1", "Role", "Session", "yield", "apps/orchestrator/tests/test_shell.py", "--suite behavior"]),
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
