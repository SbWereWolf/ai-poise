#!/usr/bin/env python3
"""Focused contracts for registered-check input fingerprints."""

from __future__ import annotations

from pathlib import Path
import copy
import json
import sys
import subprocess
import tempfile
import traceback
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ai-assistant" / "checks"))

from check_configuration import CheckConfiguration
from command_profile_lib import (
    CacheProfile,
    ProfileError,
    ProfileSet,
    load_profiles,
)
from check_runner_lib import (
    RunnerError,
    canonical_model_visible_json,
    check_prerequisites,
    explicit_input_fingerprint,
    fit_model_visible_result,
    profile_input_fingerprint,
)
from registered_command_adoption_lib import ActiveRegistry, Candidate  # noqa: E402
import run_check as runner_cli  # noqa: E402
import registered_check_service as registered_service  # noqa: E402


CACHE_HIT = {
    "eligible": True,
    "hit": True,
    "stored": False,
    "key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "source_check_id": "chk_20260825T000000Z_0123456789",
    "result_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "created_at": "2026-08-25T00:00:00Z",
    "lookup_duration_ms": 3,
    "reason": "hit",
}


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True)


def repository() -> Path:
    root = Path(tempfile.mkdtemp())
    git(root, "init", "-q")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "Tests")
    return root


def test_model_visible_result_uses_complete_or_versioned_overflow_union() -> None:
    complete = {
        "schema_version": 1,
        "check_id": "chk_20260825T000000Z_0123456789",
        "command_id": "fixture-check",
        "workload_class": "default",
        "runtime_class": "test-host",
        "status": "passed",
        "verified_success": True,
        "counts": {"tests": 3, "assertions": 3},
        "summary": "Я" * 4_000,
        "artifacts": {
            "record_path": ".runtime/checks/chk_20260825T000000Z_0123456789/result/record.json",
        },
        "task_binding": {"status": "bound", "changed": True},
        "cache": copy.deepcopy(CACHE_HIT),
    }
    assert fit_model_visible_result(complete, 16_384) == complete

    expected_overflow = {
        "schema_version": 1,
        "result_kind": "overflow",
        "overflow_schema_version": 1,
        "check_id": "chk_20260825T000000Z_0123456789",
        "command_id": "fixture-check",
        "workload_class": "default",
        "runtime_class": "test-host",
        "status": "passed",
        "verified_success": True,
        "counts": {"tests": 3, "assertions": 3},
        "task_binding": {"status": "bound", "changed": True},
        "output_budget_bytes": 1024,
        "recovery_path": ".runtime/checks/chk_20260825T000000Z_0123456789/result/record.json",
        "cache": CACHE_HIT,
    }
    fitted = fit_model_visible_result(complete, 1024)
    assert fitted == expected_overflow
    assert len(canonical_model_visible_json(fitted)) <= 1024
    fitted["cache"]["hit"] = False
    assert complete["cache"] == CACHE_HIT

    try:
        fit_model_visible_result(complete, 20)
    except RunnerError as error:
        assert "minimal overflow envelope" in str(error)
    else:
        raise AssertionError("an oversized minimal overflow envelope must fail explicitly")


def overflow_candidate() -> dict:
    return {
        "schema_version": 1,
        "check_id": "chk_20260825T000000Z_0123456789",
        "command_id": "fixture-check",
        "workload_class": "default",
        "runtime_class": "test-host",
        "status": "passed",
        "verified_success": True,
        "counts": {"tests": 3, "assertions": 3},
        "summary": "Я" * 4000,
        "artifacts": {
            "record_path": ".runtime/checks/chk_20260825T000000Z_0123456789/result/record.json",
        },
        "cache": copy.deepcopy(CACHE_HIT),
    }


def test_overflow_preserves_non_hit_states_without_upgrading_proof() -> None:
    failures = []
    for eligible, reason, status, verified in (
        (True, "not_found", "passed", True),
        (False, "profile_disabled", "passed", True),
        (True, "entry_invalid", "passed", True),
        (True, "not_found", "failed", False),
        (True, "not_found", "unconfirmed", False),
    ):
        cache = {
            "eligible": eligible,
            "hit": False,
            "stored": False,
            "key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" if eligible else None,
            "source_check_id": None,
            "result_hash": None,
            "created_at": None,
            "lookup_duration_ms": 0,
            "reason": reason,
        }
        value = overflow_candidate()
        value.update(status=status, verified_success=verified, cache=copy.deepcopy(cache))
        reduced = fit_model_visible_result(value, 1024)
        expected = {
            "schema_version": 1,
            "result_kind": "overflow",
            "overflow_schema_version": 1,
            "check_id": "chk_20260825T000000Z_0123456789",
            "command_id": "fixture-check",
            "workload_class": "default",
            "runtime_class": "test-host",
            "status": status,
            "verified_success": verified,
            "counts": {"tests": 3, "assertions": 3},
            "output_budget_bytes": 1024,
            "recovery_path": ".runtime/checks/chk_20260825T000000Z_0123456789/result/record.json",
            "cache": cache,
        }
        try:
            assert reduced == expected
        except AssertionError:
            failures.append((eligible, reason, status, verified))
        assert len(canonical_model_visible_json(reduced)) <= 1024
    assert failures == [], f"non-hit state projection failures: {failures}"


def test_overflow_requires_actual_cache_metadata() -> None:
    failures = []
    for label, invalid in (("absent", None), ("null", None), ("boolean", False), ("string", "hit")):
        value = overflow_candidate()
        if label == "absent":
            del value["cache"]
        else:
            value["cache"] = invalid
        try:
            fit_model_visible_result(value, 1024)
        except RunnerError as error:
            assert str(error) == "model-visible overflow requires cache metadata"
        else:
            failures.append(label)
    assert failures == [], f"missing admission metadata must fail explicitly: {failures}"


def test_overflow_does_not_drop_cache_to_fit_a_smaller_budget() -> None:
    try:
        fit_model_visible_result(overflow_candidate(), 512)
    except RunnerError as error:
        assert "minimal overflow envelope cannot fit output budget 512" in str(error)
    else:
        raise AssertionError("insufficient budget must fail rather than discard admission proof")


def test_overflow_schema_requires_cache_proof() -> None:
    schema = json.loads((ROOT / "ai-assistant" / "checks" / "schemas" / "check-result.schema.json").read_text())
    assert "cache" in schema["allOf"][0]["then"]["required"]


def test_direct_runner_releases_its_adoption_lock_after_terminal_result() -> None:
    profile = load_profiles(ROOT / "ai-assistant" / "checks" / "command-profiles.toml").select(
        "repository-documentation-verification",
        platform_name="linux",
        workload_class="default",
        runtime_class="posix-host",
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        registry = ActiveRegistry(root / ".runtime" / "registered-command-adoption" / "state.sqlite3")
        assert registry.claim(
            scope_key="scope-test",
            session_id="session-test",
            turn_id="turn-test",
            tool_use_id="tool-test",
            selected_transport="direct-fallback",
            candidate=Candidate.from_profile(profile),
        ) is None
        with (
            patch.object(runner_cli, "repository_root", return_value=root),
            patch.object(CheckConfiguration, "load_profiles", return_value=ProfileSet((profile,))),
            patch.object(registered_service, "runtime_calibrated_profile", side_effect=lambda selected, ledger: selected),
            patch.object(registered_service, "run_registered_command", return_value=(
                {"check_id": "chk_terminal"}, {"status": "passed", "check_id": "chk_terminal"},
            )),
        ):
            assert runner_cli.main(
                [
                    "--command-id",
                    profile.command_id,
                    "--adoption-scope-key",
                    "scope-test",
                    "--adoption-tool-use-id",
                    "tool-test",
                    "--json",
                ]
            ) == 0
        assert registry.current("scope-test") is None


def test_tracked_markdown_scope_ignores_ignored_tree() -> None:
    root = repository()
    (root / ".gitignore").write_text("tasks/\n", encoding="utf-8")
    (root / "README.md").write_text("# Root\n", encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    tracked = docs / "tracked.md"
    tracked.write_text("# Tracked\n", encoding="utf-8")
    ignored = root / "tasks" / "ignored"
    ignored.mkdir(parents=True)
    (ignored / "ignored.md").write_text("# Ignored\n", encoding="utf-8")
    git(root, "add", ".gitignore", "README.md", "docs/tracked.md")
    git(root, "commit", "-qm", "initial")

    cache = CacheProfile(
        False,
        (),
        (),
        input_selection="git-tracked-markdown",
        max_input_files=4096,
        max_input_bytes=67_108_864,
    )
    first = profile_input_fingerprint(root, cache)
    assert {entry["path"] for entry in first["entries"]} == {
        "README.md",
        "docs/tracked.md",
    }
    tracked.write_text("# Changed\n", encoding="utf-8")
    assert profile_input_fingerprint(root, cache)["sha256"] != first["sha256"]


def test_tracked_markdown_scope_is_bounded_and_fails_closed() -> None:
    root = repository()
    (root / "README.md").write_text("# Root\n", encoding="utf-8")
    (root / "SECOND.md").write_text("# Second\n", encoding="utf-8")
    git(root, "add", "README.md", "SECOND.md")
    git(root, "commit", "-qm", "initial")

    for cache, expected in (
        (
            CacheProfile(False, (), (), "git-tracked-markdown", 1, 67_108_864),
            "max_input_files",
        ),
        (
            CacheProfile(False, (), (), "git-tracked-markdown", 4096, 1),
            "max_input_bytes",
        ),
    ):
        try:
            profile_input_fingerprint(root, cache)
        except RunnerError as error:
            assert expected in str(error)
            assert "ai-assistant/checks/command-profiles.toml" in str(error)
        else:
            raise AssertionError("tracked Markdown input bounds must fail closed")


def test_generic_glob_scope_is_unchanged() -> None:
    root = repository()
    (root / ".gitignore").write_text(".runtime/\n", encoding="utf-8")
    (root / "README.md").write_text("# Root\n", encoding="utf-8")
    runtime = root / ".runtime"
    runtime.mkdir()
    (runtime / "ignored.md").write_text("# Ignored\n", encoding="utf-8")
    git(root, "add", ".gitignore", "README.md")
    git(root, "commit", "-qm", "initial")

    expected = explicit_input_fingerprint(root, ("**/*.md",))
    actual = profile_input_fingerprint(root, CacheProfile(False, ("**/*.md",), ()))
    assert actual == expected


def test_documentation_profile_disables_repository_wide_markdown_cache() -> None:
    profiles = load_profiles(ROOT / "ai-assistant" / "checks" / "command-profiles.toml")
    profile = profiles.select(
        "repository-documentation-verification",
        platform_name="linux",
        workload_class="default",
    )
    windows_profile = profiles.select(
        "repository-documentation-verification",
        platform_name="windows",
        workload_class="default",
    )
    assert profile.cache.enabled is False
    assert profile.cache.input_selection == "filesystem-glob"
    assert profile.cache.input_paths == ()
    assert profile.cache.max_input_files is None
    assert profile.cache.max_input_bytes is None
    assert profile.prerequisites.executables == ("bash", "git")
    assert windows_profile.prerequisites.executables == ("cmd.exe", "git")

def test_documentation_profile_classifies_missing_git_as_dependency() -> None:
    profile = load_profiles(ROOT / "ai-assistant" / "checks" / "command-profiles.toml").select(
        "repository-documentation-verification",
        platform_name="linux",
        workload_class="default",
    )
    with patch(
        "check_runner_lib.shutil.which",
        side_effect=lambda executable: None if executable == "git" else "/bin/tool",
    ):
        report = check_prerequisites(profile, ROOT, configuration=CheckConfiguration.select(ROOT, None))
    assert report.missing_executables == ("git",)
    assert not report.missing_files


def test_tracked_markdown_symlink_target_content_invalidates_fingerprint() -> None:
    root = repository()
    target = root / "target.txt"
    target.write_text("# Valid\n", encoding="utf-8")
    document = root / "DOC.md"
    document.symlink_to(target.name)
    git(root, "add", "DOC.md", "target.txt")
    git(root, "commit", "-qm", "initial")
    cache = CacheProfile(False, (), (), "git-tracked-markdown", 4096, 67_108_864)

    before = profile_input_fingerprint(root, cache)
    target.write_text("[broken](missing.md)\n", encoding="utf-8")
    after = profile_input_fingerprint(root, cache)
    assert after["sha256"] != before["sha256"]


def test_tracked_markdown_selection_rejects_invalid_git_or_profile_identity() -> None:
    no_git = repository()
    cache = CacheProfile(False, (), (), "git-tracked-markdown", 4096, 67_108_864)
    try:
        profile_input_fingerprint(no_git, cache)
    except RunnerError as error:
        assert "Git worktree" in str(error)
    else:
        raise AssertionError("missing Git identity must fail explicitly")

    try:
        CacheProfile.from_raw(
            {"enabled": False, "input_paths": [], "environment": [], "input_selection": "unknown"},
            "cache",
        )
    except ProfileError as error:
        assert "input_selection" in str(error)
    else:
        raise AssertionError("unknown input selection must fail explicitly")

    profiles = Path(tempfile.mkdtemp()) / "command-profiles.toml"
    profiles.write_text("schema_version = 2\n", encoding="utf-8")
    try:
        load_profiles(profiles)
    except ProfileError as error:
        assert "schema_version" in str(error)
    else:
        raise AssertionError("profile schema drift must fail explicitly")


def test_tracked_markdown_selection_rejects_escaped_path() -> None:
    root = repository()
    escaped = root / "escaped.md"
    escaped.symlink_to(Path(tempfile.mkdtemp()))
    git(root, "add", "escaped.md")
    git(root, "commit", "-qm", "initial")
    try:
        profile_input_fingerprint(
            root,
            CacheProfile(False, (), (), "git-tracked-markdown", 4096, 67_108_864),
        )
    except RunnerError as error:
        assert "escapes repository root" in str(error)
    else:
        raise AssertionError("escaped Git-tracked path must fail explicitly")


def main() -> None:
    failures = []
    for test in (
        test_model_visible_result_uses_complete_or_versioned_overflow_union,
        test_direct_runner_releases_its_adoption_lock_after_terminal_result,
        test_tracked_markdown_scope_ignores_ignored_tree,
        test_tracked_markdown_scope_is_bounded_and_fails_closed,
        test_generic_glob_scope_is_unchanged,
        test_documentation_profile_disables_repository_wide_markdown_cache,
        test_documentation_profile_classifies_missing_git_as_dependency,
        test_tracked_markdown_symlink_target_content_invalidates_fingerprint,
        test_tracked_markdown_selection_rejects_invalid_git_or_profile_identity,
        test_tracked_markdown_selection_rejects_escaped_path,
        test_overflow_preserves_non_hit_states_without_upgrading_proof,
        test_overflow_requires_actual_cache_metadata,
        test_overflow_does_not_drop_cache_to_fit_a_smaller_budget,
        test_overflow_schema_requires_cache_proof,
    ):
        try:
            test()
        except AssertionError:
            failures.append(test.__name__)
            traceback.print_exc()
            print(f"FAIL {test.__name__}", flush=True)
        else:
            print(f"PASS {test.__name__}", flush=True)
    assert failures == [], f"failed contracts: {failures}"
    print("registered check input fingerprint contracts passed")


if __name__ == "__main__":
    main()
