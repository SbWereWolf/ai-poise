"""Independent catalog/v2 observations against the accepted component wheel."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import venv
import zipfile


WHEEL_SHA256 = "6b96c53b1e189665e4922252ee1b80b2bef9c64ceec214dc557884bec4fac5fc"
ACTION_FIXTURE = Path(__file__).with_name("fixtures") / "state_action.py"


class AcceptedComponentCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        wheel_name = os.environ.get("ERP_P001_COMPONENT_WHEEL")
        if not wheel_name:
            raise AssertionError("ERP_P001_COMPONENT_WHEEL must identify the accepted wheel")
        wheel = Path(wheel_name)
        if not wheel.is_file():
            raise AssertionError(f"accepted wheel is missing: {wheel}")
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
            raise AssertionError("accepted wheel digest differs from the Task lock")
        cls.temporary = tempfile.TemporaryDirectory(prefix="component-cli-test-")
        cls.venv_root = Path(cls.temporary.name) / "venv"
        venv.EnvBuilder(with_pip=False).create(cls.venv_root)
        cls.python = cls.venv_root / "bin/python"
        probe = subprocess.run(
            [str(cls.python), "-I", "-B", "-c", "import site; print(site.getsitepackages()[0])"],
            capture_output=True, text=True, check=True,
        )
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(Path(probe.stdout.strip()))
        version = subprocess.run(
            [str(cls.python), "-I", "-B", "-c", "import importlib.metadata as m; print(m.version('environment-maintenance'))"],
            capture_output=True, text=True, check=True,
        )
        if version.stdout.strip() != "0.2.0":
            raise AssertionError("installed component version is not the accepted 0.2.0")

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def setUp(self) -> None:
        self.case = tempfile.TemporaryDirectory(prefix="component-case-")
        self.addCleanup(self.case.cleanup)
        self.root = Path(self.case.name)
        self.catalog_dir = self.root / "catalog"
        self.catalog_dir.mkdir()
        self.state = self.root / "state.txt"
        self.log = self.root / "actions.jsonl"

    def action(self, role: str, *, fail_action: str = "none", limit: int = 4096,
               bound: float = 3, env: dict[str, str] | None = None) -> dict:
        return {
            "argv": [str(self.python), "-I", "-B", str(ACTION_FIXTURE), role,
                     str(self.state), str(self.log), "required", fail_action],
            "cwd": ".",
            "env": env or {},
            "output": {"schema": "environment-maintenance/action-result/v2"},
            "max_output_bytes": limit,
            "execution_bound_seconds": bound,
        }

    def write_catalog(self, *, fail_action: str = "none", limit: int = 4096,
                      bound: float = 3, parameters: dict | None = None,
                      inherit_env: list[str] | None = None,
                      action_env: dict[str, str] | None = None) -> None:
        manifest = {
            "schema": "environment-maintenance/catalog/v2",
            "parameters": parameters or {},
            "inherit_env": inherit_env or [],
            "requirements": [{
                "id": "required",
                "check": self.action("check", fail_action=fail_action, limit=limit,
                                     bound=bound, env=action_env),
                "install": self.action("install", fail_action=fail_action, env=action_env),
                "repair": self.action("repair", fail_action=fail_action, env=action_env),
            }],
        }
        (self.catalog_dir / "catalog.json").write_text(json.dumps(manifest), encoding="utf-8")

    def invoke(self, operation: str, *options: str,
               extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for name in ("P001_VALUE", "P001_PARENT", "P001_DENIED"):
            env.pop(name, None)
        env.update(extra_env or {})
        return subprocess.run(
            [str(self.python), "-I", "-B", "-m", "environment_maintenance", operation,
             "--catalog-dir", str(self.catalog_dir), *options],
            capture_output=True, cwd=self.root, env=env,
        )

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_check_and_dry_run_never_mutate_missing_or_invalid(self) -> None:
        self.write_catalog()
        for state in ("missing", "invalid"):
            for operation, options in (("check", ()), ("run", ("--dry-run",))):
                with self.subTest(state=state, operation=operation):
                    self.state.write_text(state, encoding="utf-8")
                    self.log.unlink(missing_ok=True)
                    result = self.invoke(operation, *options)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stdout)["status"], "action_required")
                    self.assertEqual([call["action"] for call in self.calls()], ["check"])
                    self.assertEqual(self.state.read_text(), state)

    def test_satisfied_requires_no_mutation(self) -> None:
        self.state.write_text("satisfied", encoding="utf-8")
        self.write_catalog()
        result = self.invoke("run")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "ready")
        self.assertEqual([call["action"] for call in self.calls()], ["check"])

    def test_missing_installs_and_invalid_repairs_then_rechecks(self) -> None:
        self.write_catalog()
        for initial, mutation in (("missing", "install"), ("invalid", "repair")):
            with self.subTest(initial=initial):
                self.state.write_text(initial, encoding="utf-8")
                self.log.unlink(missing_ok=True)
                result = self.invoke("run")
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["status"], "ready")
                self.assertEqual([call["action"] for call in self.calls()],
                                 ["check", mutation, "check"])
                self.assertEqual(self.state.read_text(), "satisfied")

    def test_failed_mutation_still_rechecks(self) -> None:
        for initial, mutation in (("missing", "install"), ("invalid", "repair")):
            with self.subTest(initial=initial, mutation=mutation):
                self.state.write_text(initial, encoding="utf-8")
                self.log.unlink(missing_ok=True)
                self.write_catalog(fail_action=mutation)
                result = self.invoke("run")
                self.assertEqual(result.returncode, 4)
                self.assertEqual(json.loads(result.stdout)["status"], "failed")
                self.assertEqual([call["action"] for call in self.calls()],
                                 ["check", mutation, "check"])
                self.assertEqual(self.state.read_text(), initial)

    def test_unavailable_preserves_cause_and_recommendations_without_mutation(self) -> None:
        self.state.write_text("unavailable", encoding="utf-8")
        self.write_catalog()
        result = self.invoke("run")
        packet = json.loads(result.stdout)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(packet["status"], "unavailable")
        self.assertEqual(packet["requirements"][0]["id"], "required")
        self.assertEqual(packet["requirements"][0]["state"], "unavailable")
        self.assertEqual(packet["requirements"][0]["cause"], "repository is offline")
        self.assertEqual(packet["requirements"][0]["recommendations"],
                         ["restore repository access"])
        self.assertEqual([call["action"] for call in self.calls()], ["check"])

    def test_parameter_precedence_and_environment_allowlist(self) -> None:
        self.state.write_text("satisfied", encoding="utf-8")
        self.write_catalog(
            parameters={"value": {"type": "string", "default": "default", "env": "P001_VALUE"}},
            inherit_env=["P001_PARENT", "P001_VALUE"],
            action_env={"TOKEN": "${value}"},
        )
        first = self.root / "first.json"
        second = self.root / "second.json"
        first.write_text('{"value":"first"}', encoding="utf-8")
        second.write_text('{"value":"second"}', encoding="utf-8")
        values = ("--values", str(first), "--values", str(second))
        cases = (
            ((), {}, "default"),
            (("--values", str(first)), {}, "first"),
            (values, {}, "second"),
            (values, {"P001_VALUE": "environment"}, "environment"),
            (values + ("--set", "value=cli"), {"P001_VALUE": "environment"}, "cli"),
        )
        for options, chosen_env, expected in cases:
            with self.subTest(expected=expected):
                self.log.unlink(missing_ok=True)
                result = self.invoke(
                    "check", *options,
                    extra_env={**chosen_env, "P001_PARENT": "parent", "P001_DENIED": "denied"},
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["status"], "ready")
                self.assertEqual(self.calls(), [{
                    "action": "check", "cwd": str(self.catalog_dir), "token": expected,
                    "parent": "parent", "denied": None,
                }])

        self.log.unlink(missing_ok=True)
        self.write_catalog(
            parameters={"value": {"type": "string", "default": "default"}},
            inherit_env=["P001_PARENT"],
            action_env={"TOKEN": "${value}", "P001_PARENT": "catalog-override"},
        )
        result = self.invoke("check", extra_env={"P001_PARENT": "parent"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [{
            "action": "check", "cwd": str(self.catalog_dir), "token": "default",
            "parent": "catalog-override", "denied": None,
        }])

    def test_output_and_time_bounds_are_protocol_failures(self) -> None:
        self.state.write_text("satisfied", encoding="utf-8")
        for limit, bound, fail_action in ((5, 3, "none"), (4096, 0.05, "slow")):
            with self.subTest(limit=limit, bound=bound):
                self.log.unlink(missing_ok=True)
                self.write_catalog(limit=limit, bound=bound, fail_action=fail_action)
                result = self.invoke("check")
                self.assertEqual(result.returncode, 4)
                packet = json.loads(result.stdout)
                self.assertEqual(packet["status"], "failed")
                self.assertIn("protocol_failure", result.stdout.decode())
                self.assertEqual([call["action"] for call in self.calls()], ["check"])

    def test_malformed_catalog_rejects_before_action(self) -> None:
        self.write_catalog()
        manifest = json.loads((self.catalog_dir / "catalog.json").read_text())
        manifest["requirements"][0]["apply"] = manifest["requirements"][0]["install"]
        (self.catalog_dir / "catalog.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = self.invoke("check")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotEqual(result.stderr, b"")
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
