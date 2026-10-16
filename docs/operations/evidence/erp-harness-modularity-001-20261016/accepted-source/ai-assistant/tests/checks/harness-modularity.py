#!/usr/bin/env python3
"""ERP-HARNESS-MODULARITY-001: independent capabilities and real consumer smoke.

HM-01..12 cover profile/configuration contracts; HM-13/14 exercise the shared
operation. Existing complete runner/cache/CLI/MCP/provider/admission suites own
HM-15..17 regressions. Schema checks cover declared structure; semantic and
computed invariants are checked through the canonical profile owner.
"""

from __future__ import annotations

import ast
from contextlib import closing, redirect_stderr, redirect_stdout
import copy
from dataclasses import asdict, replace
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
CHECKS = ROOT / "ai-assistant/checks"
FIXTURES = Path(__file__).with_name("fixtures") / "harness-modularity"
sys.path.insert(0, str(CHECKS))
sys.path.insert(0, str(ROOT / "ai-assistant/tests/support"))
from json_schema_contract import SchemaContractError, validate_schema_contract


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fixture_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def profile_text():
    return (FIXTURES / "profile.toml").read_text(encoding="utf-8")


def profile_owner():
    return importlib.import_module("command_profile_lib")


def load_source(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def repository_fixture():
    support = load_source(
        "modularity_mcp_support", ROOT / "ai-assistant/tests/mcp/repo-checks-server.py"
    )
    temporary = support.temporary_repository()
    root = Path(temporary.name)
    shutil.copy2(FIXTURES / "command-fixture.py", root / "fixture.py")
    (root / "fixture-input.json").write_text('{"exit_code":0}', encoding="utf-8")
    text = profile_text().replace('"fixture-tool"', json.dumps(sys.executable))
    (root / "ai-assistant/checks/command-profiles.toml").write_text(text, encoding="utf-8")
    from artifact_policy_gate import active_session_id, ensure_artifact_session

    identifier = active_session_id()
    if identifier is None:
        raise RuntimeError("The fixture requires the explicitly configured active artifact session")
    ensure_artifact_session(root, identifier)
    return temporary


def run_cli(root):
    cli = importlib.import_module("run_check")
    out, error = io.StringIO(), io.StringIO()
    with patch.object(cli, "repository_root", return_value=root), redirect_stdout(out), redirect_stderr(error):
        code = cli.main(["--command-id", "modularity-fixture", "--json"])
    return code, out.getvalue(), error.getvalue()


class HarnessModularityContracts(unittest.TestCase):
    def assert_stored_run(self, ledger, check_id):
        import sqlite3

        self.assertTrue(ledger.is_file())
        with closing(sqlite3.connect(ledger)) as connection:
            actual = connection.execute(
                "SELECT command_id, status, verified_success FROM check_runs WHERE check_id = ?",
                (check_id,),
            ).fetchone()
        self.assertEqual(actual, ("modularity-fixture", "passed", 1))

    def assert_profile(self, profile, *, bound=True):
        expected = fixture_json("profile.json")
        if not bound:
            expected["evidence"] = None
        self.assertEqual(canonical(asdict(profile)), canonical(expected))

    def guarded(self, probe, *arguments, cwd=None):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(FIXTURES / "import-guard.py"),
             str(CHECKS), str(FIXTURES / probe), *map(str, arguments)],
            cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_hm01_profile_import_requires_no_runtime_or_configuration(self):
        actual = self.guarded("profile-probe.py")
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(actual.stdout, "profile-import-ok\n")

    def test_hm01b_hm11_context_guard_covers_selection_and_use(self):
        with tempfile.TemporaryDirectory() as name:
            parent = Path(name).resolve()
            subject, launch, after = (parent / item for item in ("subject", "launch", "after"))
            for path in (subject, launch, after):
                path.mkdir()
            for configured, expected, explicit in (
                (None, subject, False),
                (str(parent / "selected"), parent / "selected", True),
                ("relative-selected", launch / "relative-selected", True),
                ("", launch, True),
            ):
                with self.subTest(configured=configured):
                    actual = self.guarded(
                        "context-probe.py", subject, json.dumps(configured), after,
                        "select", cwd=launch,
                    )
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    observed = json.loads(actual.stdout)
                    self.assertEqual(observed["root"], str(expected))
                    self.assertIs(observed["explicit"], explicit)

    def test_hm01b_guard_detects_a_dependency_deferred_until_construction(self):
        actual = self.guarded("deferred-import.py")
        self.assertNotEqual(actual.returncode, 0)
        self.assertIn("forbidden dependency: sqlite3", actual.stderr)
        self.assertNotIn("probe prerequisites imported", actual.stderr)

    def test_hm02_literal_profile_contract_and_hm12_independent_loading(self):
        actual = self.guarded("profile-probe.py", FIXTURES / "profile.toml")
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(canonical(json.loads(actual.stdout)), canonical(fixture_json("profile.json")))

    def test_hm03_root_extension_does_not_change_loaded_profile(self):
        owner = profile_owner()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "profiles.toml"
            text = profile_text().replace("schema_version = 1", "schema_version = 1\nfuture = { enabled = true }")
            path.write_text(text, encoding="utf-8")
            self.assert_profile(owner.load_profiles(path).profiles[0])

    def test_hm04_every_nested_extension_is_ignored_by_its_owner(self):
        owner = profile_owner()
        raw = tomllib.loads(profile_text())
        for level in (None, "progress", "output", "prerequisites", "cache"):
            with self.subTest(level=level):
                command = copy.deepcopy(raw["commands"][0])
                target = command if level is None else command[level]
                target["future"] = {"enabled": True, "invalid_known_type": [False, "bad"]}
                self.assert_profile(owner.CommandProfile.from_raw(command, 0), bound=False)
        evidence = copy.deepcopy(raw["evidence_contracts"][0])
        evidence["future"] = {"max_files": "not an integer"}
        self.assertEqual(
            canonical(asdict(owner.EvidenceContract.from_raw(evidence, 0))),
            canonical(fixture_json("profile.json")["evidence"]),
        )

    def test_hm03_hm04_schema_ignores_extensions_at_all_declared_levels(self):
        schema = json.loads((CHECKS / "schemas/command-profiles.schema.json").read_text(encoding="utf-8"))
        raw = tomllib.loads(profile_text())
        validate_schema_contract(raw, schema)
        for location in ("root", "evidence", "command", "progress", "output", "prerequisites", "cache"):
            with self.subTest(location=location):
                value = copy.deepcopy(raw)
                if location == "root":
                    target = value
                elif location == "evidence":
                    target = value["evidence_contracts"][0]
                elif location == "command":
                    target = value["commands"][0]
                else:
                    target = value["commands"][0][location]
                target["future"] = {"enabled": "invalid if it were known", "root": [False]}
                validate_schema_contract(value, schema)

    def test_hm05_known_required_values_remain_strict_with_extensions(self):
        owner = profile_owner()
        raw = tomllib.loads(profile_text())["commands"][0]
        for key, invalid in (
            ("command_id", None), ("execution_timeout_seconds", True),
            ("execution_timeout_seconds", "ten"), ("require_parser_confirmation", 1),
            ("parser", "unknown-parser"), ("invocation", "shell"),
            ("platforms", ["unknown-platform"]), ("argv", []),
        ):
            with self.subTest(key=key, invalid=invalid):
                value = copy.deepcopy(raw)
                value["future"] = {key: "cannot replace a known value"}
                if invalid is None:
                    del value[key]
                else:
                    value[key] = invalid
                with self.assertRaises(owner.ProfileError):
                    owner.CommandProfile.from_raw(value, 0)

    def test_hm05_missing_required_profile_fields_fail_with_extensions(self):
        owner = profile_owner()
        raw = tomllib.loads(profile_text())["commands"][0]
        required_paths = (
            ("command_id",), ("workload_class",), ("runtime_class",),
            ("platforms",), ("invocation",), ("argv",), ("cwd",),
            ("parser",), ("require_parser_confirmation",),
            ("execution_timeout_seconds",), ("observed_max_success_seconds",),
            ("observed_max_progress_gap_seconds",), ("stall_timeout_seconds",),
            ("progress",), ("output",), ("prerequisites",), ("cache",),
            ("initial_limit_reason",), ("last_calibrated_at",),
            ("progress", "mode"),
            ("output", "success_bytes"), ("output", "failure_bytes"),
            ("output", "timeout_bytes"), ("output", "max_failures"),
            ("prerequisites", "executables"), ("prerequisites", "files"),
            ("prerequisites", "environment"),
            ("cache", "enabled"), ("cache", "input_paths"),
            ("cache", "environment"),
        )
        for path in required_paths:
            for extension in (False, True):
                with self.subTest(path=path, extension=extension):
                    value = copy.deepcopy(raw)
                    target = value if len(path) == 1 else value[path[0]]
                    del target[path[-1]]
                    if extension:
                        target["future"] = {path[-1]: "cannot replace the missing field"}
                    with self.assertRaisesRegex(owner.ProfileError, path[-1]):
                        owner.CommandProfile.from_raw(value, 0)

    def test_hm05_loader_rejects_missing_cache(self):
        owner = profile_owner()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "profiles.toml"
            path.write_text(profile_text().split("[commands.cache]", 1)[0], encoding="utf-8")
            with self.assertRaisesRegex(owner.ProfileError, "commands\\[0\\]\\.cache"):
                owner.load_profiles(path)

    def test_hm05_schema_retains_required_fields_and_known_types(self):
        schema = json.loads((CHECKS / "schemas/command-profiles.schema.json").read_text(encoding="utf-8"))
        raw = tomllib.loads(profile_text())
        for key, invalid in (("command_id", None), ("execution_timeout_seconds", True),
                             ("require_parser_confirmation", "yes"), ("parser", "unknown")):
            with self.subTest(key=key):
                value = copy.deepcopy(raw)
                if invalid is None:
                    del value["commands"][0][key]
                else:
                    value["commands"][0][key] = invalid
                with self.assertRaises(SchemaContractError):
                    validate_schema_contract(value, schema)

    def test_hm06_loader_rejects_unsupported_or_wrongly_typed_version(self):
        owner = profile_owner()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "profiles.toml"
            for version in ("2", "true", '"1"'):
                with self.subTest(version=version):
                    path.write_text(profile_text().replace("schema_version = 1", "schema_version = " + version), encoding="utf-8")
                    with self.assertRaises(owner.ProfileError):
                        owner.load_profiles(path)

    def test_hm06_schema_does_not_equate_boolean_true_with_version_one(self):
        schema = json.loads((CHECKS / "schemas/command-profiles.schema.json").read_text(encoding="utf-8"))
        for version in (2, True, "1"):
            with self.subTest(version=version):
                raw = tomllib.loads(profile_text())
                raw["schema_version"] = version
                with self.assertRaises(SchemaContractError):
                    validate_schema_contract(raw, schema)

    def test_hm07_semantic_combinations_fail_even_with_extensions(self):
        owner = profile_owner()
        source = tomllib.loads(profile_text())
        raw = source["commands"][0]
        for change in ("pattern", "history", "cache-limits", "calibration"):
            with self.subTest(change=change):
                value = copy.deepcopy(raw)
                value["future"] = {"enabled": "bad"}
                if change == "pattern":
                    value["progress"]["pattern"] = "known-but-unavailable"
                elif change == "history":
                    value["observed_max_progress_gap_seconds"] = 1
                elif change == "cache-limits":
                    value["cache"]["max_input_files"] = 1
                else:
                    value["observed_max_success_seconds"] = 20
                with self.assertRaises(owner.ProfileError):
                    owner.CommandProfile.from_raw(value, 0)
        evidence = copy.deepcopy(source["evidence_contracts"][0])
        evidence.update(required_globs=["same/*.txt"], optional_globs=["same/*.txt"], future=True)
        with self.assertRaises(owner.ProfileError):
            owner.EvidenceContract.from_raw(evidence, 0)

    def test_hm08_selection_is_exact_and_never_chooses_an_ambiguous_variant(self):
        owner = profile_owner()
        profiles = owner.load_profiles(FIXTURES / "profile.toml")
        selected = profiles.select("modularity-fixture", platform_name="linux")
        self.assertEqual(selected.key, ("modularity-fixture", "default", "test-host"))
        with self.assertRaises(owner.ProfileError):
            profiles.select("absent", platform_name="linux")
        variants = owner.ProfileSet((selected, replace(selected, runtime_class="other-host")))
        with self.assertRaises(owner.ProfileError):
            variants.select("modularity-fixture", platform_name="linux")

    def test_hm09_render_reload_preserves_the_literal_contract(self):
        owner = profile_owner()
        profiles = owner.load_profiles(FIXTURES / "profile.toml")
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "round-trip.toml"
            owner.write_profiles(path, profiles)
            self.assert_profile(owner.load_profiles(path).profiles[0])

    def test_hm10_file_errors_are_lazy_and_keep_original_exception_types(self):
        context_type = importlib.import_module("check_configuration").CheckConfiguration
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            context = context_type.select(root, str(root))
            self.assertEqual(context.root, root.resolve())
            self.assertIs(context.explicit, True)
            with self.assertRaises(FileNotFoundError) as absent:
                context.load_profiles()
            self.assertIsNone(absent.exception.__cause__)
            path = root / "ai-assistant/checks/command-profiles.toml"
            path.parent.mkdir(parents=True)
            path.write_text("schema_version =", encoding="utf-8")
            with self.assertRaises(tomllib.TOMLDecodeError) as malformed:
                context.load_profiles()
            self.assertIsNone(malformed.exception.__cause__)

    def test_hm12_context_profile_use_has_no_unrelated_prerequisites(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            selected = root / "selected"
            path = selected / "ai-assistant/checks/command-profiles.toml"
            path.parent.mkdir(parents=True)
            path.write_text(profile_text(), encoding="utf-8")
            after = root / "after"
            after.mkdir()
            for configured in (str(selected), "selected"):
                with self.subTest(configured=configured):
                    actual = self.guarded("context-probe.py", root, json.dumps(configured), after, "profiles", cwd=root)
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    observed = json.loads(actual.stdout)
                    self.assertEqual(observed["root"], str(selected))
                    self.assertIs(observed["explicit"], True)
                    self.assertEqual(observed["profile_key"], ["modularity-fixture", "default", "test-host"])
            self.assertFalse((selected / "ai-assistant/checks/test-packages.toml").exists())

    def test_hm03_real_cli_ignores_an_unknown_cache_extension(self):
        with repository_fixture() as name:
            root = Path(name)
            path = root / "ai-assistant/checks/command-profiles.toml"
            with path.open("a", encoding="utf-8") as stream:
                stream.write('\nfuture = { enabled = "invalid if known" }\n')
            exit_code, out, error = run_cli(root)
            self.assertEqual(exit_code, 0, error)
            result = json.loads(out)
            self.assertEqual(result["status"], "passed")
            self.assertIs(result["verified_success"], True)
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual((root / "launches.log").read_text(encoding="utf-8"), "launch\n")

    def test_hm13_service_keeps_selected_configuration_separate_from_data_owner(self):
        configuration = importlib.import_module("check_configuration").CheckConfiguration
        service_type = importlib.import_module("registered_check_service").RegisteredCheckService
        with repository_fixture() as name, tempfile.TemporaryDirectory() as config_name:
            root, selected = Path(name), Path(config_name)
            path = selected / "ai-assistant/checks/command-profiles.toml"
            path.parent.mkdir(parents=True)
            path.write_text(profile_text().replace('"fixture-tool"', json.dumps(sys.executable)), encoding="utf-8")
            # The subject's own configuration is a deliberate wrong-root input.
            (root / "ai-assistant/checks/command-profiles.toml").write_text("schema_version = 2", encoding="utf-8")
            service = service_type(configuration.select(root, str(selected)))
            # One request retains its already-loaded configuration. Re-reading
            # here would fail on this deliberately invalid subsequent value.
            path.write_text("schema_version =", encoding="utf-8")
            data = root / "ai-assistant/checks/data/declared-owner"
            profile, record, result = service.run(root, data, "modularity-fixture", platform_name="linux")
            self.assertEqual(profile.key, ("modularity-fixture", "default", "test-host"))
            self.assertEqual(result["status"], "passed")
            self.assertIs(result["verified_success"], True)
            self.assertEqual(record["exit_code"], 0)
            self.assertTrue((data / "ledger.sqlite3").is_file())
            self.assert_stored_run(data / "ledger.sqlite3", result["check_id"])
            self.assertFalse((selected / "ai-assistant/checks/data").exists())
            self.assertFalse((selected / "launches.log").exists())
            self.assertEqual((root / "launches.log").read_text(encoding="utf-8"), "launch\n")
            artifact = Path(result["artifacts"]["record_path"])
            artifact = artifact if artifact.is_absolute() else root / artifact
            self.assertTrue(artifact.is_relative_to(root))
            self.assertFalse(artifact.is_relative_to(selected))
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)

    def test_hm14_actual_cli_and_mcp_delegate_once_to_the_shared_operation(self):
        service_type = importlib.import_module("registered_check_service").RegisteredCheckService
        server_module = load_source("modularity_mcp_server", ROOT / "ai-assistant/mcp/repo-checks-server.py")
        calls = []
        actual_run = service_type.run

        def observe(instance, *arguments, **options):
            calls.append((arguments[0], arguments[1]))
            return actual_run(instance, *arguments, **options)

        with repository_fixture() as name, patch.object(service_type, "run", autospec=True, side_effect=observe):
            root = Path(name)
            exit_code, out, error = run_cli(root)
            self.assertEqual(exit_code, 0, error)
            self.assertEqual(json.loads(out)["status"], "passed")
            server = server_module.RepoChecksServer(root)
            responses = queue.Queue()
            with patch.object(server, "write", side_effect=responses.put):
                server.handle(fixture_json("rpc-initialize.json"))
                self.assertEqual(responses.get(timeout=10)["id"], 1)
                server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
                server.handle(fixture_json("rpc-run.json"))
                response = responses.get(timeout=10)
                with server.state_lock:
                    workers = list(server.workers)
                for worker in workers:
                    worker.join(timeout=10)
                    self.assertFalse(worker.is_alive())
            self.assertEqual(response["id"], 2)
            self.assertIs(response["result"]["isError"], False)
            self.assertEqual(response["result"]["structuredContent"]["status"], "passed")
            expected_data = root / "ai-assistant/checks/data"
            self.assertEqual(calls, [(root, expected_data), (root, expected_data)])
            self.assertEqual((root / "launches.log").read_text(encoding="utf-8"), "launch\nlaunch\n")

    def test_hm16_existing_success_contract_proves_the_execution_fixture(self):
        with repository_fixture() as name:
            root = Path(name)
            code, out, error = run_cli(root)
            self.assertEqual(code, 0, error)
            actual = json.loads(out)
            self.assertEqual(actual["status"], "passed")
            self.assertIs(actual["verified_success"], True)
            self.assertEqual(actual["exit_code"], 0)
            self.assert_stored_run(root / "ai-assistant/checks/data/ledger.sqlite3", actual["check_id"])
            self.assertEqual((root / "launches.log").read_text(encoding="utf-8"), "launch\n")

    def test_profile_writer_preserves_atomic_replacement_permissions_and_failure(self):
        owner = profile_owner()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "record.txt"
            owner.write_text_atomically(path, "before\n", mode=0o600)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with patch.object(owner.os, "replace", side_effect=OSError("fixture replace denied")):
                with self.assertRaisesRegex(OSError, "^fixture replace denied$"):
                    owner.write_text_atomically(path, "after\n", mode=0o600)
            self.assertEqual(path.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(sorted(item.name for item in path.parent.iterdir()), ["record.txt"])

    def test_hm18_real_consumers_import_the_canonical_profile_owner(self):
        owner = profile_owner()
        self.assertEqual(owner.CommandProfile.__module__, "command_profile_lib")
        for name in ("run_check.py", "registered_command_adoption_lib.py", "calibrate_timeout.py", "generate_runtime_limits.py"):
            with self.subTest(consumer=name):
                tree = ast.parse((CHECKS / name).read_text(encoding="utf-8"))
                imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
                self.assertIn("command_profile_lib", imported)


if __name__ == "__main__":
    # Reuse the existing session scratch owner; native runpy and CLI share this
    # entry point. unittest imports the test module without executing this block.
    raise SystemExit(subprocess.call([
        sys.executable, str(ROOT / "ai-assistant/agent/session-temp-runtime.py"),
        "--repository-root", str(ROOT), "--config",
        str(ROOT / "ai-assistant/agent/artifact-path-policy.json"), "--",
        sys.executable, "-m", "unittest", str(Path(__file__).resolve()), "-v",
    ]))
