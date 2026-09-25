from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest

import pytest

import conftest
from batch.helpers import request
from conftest import project as project_fixture
from conftest import Poise
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.inspection.domain import FeedbackBook
from runner.helpers import finding, inspect, resolution
from runner.test_runner_paths import edit, result, setup_project


class EmptyReworkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.monkeypatch = pytest.MonkeyPatch()
        self.dispatchers = conftest.track_runtime_dispatchers(self.monkeypatch)
        self.monkeypatch.setattr(conftest, "git", self._quiet_git)
        self.project = project_fixture.__wrapped__(
            Path(self.temporary.name), self.monkeypatch
        )

    def tearDown(self):
        # Real optional workers may outlive foreground work, but not the directory
        # this test owns. Drain them before undoing hooks or deleting their DB.
        try:
            for dispatcher in self.dispatchers:
                dispatcher.close()
        finally:
            self.monkeypatch.undo()
        self.temporary.cleanup()

    @staticmethod
    def _quiet_git(root, *args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def _handoff(self, runtime, request_id="empty-rework-handoff", payload=None):
        return WorkTools(runtime).invoke(
            request(
                "handoff",
                {
                    "request_id": request_id,
                    "reason": "recover the accidentally opened empty rework",
                    "result": payload,
                    "commit_message": None,
                    "artifact_paths": [],
                },
            )
        )

    def _recover(self, task_id="T1"):
        runtime = Poise(self.project["config_path"], "RECOVERY")
        try:
            return WorkTools(runtime).invoke(
                request(
                    "recover_empty_rework",
                    {
                        "task_id": task_id,
                        "reason": "User authorized recovery of the accidental empty iteration.",
                    },
                )
            )
        except PoiseError as exc:
            if str(exc) == "Unknown work operation":
                self.fail(f"public recovery behavior is missing: {exc}")
            raise

    def _simple_empty_rework(self, *, release=True):
        runtime = setup_project(self.project, "development")
        context = runtime.bootstrap(task_file=self.project["task_path"])
        edit(context, "development", "verified\n")
        result(context, {})
        verified = runtime.verify()
        active = runtime.bootstrap(
            decision="rework",
            feedback="Accidental empty rework.",
            rework_stage="draft",
        )
        if release:
            self._handoff(runtime)
        return runtime, verified, active

    def _pending_resolution_rework(self):
        runtime = setup_project(self.project, "development")
        context = runtime.bootstrap(task_file=self.project["task_path"])
        edit(context, "development", "initial\n")
        result(context, {})
        runtime.verify()

        context = runtime.bootstrap(decision="continue")
        result(context, inspect([finding()]))
        runtime.verify()

        context = runtime.bootstrap(decision="continue")
        edit(context, "development", "resolved\n")
        result(context, {"resolutions": [resolution()]})
        verified = runtime.verify()
        # Reproduce a state persisted by the product version before the
        # pending-resolution rework guard existed. The current public path
        # must reject creating this state; recovery still has to read it.
        with self.monkeypatch.context() as legacy_product:
            legacy_product.setattr(
                FeedbackBook,
                "pending_resolutions",
                property(lambda _book: ()),
            )
            active = runtime.bootstrap(
                decision="rework",
                feedback="Accidental empty rework before inspection.",
                rework_stage="amend",
            )
        self._handoff(runtime)
        return runtime, verified, active

    def test_recovers_released_empty_rework(self):
        runtime, verified, active = self._simple_empty_rework()

        recovered = self._recover()

        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["task"], "T1")
        self.assertEqual(recovered["stage"], verified["stage"])
        self.assertEqual(recovered["iteration"], verified["iteration"])
        self.assertEqual(active["iteration"], verified["iteration"] + 1)
        stored = runtime.task_queries.record("T1")
        self.assertEqual(stored["status"], "verified")
        self.assertIsNone(stored["claimed_by"])
        reviewer = WorkTools(WorkPoise(self.project["config_path"], "REVIEWER")).invoke(
            request(
                "bootstrap",
                {
                    "task": {"id": "T1"},
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )
        self.assertEqual(reviewer["status"], "verified")
        self.assertEqual(reviewer["iteration"], verified["iteration"])

    def test_preserves_pending_resolution_and_history(self):
        runtime, verified, active = self._pending_resolution_rework()
        submissions_before = runtime.store.counts("T1")[0]
        history_before = runtime.task_queries.history("T1")

        recovered = self._recover()

        stored = runtime.task_queries.record("T1")
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(stored["status"], "verified")
        self.assertEqual(stored["iteration"], verified["iteration"])
        self.assertEqual(active["iteration"], verified["iteration"] + 1)
        self.assertEqual(runtime.store.counts("T1")[0], submissions_before)
        history_after = runtime.task_queries.history("T1")
        self.assertEqual(history_after[:-1], history_before)
        self.assertEqual(history_after[-1]["event"], "empty_rework_recovered")
        reviewer = WorkTools(WorkPoise(self.project["config_path"], "REVIEWER")).invoke(
            request(
                "bootstrap",
                {
                    "task": {"id": "T1"},
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )
        pending = reviewer["workflow"]["feedback"]["pending_resolutions"]
        self.assertEqual([item["id"] for item in pending], ["R1"])

    def test_recovers_integrated_task_after_its_worktree_was_cleaned(self):
        runtime, verified, _ = self._pending_resolution_rework()
        worktree = Path(runtime.task_queries.record("T1")["worktree"])
        commit = runtime.task_queries.record("T1")["last_report"]["commit"]
        self._quiet_git(self.project["app"], "merge", "--ff-only", commit)
        self._quiet_git(self.project["app"], "worktree", "remove", str(worktree))
        self._quiet_git(self.project["app"], "branch", "-d", "tasks/T1")

        recovered = self._recover()

        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["stage"], verified["stage"])
        self.assertEqual(recovered["verified_tree"], verified["verified_tree"])
        self.assertFalse(worktree.exists())

    def test_rejects_missing_worktree_when_verified_commit_is_not_integrated(self):
        runtime, _, _ = self._pending_resolution_rework()
        worktree = Path(runtime.task_queries.record("T1")["worktree"])
        self._quiet_git(self.project["app"], "worktree", "remove", str(worktree))
        record_before = runtime.task_queries.record("T1")
        history_before = runtime.task_queries.history("T1")

        with self.assertRaisesRegex(PoiseError, "текущем base"):
            self._recover()

        self.assertEqual(runtime.task_queries.record("T1"), record_before)
        self.assertEqual(runtime.task_queries.history("T1"), history_before)

    def test_recovers_across_ownership_only_handoff_suffix(self):
        _, verified, active = self._simple_empty_rework()
        for number in (1, 2):
            reviewer = WorkPoise(
                self.project["config_path"], f"INTERMEDIATE-REVIEWER-{number}"
            )
            context = WorkTools(reviewer).invoke(
                request(
                    "bootstrap",
                    {
                        "task": {"id": "T1"},
                        "decision": None,
                        "feedback": None,
                        "rework_stage": None,
                    },
                )
            )
            self.assertEqual(context["status"], "active")
            self.assertEqual(context["iteration"], active["iteration"])
            self._handoff(
                reviewer,
                request_id=f"ownership-only-handoff-{number}",
            )

        recovered = self._recover()

        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["stage"], verified["stage"])
        self.assertEqual(recovered["iteration"], verified["iteration"])

    def test_recovers_after_resumed_handoff_and_plain_ownership_release(self):
        _, verified, active = self._simple_empty_rework()
        reviewer = WorkPoise(self.project["config_path"], "FINAL-REVIEWER")
        context = WorkTools(reviewer).invoke(
            request(
                "bootstrap",
                {
                    "task": {"id": "T1"},
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )
        self.assertEqual(context["iteration"], active["iteration"])
        reviewer.ownership.release_task("T1")

        recovered = self._recover()

        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["stage"], verified["stage"])
        self.assertEqual(recovered["iteration"], verified["iteration"])

    def test_recovers_after_plain_ownership_release_without_a_rework_handoff(self):
        runtime, verified, active = self._simple_empty_rework(release=False)
        runtime.ownership.release_task("T1")

        recovered = self._recover()

        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["stage"], verified["stage"])
        self.assertEqual(recovered["iteration"], verified["iteration"])
        self.assertEqual(active["iteration"], verified["iteration"] + 1)

    def test_rejects_non_ownership_suffix_without_changing_task_state(self):
        runtime, _, active = self._simple_empty_rework()
        reviewer = WorkPoise(self.project["config_path"], "INTERMEDIATE-REVIEWER")
        WorkTools(reviewer).invoke(
            request(
                "bootstrap",
                {
                    "task": {"id": "T1"},
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )
        self._handoff(reviewer, request_id="ownership-only-handoff")
        handoff_version = runtime.task_queries.record("T1")["_version"] - 1
        event = json.dumps(
            {
                "event": "verified",
                "iteration": active["iteration"],
                "reason": None,
                "stage": active["stage"],
                "submission": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with runtime.store.transaction() as database:
            database.execute(
                "INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                (
                    "T1",
                    handoff_version,
                    datetime.now(timezone.utc).isoformat(),
                    event,
                ),
            )
        stored = runtime.task_queries.record("T1")
        history = runtime.task_queries.history("T1")

        with self.assertRaisesRegex(PoiseError, "non-ownership"):
            self._recover()

        self.assertEqual(runtime.task_queries.record("T1"), stored)
        self.assertEqual(runtime.task_queries.history("T1"), history)

    def test_rejects_claimed_task(self):
        self._simple_empty_rework(release=False)

        with self.assertRaisesRegex(PoiseError, "released"):
            self._recover()

    def test_rejects_changed_tree(self):
        _, _, active = self._simple_empty_rework()
        edit(active, "development", "changed after handoff\n")

        with self.assertRaisesRegex(PoiseError, "tree|changed"):
            self._recover()

    def test_rejects_submitted_iteration(self):
        runtime, _, active = self._simple_empty_rework(release=False)
        result(active, {})
        self._handoff(runtime, request_id="submitted-rework-handoff", payload=active["result_template"])

        with self.assertRaisesRegex(PoiseError, "empty|submission|non-ownership"):
            self._recover()

    def test_rejects_non_rework_last_event(self):
        runtime = setup_project(self.project, "development")
        context = runtime.bootstrap(task_file=self.project["task_path"])
        edit(context, "development", "verified\n")
        result(context, {})
        runtime.verify()
        self._handoff(runtime, request_id="verified-result-handoff")

        with self.assertRaisesRegex(PoiseError, "user_rework"):
            self._recover()


if __name__ == "__main__":
    unittest.main()
