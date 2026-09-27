"""Public Bootstrap boundary tests; the component is an independent test double."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import venv


ROOT = Path(__file__).resolve().parents[2]
LOCK = {
    "schema": "ai-poise/bootstrap-component/v1",
    "distribution": "environment-maintenance",
    "version": "0.2.0",
    "accepted_commit": "a980fcd54d0958e0a491efb385ede406453d6899",
    "wheel_sha256": "6b96c53b1e189665e4922252ee1b80b2bef9c64ceec214dc557884bec4fac5fc",
    "delivery_manifest_sha256": "3bfab74a2d5c38a6e09880b9aa4e45437a0b119f0cdda56b1de0946fe68e029a",
}
ERROR_FIELDS = {
    "schema", "source", "status", "code", "command", "cause",
    "recommendations", "exit_code",
}
COMPONENT_FIXTURE = Path(__file__).with_name("fixtures") / "component_main.py"


class BootstrapBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        if not (ROOT / "bootstrap").is_file() or not (ROOT / "apps/bootstrap/component.json").is_file():
            self.skipTest("Bootstrap implementation has not created its entrypoint and lock")
        self.temporary = tempfile.TemporaryDirectory(prefix="bootstrap-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.layout = self.root / "layout"
        self.layout.mkdir()
        shutil.copy2(ROOT / "bootstrap", self.layout / "bootstrap")
        shutil.copytree(ROOT / "apps/bootstrap", self.layout / "apps/bootstrap")
        self.venv_root = self.root / "venv"
        venv.EnvBuilder(with_pip=False).create(self.venv_root)
        self.python = self.venv_root / "bin/python"
        probe = subprocess.run(
            [str(self.python), "-I", "-B", "-c", "import site; print(site.getsitepackages()[0])"],
            capture_output=True, text=True, check=True,
        )
        self.site_packages = Path(probe.stdout.strip())
        self.calls = self.root / "calls.jsonl"
        self.install_component()

    def install_component(self, *, version: str = "0.2.0", module: bool = True) -> None:
        metadata = self.site_packages / f"environment_maintenance-{version}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: environment-maintenance\nVersion: {version}\n",
            encoding="utf-8",
        )
        if module:
            package = self.site_packages / "environment_maintenance"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            shutil.copy2(COMPONENT_FIXTURE, package / "__main__.py")

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env["BOOTSTRAP_TEST_CALLS"] = str(self.calls)
        return subprocess.run(
            [str(self.python), "-I", "-B", str(self.layout / "bootstrap"), *arguments],
            capture_output=True, cwd=self.layout, env=env,
        )

    def assert_bootstrap_error(
        self, result: subprocess.CompletedProcess[bytes], code: str, exit_code: int,
        command: str | None,
    ) -> None:
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout.count(b"\n"), 1)
        packet = json.loads(result.stdout)
        self.assertEqual(set(packet), ERROR_FIELDS)
        self.assertEqual(packet["schema"], "ai-poise/bootstrap-error/v1")
        self.assertEqual(packet["source"], "bootstrap")
        self.assertEqual(packet["status"], "error")
        self.assertEqual(packet["code"], code)
        self.assertEqual(packet["command"], command)
        self.assertEqual(packet["exit_code"], exit_code)
        self.assertIsInstance(packet["cause"], str)
        self.assertTrue(packet["cause"])
        self.assertIsInstance(packet["recommendations"], list)
        self.assertTrue(packet["recommendations"])
        self.assertTrue(all(isinstance(value, str) and value for value in packet["recommendations"]))
        self.assertFalse(self.calls.exists(), "component action ran despite Bootstrap failure")

    def test_component_lock_records_exact_accepted_distribution(self) -> None:
        self.assertEqual(json.loads((self.layout / "apps/bootstrap/component.json").read_text()), LOCK)

    def test_public_verbs_forward_to_one_external_command_with_ordered_arguments(self) -> None:
        for public, external in (("infra", "run"), ("deps", "run"), ("check", "check")):
            with self.subTest(public=public):
                result = self.invoke(
                    public, "--catalog-dir", "/selected/catalog", "--values", "first.json",
                    "--set", "x=1", "--values", "second.json", "--dry-run",
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, b"")
                self.assertEqual(
                    result.stdout,
                    b'{"schema":"environment-maintenance/result/v2","status":"ready"}\n',
                )
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                self.assertEqual(
                    calls[-1],
                    [external, "--catalog-dir", "/selected/catalog", "--values", "first.json",
                     "--set", "x=1", "--values", "second.json", "--dry-run"],
                )

    def test_external_stdout_stderr_and_exit_are_unmodified(self) -> None:
        result = self.invoke("infra", "--catalog-dir", "exit-seven")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"external stdout\n")
        self.assertEqual(result.stderr, b"external stderr\n")

    def test_catalog_selection_is_explicit(self) -> None:
        for arguments in ((), ("infra",), ("deps",), ("check",)):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertNotEqual(result.stderr, b"")
        self.assertFalse(self.calls.exists())

    def test_missing_and_invalid_locks_fail_before_component_action(self) -> None:
        lock_path = self.layout / "apps/bootstrap/component.json"
        for contents, code in ((None, "component_lock_missing"), ("{}", "component_lock_invalid"),
                               ('{"schema":"a","schema":"b"}', "component_lock_invalid"),
                               ("not JSON", "component_lock_invalid")):
            with self.subTest(contents=contents):
                if contents is None:
                    lock_path.unlink(missing_ok=True)
                else:
                    lock_path.write_text(contents, encoding="utf-8")
                self.assert_bootstrap_error(self.invoke("check", "--catalog-dir", "/selected"), code, 2, "check")

    def test_missing_distribution_and_module_are_distinct_preflight_failures(self) -> None:
        for metadata in self.site_packages.glob("environment_maintenance-*.dist-info"):
            shutil.rmtree(metadata)
        self.assert_bootstrap_error(
            self.invoke("check", "--catalog-dir", "/selected"), "component_missing", 3, "check",
        )
        self.install_component(module=False)
        shutil.rmtree(self.site_packages / "environment_maintenance")
        self.assert_bootstrap_error(
            self.invoke("check", "--catalog-dir", "/selected"), "component_missing", 3, "check",
        )

    def test_wrong_distribution_version_is_rejected(self) -> None:
        shutil.rmtree(self.site_packages / "environment_maintenance-0.2.0.dist-info")
        self.install_component(version="0.1.1")
        self.assert_bootstrap_error(
            self.invoke("infra", "--catalog-dir", "/selected"), "component_version_mismatch", 3, "infra",
        )

    def test_component_signal_discards_partial_output(self) -> None:
        result = self.invoke("deps", "--catalog-dir", "signal")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stderr, b"")
        packet = json.loads(result.stdout)
        self.assertEqual(packet["code"], "component_signaled")
        self.assertEqual(packet["command"], "deps")
        self.assertEqual(packet["exit_code"], 4)
        self.assertIn("15", packet["cause"])


class BootstrapPresenceTest(unittest.TestCase):
    def test_public_entrypoint_and_component_lock_exist(self) -> None:
        self.assertTrue((ROOT / "bootstrap").is_file(), "public bootstrap entrypoint is missing")
        self.assertTrue((ROOT / "apps/bootstrap/component.json").is_file(), "component lock is missing")


if __name__ == "__main__":
    unittest.main()
