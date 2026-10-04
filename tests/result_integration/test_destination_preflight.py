"""Observable destination refusal before integration effects, using real Git."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from pytest import MonkeyPatch

from conftest import project as project_fixture
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.infrastructure.task_cleanup import RuntimeTaskResourceCleanup
from poise.modules.foundation.errors import PoiseError
from result_integration.helpers import (
    conflicting_source_change, integration_guard_method, integration_input, prepare_completed_task, request,
    source_change,
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.PIPE, text=True,
    ).strip()


def observe_git(root: Path, *args: str, required: bool = True) -> bytes:
    """Avoid refreshing the index while observing the independent oracle."""
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, capture_output=True,
    )
    if required:
        assert result.returncode == 0, result.stderr
    return result.stdout if result.returncode == 0 else b"<absent>"


def git_path(root: Path, name: str) -> Path:
    value = Path(observe_git(root, "rev-parse", "--git-path", name).decode().strip())
    return value if value.is_absolute() else root / value


def file_values(root: Path) -> dict:
    values = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        info = path.lstat()
        if path.is_symlink():
            content = os.readlink(path).encode()
        elif path.is_file():
            content = path.read_bytes()
        else:
            continue
        values[str(relative)] = (stat.S_IMODE(info.st_mode), content)
    return values


def checkout_state(root: Path) -> dict:
    operations = {}
    for name in (
        "MERGE_HEAD", "MERGE_MSG", "REBASE_HEAD", "CHERRY_PICK_HEAD",
        "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer",
    ):
        path = git_path(root, name)
        operations[name] = (
            file_values(path) if path.is_dir()
            else path.read_bytes() if path.is_file() else None
        )
    return {
        "head": observe_git(root, "rev-parse", "HEAD"),
        "branch": observe_git(root, "symbolic-ref", "HEAD", required=False),
        "refs": observe_git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        "index": git_path(root, "index").read_bytes(),
        "files": file_values(root),
        "status": observe_git(root, "status", "--porcelain=v2", "-z"),
        "operations": operations,
        "worktrees": observe_git(root, "worktree", "list", "--porcelain"),
    }


def task_state(project: dict, task_id="T1") -> tuple:
    database = (project["root"] / project["cfg"]["paths"]["state"]
                / project["cfg"]["paths"]["database"])
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        return (
            db.execute(
                "SELECT status,stage_index,iteration,claimed_by,version,"
                "current_submission_id,metadata FROM tasks WHERE id=?", (task_id,),
            ).fetchone(),
            db.execute(
                "SELECT data,version FROM task_execution WHERE task_id=?", (task_id,),
            ).fetchone(),
            db.execute(
                "SELECT data FROM task_workflows WHERE task_id=?", (task_id,),
            ).fetchone(),
            db.execute("SELECT id,task_id FROM sessions ORDER BY id").fetchall(),
        )


class DestinationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patch = MonkeyPatch()
        self.addCleanup(self.patch.undo)
        self.project = project_fixture.__wrapped__(Path(self.directory.name), self.patch)
        self.sentinel = Path(self.directory.name) / "candidate-check-ran"

    def prepare(self, *, guard=False, task_id="T1", change=source_change):
        self.task_id = task_id
        methods = []
        if guard:
            method = integration_guard_method()
            method["argv"] = [
                sys.executable, "-c",
                "from pathlib import Path;"
                f"p=Path({str(self.sentinel)!r});p.write_text('ran');"
                "assert not Path('fail-check').exists()",
            ]
            method["stdout_contains"] = []
            methods.append(method)
        self.tools, self.source, self.accepted = prepare_completed_task(
            self.project, change, methods=methods, task_id=task_id,
            checks=[method["id"] for method in methods],
        )
        # The accepted result checks also run during preparation.
        self.sentinel.unlink(missing_ok=True)
        (self.project["app"] / "target-only").write_text("target\n")
        git(self.project["app"], "add", "target-only")
        git(self.project["app"], "commit", "-m", "test: divergent target")
        self.intent = integration_input(
            self.project, self.accepted, task_id=task_id,
            request_id="integrate-0048-1" if task_id == "0048" else "integrate-1",
        )

    def wrong_branch(self):
        git(self.project["app"], "switch", "-c", "other-target")

    def invoke(self, *, prepare_source=False):
        if prepare_source:
            return self.tools.runtime.integration_tools.prepare_source(self.intent)
        return self.tools.invoke(request("integrate", self.intent))

    def refusal(self, *, prepare_source=False, expected=(), recorded_source=None):
        roots = (self.project["app"], self.source)
        before = [checkout_state(root) for root in roots]
        state = task_state(self.project, self.task_id)
        try:
            result = self.invoke(prepare_source=prepare_source)
        except PoiseError as error:
            diagnostic = str(error)
        else:
            self.assertIsInstance(result, dict)
            self.assertEqual(result.get("status"), "blocked")
            diagnostic = json.dumps(result, ensure_ascii=False)
        self.assertEqual(task_state(self.project, self.task_id), state, "destination refusal changed Task state")
        for root, prior in zip(roots, before):
            self.assertEqual(checkout_state(root), prior, f"destination refusal changed {root}")
        self.assertFalse(self.sentinel.exists(), "candidate verification executed before refusal")
        for fact in (str(self.project["app"]), str(recorded_source or self.source), "main", *expected):
            self.assertIn(fact, diagnostic)
        self.assertRegex(diagnostic.lower(), r"correct|restore|исправ|восстанов")
        return diagnostic

    def test_wrong_branch_preserves_both_checkouts(self):
        self.prepare(guard=True)
        self.wrong_branch()
        self.refusal(expected=("other-target",))

    def test_dirty_checkout_preserves_ignored_bytes_modes_index_and_operations(self):
        self.prepare()
        self.wrong_branch()
        main = self.project["app"]
        (main / "src" / "double.py").write_text("staged value\n")
        git(main, "add", "src/double.py")
        (main / "src" / "double.py").write_text("unstaged value\n")
        (main / "untracked").write_bytes(b"untracked\x00bytes")
        git_path(main, "info/exclude").write_text("ignored-local\n")
        (main / "ignored-local").write_bytes(b"test-only ignored content\x00")
        (main / "executable").write_text("#!/bin/sh\n")
        (main / "executable").chmod(0o751)
        (main / "test-link").symlink_to("untracked")
        # A pre-existing operation must survive destination rejection exactly.
        git_path(main, "CHERRY_PICK_HEAD").write_text(self.accepted + "\n")
        self.refusal(expected=("other-target",))

    def test_detached_checkout_is_diagnosed_without_effects(self):
        self.prepare()
        git(self.project["app"], "switch", "--detach")
        self.refusal(expected=("detached",))

    def test_prepare_source_wrong_branch_refuses_before_effects(self):
        self.prepare()
        self.wrong_branch()
        self.refusal(prepare_source=True, expected=("other-target",))

    def foreign_repository(self):
        foreign = Path(self.directory.name) / "foreign"
        subprocess.run(
            ["git", "clone", "--no-local", str(self.project["app"]), str(foreign)],
            check=True, capture_output=True,
        )
        git(foreign, "switch", "main")
        return foreign

    def test_foreign_publication_repository_does_not_mutate_either_repository(self):
        self.prepare()
        foreign = self.foreign_repository()
        prior = checkout_state(foreign)
        self.tools.runtime.cfg["git"]["repository"] = str(foreign)
        self.refusal(expected=(str(foreign),))
        self.assertEqual(checkout_state(foreign), prior)

    def test_nested_publication_path_is_not_silently_rebound(self):
        self.prepare()
        nested = self.project["app"] / "src"
        self.tools.runtime.cfg["git"]["repository"] = str(nested)
        self.refusal(expected=(str(nested),))

    def test_foreign_source_binding_is_rejected_without_effects(self):
        self.prepare()
        foreign = self.foreign_repository()
        git(foreign, "config", "user.name", "Fixture")
        git(foreign, "config", "user.email", "fixture@example.invalid")
        git(foreign, "fetch", str(self.source), "HEAD")
        git(foreign, "switch", "-c", "tasks/T1", "FETCH_HEAD")
        with self.tools.runtime.store.unit_of_work() as uow:
            data, version = uow.execution.load("T1")
            data["worktree"] = str(foreign)
            uow.execution.save("T1", data, version)
        prior = checkout_state(foreign)
        self.refusal(expected=(str(foreign),), recorded_source=foreign)
        self.assertEqual(checkout_state(foreign), prior)

    def legacy_pending(self):
        # Literal isolated arrangement mirrors the supported legacy input schema.
        legacy = json.loads((Path(__file__).parent / "fixtures/destination_preflight_legacy.json").read_text())
        legacy["intent"] = {key: value for key, value in self.intent.items() if key != "resolutions"}
        legacy["target_before"] = self.intent["expected_target_commit"]
        legacy["merge"]["preflight"]["head"] = self.intent["expected_target_commit"]
        with self.tools.runtime.store.unit_of_work() as uow:
            data, version = uow.execution.load(self.task_id)
            data["pending"] = legacy
            uow.execution.save(self.task_id, data, version)

    def test_legacy_apply_refusal_preserves_pending_without_conversion(self):
        self.prepare(task_id="0048")
        self.legacy_pending()
        self.wrong_branch()
        self.refusal(expected=("other-target",))

    def test_legacy_prepare_source_refusal_preserves_pending_without_conversion(self):
        self.prepare(task_id="0048")
        self.legacy_pending()
        self.wrong_branch()
        self.refusal(prepare_source=True, expected=("other-target",))

    def test_unfinished_checks_failure_retry_refuses_before_rerunning_checks(self):
        self.prepare(guard=True)
        (self.project["app"] / "fail-check").write_text("fail\n")
        git(self.project["app"], "add", "fail-check")
        git(self.project["app"], "commit", "-m", "test: fail candidate check")
        self.intent = integration_input(self.project, self.accepted)
        blocked = self.invoke()
        self.assertEqual(blocked["phase"], "checks_failed")
        self.assertTrue(self.sentinel.exists())
        self.sentinel.unlink()
        self.wrong_branch()
        self.refusal(expected=("other-target",))

    def test_unfinished_publication_retry_refuses_before_state_transition(self):
        self.prepare()
        original = RuntimeResultIntegration._publish

        def controlled_failure(owner, run, repository):
            return owner._publication_failed(run, "injected_fixture_failure", {})

        self.patch.setattr(RuntimeResultIntegration, "_publish", controlled_failure)
        blocked = self.invoke()
        self.assertEqual(blocked["phase"], "publication_failed")
        self.patch.setattr(RuntimeResultIntegration, "_publish", original)
        self.wrong_branch()
        self.refusal(expected=("other-target",))

    def test_correction_reuses_same_request_and_terminal_replay_without_child(self):
        self.prepare(guard=True)
        self.wrong_branch()
        self.refusal(expected=("other-target",))
        git(self.project["app"], "switch", "main")
        completed = self.invoke()
        self.assertEqual(completed["status"], "integrated")
        self.assertEqual(completed["accepted_commit"], self.accepted)
        self.assertTrue(self.sentinel.exists())
        self.assertFalse(self.source.exists())
        before = checkout_state(self.project["app"])
        state = task_state(self.project)
        replay = self.invoke()
        self.assertEqual(replay["status"], "integrated")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["target_after"], completed["target_after"])
        self.assertEqual(checkout_state(self.project["app"]), before)
        self.assertEqual(task_state(self.project), state)

    def test_correct_destination_preserves_trailing_space_in_checkout_path(self):
        # Arrange a legitimate path before the real public accepted Task is made.
        original = self.project["app"]
        spaced = original.with_name(original.name + " ")
        original.rename(spaced)
        self.project["app"] = spaced
        self.project["cfg"]["git"]["repository"] = str(spaced)
        self.assertTrue(spaced.name.endswith(" "))
        self.prepare(guard=True)
        # Direct protocol oracle preserves the path suffix, independent of _git.
        self.assertEqual(
            observe_git(spaced, "rev-parse", "--show-toplevel"),
            (str(spaced.resolve()) + "\n").encode(),
        )
        try:
            completed = self.invoke()
        except PoiseError as error:
            self.fail(f"Correct destination with a meaningful space was refused: {error}")
        self.assertEqual(completed["status"], "integrated")
        self.assertEqual(completed["accepted_commit"], self.accepted)
        self.assertTrue(self.sentinel.exists())
        self.assertFalse(self.source.exists())
        self.assertEqual(
            observe_git(spaced, "rev-parse", "refs/heads/main").decode().removesuffix("\n"),
            completed["target_after"],
        )

    def test_malformed_current_phase_is_explicit_error_without_effects(self):
        self.prepare(guard=True)
        original = RuntimeResultIntegration._save

        def stop_after_prepared(owner, task_id, run, version):
            original(owner, task_id, run, version)
            if run.phase == "prepared":
                raise PoiseError("fixture interruption after prepared")

        self.patch.setattr(RuntimeResultIntegration, "_save", stop_after_prepared)
        with self.assertRaisesRegex(PoiseError, "fixture interruption after prepared"):
            self.invoke()
        self.patch.setattr(RuntimeResultIntegration, "_save", original)
        with self.tools.runtime.store.unit_of_work() as uow:
            data, version = uow.execution.load(self.task_id)
            self.assertEqual(data["pending"]["schema"], "existing-task-worktree-1")
            self.assertEqual(data["pending"]["phase"], "prepared")
            data["pending"]["phase"] = []
            uow.execution.save(self.task_id, data, version)
        state = task_state(self.project, self.task_id)
        roots = (self.project["app"], self.source)
        before = [checkout_state(root) for root in roots]
        error = None
        try:
            self.invoke()
        except (PoiseError, TypeError) as observed:
            error = observed
        self.assertEqual(task_state(self.project, self.task_id), state)
        self.assertEqual([checkout_state(root) for root in roots], before)
        self.assertFalse(self.sentinel.exists())
        self.assertIsInstance(error, PoiseError, "Malformed saved phase leaked a Python exception")
        self.assertRegex(str(error).lower(), r"phase|state")

    def test_correct_destination_integrates_and_removes_owned_child(self):
        self.prepare(guard=True)
        completed = self.invoke()
        self.assertEqual(completed["status"], "integrated")
        self.assertTrue(self.sentinel.exists())
        self.assertFalse(self.source.exists())
        self.assertEqual(
            git(self.project["app"], "rev-parse", "refs/heads/main"), completed["target_after"]
        )
        self.assertEqual(
            git(self.project["app"], "merge-base", "--is-ancestor", self.accepted, "main"), ""
        )

    def test_cleanup_recovery_does_not_require_new_unpublished_destination(self):
        self.prepare()
        original = RuntimeTaskResourceCleanup._run
        failed = []

        def fail_once(owner, cwd, *args, env=None):
            if args[:2] == ("worktree", "remove") and not failed:
                failed.append(True)
                return {"argv": ["git", *args], "actual_exit_code": 1,
                        "stdout": "", "stderr": "fixture cleanup failure"}
            return original(owner, cwd, *args, env=env)

        self.patch.setattr(RuntimeTaskResourceCleanup, "_run", fail_once)
        blocked = self.invoke()
        self.assertEqual(blocked["status"], "cleanup_pending")
        self.wrong_branch()
        completed = self.invoke()
        self.assertEqual(completed["status"], "integrated")
        self.assertFalse(self.source.exists())
        self.assertEqual(git(self.project["app"], "symbolic-ref", "--short", "HEAD"), "other-target")

    def test_snapshot_observations_do_not_refresh_indexes(self):
        self.prepare()
        for root in (self.project["app"], self.source):
            index = git_path(root, "index")
            before = (index.read_bytes(), index.stat().st_mtime_ns)
            self.assertEqual(checkout_state(root), checkout_state(root))
            self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)

    def test_malformed_pending_is_an_explicit_refusal_without_mutation(self):
        self.prepare()
        with self.tools.runtime.store.unit_of_work() as uow:
            data, version = uow.execution.load("T1")
            data["pending"] = {"schema": "unknown-integration-state"}
            uow.execution.save("T1", data, version)
        before = task_state(self.project)
        roots = [checkout_state(root) for root in (self.project["app"], self.source)]
        with self.assertRaises(PoiseError):
            self.invoke()
        self.assertEqual(task_state(self.project), before)
        self.assertEqual([checkout_state(root) for root in (self.project["app"], self.source)], roots)

    def interrupted_candidate(self, phase, *, guard=False, invalidate_proof=False):
        self.prepare(guard=guard)
        original = RuntimeResultIntegration._save

        def stop_after_saved_phase(owner, task_id, run, version):
            original(owner, task_id, run, version)
            if run.phase == phase:
                raise PoiseError("fixture interruption after durable candidate phase")

        self.patch.setattr(RuntimeResultIntegration, "_save", stop_after_saved_phase)
        with self.assertRaisesRegex(PoiseError, "fixture interruption"):
            self.invoke()
        self.patch.setattr(RuntimeResultIntegration, "_save", original)
        saved = self.tools.runtime.task_queries.record(self.task_id)["pending"]
        self.assertEqual(saved["phase"], phase)
        if invalidate_proof:
            self.assertTrue(saved["checks"])
            # Lost terminal output makes the real publication proof incomplete.
            Path(saved["checks"][0]["stdout"]).unlink()
        self.sentinel.unlink(missing_ok=True)
        self.wrong_branch()
        self.refusal(expected=("other-target",))

    def test_prepared_resume_refuses_before_first_candidate_transition(self):
        self.interrupted_candidate("prepared")

    def test_updating_resume_refuses_before_candidate_merge(self):
        self.interrupted_candidate("updating")

    def test_candidate_ready_resume_refuses_before_candidate_verification(self):
        self.interrupted_candidate("candidate_ready")

    def test_publishing_resume_refuses_before_proof_recheck_and_state_changes(self):
        self.interrupted_candidate("publishing", guard=True, invalidate_proof=True)

    def test_failed_candidate_resume_refuses_before_retry_state_and_checks(self):
        self.prepare(guard=True)
        original = RuntimeResultIntegration._run

        def fail_candidate_merge(owner, cwd, *args, env=None):
            if args[:3] == ("merge", "--no-ff", "--no-commit"):
                return {"argv": ["git", *args], "actual_exit_code": 2,
                        "stdout": "", "stderr": "fixture candidate merge failure"}
            return original(owner, cwd, *args, env=env)

        self.patch.setattr(RuntimeResultIntegration, "_run", fail_candidate_merge)
        blocked = self.invoke()
        self.assertEqual(blocked["phase"], "candidate_failed")
        saved = self.tools.runtime.task_queries.record(self.task_id)["pending"]
        self.assertEqual(saved["phase"], "candidate_failed")
        self.assertFalse(self.sentinel.exists())
        self.patch.setattr(RuntimeResultIntegration, "_run", original)
        self.wrong_branch()
        self.refusal(expected=("other-target",))

    def test_conflict_waiting_resume_preserves_in_progress_merge(self):
        self.prepare(change=conflicting_source_change)
        main = self.project["app"]
        (main / "src/double.py").write_text("VALUE = 'target'\n")
        git(main, "add", "src/double.py")
        git(main, "commit", "-m", "test: conflicting target")
        self.intent = integration_input(self.project, self.accepted)
        blocked = self.invoke()
        self.assertEqual(blocked["phase"], "awaiting_resolution")
        self.assertTrue(git_path(self.source, "MERGE_HEAD").is_file())
        self.wrong_branch()
        self.refusal(expected=("other-target",))
