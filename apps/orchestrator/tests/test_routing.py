#!/usr/bin/env python3
"""External CLI checks for explicit configured routing, with independent oracles."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "orchestrator"
FIXTURES = APP / "fixtures" / "routing"
OWNERS = ("requirements", "sequencing", "execution")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def bind(value, **values):
    """Bind explicit scenario paths/returned references in test-owned templates."""
    if isinstance(value, dict):
        return {key: bind(item, **values) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, **values) for item in value]
    return values.get(value, value) if isinstance(value, str) else value


def packet(name, **values):
    return bind(load(FIXTURES / "packets.json")[name], **values)


class Calls:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.env = dict(os.environ)
        self.env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + self.env.get("PATH", "")

    def run(self, argv, payload, *, cwd=None):
        self.count += 1
        prefix = self.root / f"{self.count:02d}"
        raw = json.dumps(payload, ensure_ascii=False) + "\n"
        invocation = {
            "argv": [str(arg) for arg in argv], "cwd": str(cwd or ROOT),
            "runtime": {"python": sys.executable, "PATH": self.env["PATH"]},
        }
        write_json(prefix.with_suffix(".invocation.json"), invocation)
        prefix.with_suffix(".stdin").write_text(raw, encoding="utf-8")
        try:
            result = subprocess.run(
                invocation["argv"], input=raw, text=True, capture_output=True,
                cwd=invocation["cwd"], env=self.env, check=False, timeout=30,
            )
        except OSError as exc:
            write_json(prefix.with_suffix(".terminal.json"), {"launch_error": str(exc)})
            raise
        prefix.with_suffix(".stdout").write_text(result.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr").write_text(result.stderr, encoding="utf-8")
        write_json(prefix.with_suffix(".terminal.json"), {"exit_code": result.returncode})
        return result


def command(entry):
    if entry == "stride":
        return [ROOT / "stride"]
    return [sys.executable, "-B", APP / "bin" / "orchestrator"]


def descriptor(argv, cwd):
    return bind(
        load(FIXTURES / "config.json")["descriptor"],
        **{"@ARGV@": [str(item) for item in argv], "@CWD@": str(cwd),
           "@PATH@": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")},
    )


def config(root, commands):
    data = copy.deepcopy(load(FIXTURES / "config.json")["orchestrator"])
    data["commands"] = commands
    return write_json(root / "config" / "orchestrator.json", data)


def receiver(root, tag):
    cwd = root / "receiver cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    counter = root / f"{tag}.calls"
    return descriptor([sys.executable, "-B", FIXTURES / "receiver.py", counter, tag], cwd), counter, cwd


def body(proc, exit_code=0):
    assert proc.returncode == exit_code, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stderr == "", proc.stderr
    value = json.loads(proc.stdout)
    assert isinstance(value, dict), value
    return value


def delivered(proc, request, owner, *, child_exit=0, replayed=False):
    value = body(proc)
    assert value["schema"] == "stride-orchestrator/v1", value
    assert value["status"] == "delivered", value
    assert value["request_id"] == request, value
    assert value["replayed"] is replayed, value
    assert value["result"]["command_id"] == owner, value
    assert value["result"]["exit_code"] == child_exit, value
    assert value["result"]["disposition"] == "returned", value
    return value["result"]["packet"]


def invoke(calls, entry, cfg, owner, inner, request, *, child_exit=0, replayed=False):
    outer = packet("invoke", **{"@REQUEST@": request, "@COMMAND@": owner, "@PACKET@": inner})
    proc = calls.run([*command(entry), "--config", cfg], outer)
    return delivered(proc, request, owner, child_exit=child_exit, replayed=replayed)


def test_entries(root, _entry):
    calls = Calls(root / "calls")
    desc, counter, child_cwd = receiver(root, "entry")
    cfg = config(root, {"peer.echo": desc})
    foreign = root / "foreign cwd"
    foreign.mkdir()
    failures = []
    for name, argv in (("root stride", [ROOT / "stride"]), ("app stride", [APP / "bin" / "stride"])):
        request = name.replace(" ", "-")
        outer = packet("invoke", **{"@REQUEST@": request, "@COMMAND@": "peer.echo",
                                    "@PACKET@": packet("echo")})
        try:
            proc = calls.run([*argv, "--config", cfg], outer, cwd=foreign)
        except FileNotFoundError as exc:
            # The missing app entry is itself the required feature; other launch errors are errors.
            if argv[0] == APP / "bin" / "stride" and not argv[0].exists():
                failures.append(f"{name}: required executable absent: {exc}")
                continue
            raise
        try:
            result = delivered(proc, request, "peer.echo")
            expected = bind(load(FIXTURES / "receiver-result.json"),
                            **{"@TAG@": "entry", "@CWD@": str(child_cwd)})
            assert result == expected, result
        except AssertionError as exc:
            failures.append(f"{name}: {exc}")
    assert not failures, failures
    assert counter.read_text().splitlines() == ["call", "call"]


def owner_setup(root):
    configs = {}
    commands = {}
    for owner in OWNERS:
        data = load(ROOT / "apps" / owner / "fixtures" / f"{owner}-config.json")
        if owner in ("sequencing", "execution"):
            data["identifiers"]["task"]["pattern"] = "^[0-9]{6}$"
        if owner == "sequencing":
            data["repository"]["path"] = str(ROOT)
            data["worktree"]["root"] = str(root / "worktrees")
            data["transfer"]["root"] = str(root / "transfer")
        configs[owner] = write_json(root / owner / "config" / f"{owner}.json", data)
        commands[owner] = descriptor(
            [sys.executable, "-B", ROOT / "apps" / owner / "bin" / owner, "--config", configs[owner]], ROOT,
        )
    return configs, commands


def direct_owner(calls, configs, owner, payload, exit_code=0):
    return body(calls.run(
        [sys.executable, "-B", ROOT / "apps" / owner / "bin" / owner, "--config", configs[owner]], payload,
    ), exit_code)


def test_example(root, _entry):
    data = load(APP / "config" / "orchestrator.example.json")
    assert set(data["commands"]) == set(OWNERS), data["commands"]
    calls = Calls(root / "calls")
    configs, _ = owner_setup(root / "owners")
    fields = {"protocol", "argv", "cwd", "env", "timeout_seconds", "accepted_exit_codes"}
    for owner in OWNERS:
        desc = data["commands"][owner]
        assert set(desc) == fields, desc
        assert desc["protocol"] == "owner-json-v1", desc
        assert len(desc["argv"]) == 4 and desc["argv"][2] == "--config", desc
        assert Path(desc["argv"][0]).is_absolute(), desc
        assert Path(desc["argv"][1]).is_absolute(), desc
        assert desc["argv"][1].endswith(f"/apps/{owner}/bin/{owner}"), desc
        assert Path(desc["argv"][3]).is_absolute(), desc
        assert Path(desc["cwd"]).is_absolute(), desc
        assert desc["env"]["PATH"], desc
        assert desc["timeout_seconds"] == 10.0, desc
        assert desc["accepted_exit_codes"] == [0, 2], desc
        # Only operator-filled path values change. Descriptor keys/protocol/limits stay the subject.
        desc["argv"][0] = sys.executable
        desc["argv"][1] = str(ROOT / "apps" / owner / "bin" / owner)
        desc["argv"][3] = str(configs[owner])
        desc["cwd"] = str(ROOT)
        desc["env"]["PATH"] = calls.env["PATH"]
    cfg = write_json(root / "copy" / "config" / "orchestrator.json", data)
    result = body(calls.run([*command("orchestrator"), "--config", cfg], packet("catalogue")))
    assert result["status"] == "read_only", result
    value = result["results"][0]["value"]
    assert value["command_ids"] == ["execution", "requirements", "sequencing"], value


def test_owners(root, entry):
    calls = Calls(root / "calls")
    configs, commands = owner_setup(root / "owners")
    cfg = config(root, commands)
    created = invoke(calls, entry, cfg, "requirements", packet("create"), "create")
    assert created["status"] == "created", created
    assert created["statement_revision"] == 1, created
    assert created["revision"] == 1 and created["replayed"] is False, created
    task_id = created["task_id"]
    assert len(task_id) == 6 and task_id.isdecimal(), task_id
    values = {"@TASK@": task_id, "@STATEMENT@": created["statement_revision"]}
    req_query = packet("requirements_query", **values)
    first = direct_owner(calls, configs, "requirements", req_query)
    assert first["status"] == "read_only" and first["revision"] == 1, first
    expected = bind(load(FIXTURES / "statement.json"), **values)
    assert first["results"] == expected, first
    for owner, reason in (("sequencing", "task_not_found"), ("execution", "execution_not_found")):
        absent = direct_owner(calls, configs, owner, packet(f"{owner}_query", **values), 2)
        assert absent == {"schema": f"stride-{owner}/v1", "status": "rejected", "reason": reason}, absent
    initial = direct_owner(calls, configs, "sequencing", packet("sequencing_view"))
    assert initial["status"] == "read_only" and initial["revision"] == 0, initial
    assert initial["results"][0]["value"]["task_ids"] == [], initial
    # Retain this actual revision0 before the independently selected registration.
    registered = invoke(calls, entry, cfg, "sequencing", packet("register", **values), "register")
    assert registered["schema"] == "stride-sequencing/v1", registered
    assert registered["status"] == "applied" and registered["revision"] == 1, registered
    registered_read = direct_owner(calls, configs, "sequencing", packet("sequencing_query", **values))
    assert registered_read["status"] == "read_only" and registered_read["revision"] == 1
    task = registered_read["results"][0]["value"]
    assert task["task_id"] == task_id and task["requirements_statement_revision"] == 1, task
    assert task["state"] == "available", task
    assert all(task[name] is None for name in ("sprint_id", "claim", "workspace", "terminal")), task
    no_execution = invoke(calls, entry, cfg, "execution", packet("execution_query", **values),
                          "execution-query", child_exit=2)
    assert no_execution == load(FIXTURES / "execution-absent.json"), no_execution
    later = packet("register", **values)
    later["input"]["request_id"] = "owner-later-registration"
    rejected = invoke(calls, entry, cfg, "sequencing", later, "later-registration", child_exit=2)
    assert rejected == load(FIXTURES / "revision-conflict.json"), rejected
    after = direct_owner(calls, configs, "requirements", req_query)
    assert after == first, (first, after)


def test_replacement(root, entry):
    calls = Calls(root / "calls")
    desc_a, count_a, cwd_a = receiver(root, "A")
    desc_b, count_b, cwd_b = receiver(root, "B")
    cfg = config(root, {"peer.echo": desc_a})
    for replayed in (False, True):
        result = invoke(calls, entry, cfg, "peer.echo", packet("echo"), "first", replayed=replayed)
        assert result == bind(load(FIXTURES / "receiver-result.json"),
                              **{"@TAG@": "A", "@CWD@": str(cwd_a)}), result
    assert count_a.read_text().splitlines() == ["call"]
    assert not count_b.exists()
    data = load(cfg)
    data["commands"]["peer.echo"] = desc_b
    write_json(cfg, data)
    result = invoke(calls, entry, cfg, "peer.echo", packet("echo"), "second")
    assert result == bind(load(FIXTURES / "receiver-result.json"),
                          **{"@TAG@": "B", "@CWD@": str(cwd_b)}), result
    old = packet("invoke", **{"@REQUEST@": "first", "@COMMAND@": "peer.echo", "@PACKET@": packet("echo")})
    conflict = body(calls.run([*command(entry), "--config", cfg], old), 2)
    assert conflict == load(FIXTURES / "replay-conflict.json"), conflict
    assert count_a.read_text().splitlines() == ["call"]
    assert count_b.read_text().splitlines() == ["call"]


def run(root, suite, entry):
    cases = [("C1", test_entries), ("C2", test_example), ("C3", test_owners), ("C4", test_replacement)]
    selected = cases[:2] if suite == "red" else cases[2:] if suite == "preservation" else cases
    errors, failures = [], []
    for name, check in selected:
        case_root = root / name
        case_root.mkdir(parents=True)
        try:
            check(case_root, entry)
        except AssertionError as exc:
            failures.append({"test": name, "reason": str(exc)})
        except Exception as exc:
            errors.append({"test": name, "reason": f"{type(exc).__name__}: {exc}"})
    result = {"errors": errors, "failures": failures, "tests_run": len(selected)}
    write_json(root / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 1 if errors or failures else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, choices=("red", "preservation", "behavior"))
    parser.add_argument("--entry", choices=("stride", "orchestrator"), default="stride")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    if args.evidence_dir is not None:
        args.evidence_dir.mkdir(parents=True, exist_ok=False)
        return run(args.evidence_dir.resolve(), args.suite, args.entry)
    with tempfile.TemporaryDirectory(prefix="orchestrator-routing-") as td:
        return run(Path(td), args.suite, args.entry)


if __name__ == "__main__":
    raise SystemExit(main())
