from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import ast
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from poise.infrastructure.sqlite.database import Database
from poise.infrastructure.sqlite.runtime_adapter import RuntimeRegistry


def test_common_session_boundary_exists():
    try:
        from poise.application.session_establishment import SessionEstablisher
        from poise.modules.session_establishment.domain import CallerIdentity
    except ModuleNotFoundError:
        print("RD002_BOUNDARY_MISSING")
        pytest.fail("RD002_BOUNDARY_MISSING")

    assert callable(SessionEstablisher)
    assert callable(CallerIdentity.native)
    assert callable(CallerIdentity.generated)


def registry(project: dict) -> RuntimeRegistry:
    state = project["root"] / project["cfg"]["paths"]["state"]
    return RuntimeRegistry(
        Database(
            state / project["cfg"]["paths"]["database"],
            state / project["cfg"]["paths"]["lock"],
            project["cfg"]["limits"]["lock_seconds"],
            project["cfg"]["limits"]["lock_poll_seconds"],
        )
    )


def test_native_identity_is_reused_without_adapter_specific_rights(project):
    from poise.application.session_establishment import SessionEstablisher
    from poise.modules.session_establishment.domain import CallerIdentity

    owner = SessionEstablisher(registry(project), lambda: "unused-generated-id")
    first = owner.establish(CallerIdentity.native("P", "codex-session"), [])
    second = owner.establish(CallerIdentity.native("P", "codex-session"), [])

    assert first == second
    assert first.origin == "native"


def test_generated_identity_is_persisted_and_never_reinitialized(project):
    from poise.application.session_establishment import SessionEstablisher
    from poise.modules.session_establishment.domain import CallerIdentity

    calls: list[str] = []

    def generate() -> str:
        calls.append("called")
        return "generated-session-a"

    caller = CallerIdentity.generated("P", "caller-binding-a")
    first = SessionEstablisher(registry(project), generate).establish(caller, [])

    def forbidden() -> str:
        raise AssertionError("an assigned session must not be generated again")

    second = SessionEstablisher(registry(project), forbidden).establish(caller, [])
    assert first == second
    assert first.origin == "generated"
    assert calls == ["called"]


def test_generated_collision_is_retried_atomically_between_competing_registries(project):
    from poise.application.session_establishment import SessionEstablisher
    from poise.modules.session_establishment.domain import CallerIdentity

    ready = Barrier(2)

    def compete(caller_id: str, fresh: str):
        values = iter(("collision", fresh))
        first_candidate = True

        def generate() -> str:
            nonlocal first_candidate
            candidate = next(values)
            if first_candidate:
                first_candidate = False
                ready.wait()
            return candidate

        owner = SessionEstablisher(registry(project), generate)
        return owner.establish(CallerIdentity.generated("P", caller_id), [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(compete, "caller-a", "fresh-a")
        right = pool.submit(compete, "caller-b", "fresh-b")
        established = (left.result(), right.result())

    assert {item.session_id for item in established} in (
        {"collision", "fresh-a"},
        {"collision", "fresh-b"},
    )
    assert len({item.session_id for item in established}) == 2
    with sqlite3.connect(project["database"]) as connection:
        rows = connection.execute(
            "SELECT identity_key, session_id FROM runtime_bindings ORDER BY identity_key"
        ).fetchall()
    assert len(rows) == 2
    assert len({row[1] for row in rows}) == 2


def test_existing_generated_assignment_is_not_replaced_when_native_id_appears(project):
    from poise.application.session_establishment import SessionEstablisher
    from poise.modules.session_establishment.domain import CallerIdentity

    owner = SessionEstablisher(registry(project), lambda: "generated-before-native")
    generated = owner.establish(
        CallerIdentity.generated("P", "stable-caller", native_session=None), []
    )
    later_native = owner.establish(
        CallerIdentity.generated("P", "stable-caller", native_session="native-later"), []
    )

    assert later_native == generated
    assert later_native.origin == "generated"


def _direct_env(project: dict, **extra: str) -> dict[str, str]:
    removed = {
        "POISE_SESSION",
        "POISE_CALLER_BINDING",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    }
    return {
        **{key: value for key, value in os.environ.items() if key not in removed},
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "POISE_CONFIG": str(project["config_path"]),
        **extra,
    }


def _direct_call(project: dict, env: dict[str, str], packet: dict | None = None) -> dict:
    packet = packet or {
        "operation": "bootstrap",
        "input": {"task": None, "decision": None, "feedback": None, "rework_stage": None},
        "messages": [],
    }
    result = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "work"],
        input=json.dumps(packet),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _direct_work(project: dict, env: dict[str, str]) -> dict:
    return _direct_call(project, env)


def test_direct_work_without_hook_launcher_persists_generated_session(project, tmp_path):
    binding = tmp_path / "caller.json"
    env = _direct_env(project, POISE_CALLER_BINDING=str(binding))

    first = _direct_work(project, env)
    second = _direct_work(project, env)

    assert first["status"] == second["status"] == "read_only"
    assert first["session"] == second["session"]
    assert binding.is_file()
    with sqlite3.connect(project["database"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sprints").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM runtime_bindings").fetchone()[0] == 1


def test_direct_work_uses_native_codex_identity_without_hook_launcher(project):
    env = _direct_env(
        project,
        CODEX_SESSION_ID="native-codex-session",
        POISE_SESSION="must-not-substitute-native",
    )
    first = _direct_work(project, env)
    second = _direct_work(project, env)

    assert first["status"] == second["status"] == "read_only"
    assert first["session"] == second["session"] == "native-codex-session"


def test_caller_supplied_poise_session_is_not_a_supported_substitute(project):
    env = _direct_env(project, POISE_SESSION="chosen-by-caller")
    packet = {
        "operation": "bootstrap",
        "input": {"task": None, "decision": None, "feedback": None, "rework_stage": None},
        "messages": [],
    }
    result = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "work"],
        input=json.dumps(packet),
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 2
    assert "POISE_CALLER_BINDING" in result.stderr


def test_poise_session_cannot_replace_a_valid_generated_assignment(project, tmp_path):
    env = _direct_env(
        project,
        POISE_CALLER_BINDING=str(tmp_path / "generated.json"),
        POISE_SESSION="must-not-substitute-generated",
    )
    first = _direct_work(project, env)
    second = _direct_work(project, env)

    assert first["session"] == second["session"]
    assert first["session"] != "must-not-substitute-generated"


def test_native_and_generated_origins_receive_the_same_work_surface(project, tmp_path):
    environments = (
        _direct_env(project, POISE_CALLER_BINDING=str(tmp_path / "generated.json")),
        _direct_env(project, CODEX_THREAD_ID="native-thread"),
    )

    results = []
    for environment in environments:
        boot = _direct_work(project, environment)
        show = _direct_call(
            project,
            environment,
            {
                "operation": "show",
                "input": {"queries": [{"id": "messages", "kind": "messages"}]},
                "messages": [],
            },
        )
        results.append((boot, show))

    for boot, show in results:
        assert boot["status"] == "read_only"
        assert boot["task"] is None
        assert boot["result_template"] is None
        assert show["status"] == "read_only"
        assert show["results"][0]["id"] == "messages"
        assert show["results"][0]["value"]["user_messages_count"] == 0

    def without_identity(value: dict) -> dict:
        return {
            key: item
            for key, item in value.items()
            if key not in {"session", "runtime_root", "response_path"}
        }

    generated, native = results
    assert without_identity(generated[0]) == without_identity(native[0])
    assert without_identity(generated[1]) == without_identity(native[1])


def test_all_work_composition_routes_are_structurally_bound_to_one_establisher():
    root = Path(__file__).resolve().parents[2] / "src" / "poise"
    routes = (
        root / "__main__.py",
        root / "infrastructure" / "runtime_adapter.py",
        root / "infrastructure" / "hook_transport.py",
    )
    for route in routes:
        tree = ast.parse(route.read_text())
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "establish_poise" in calls, route
        assert "Poise" not in calls, route
