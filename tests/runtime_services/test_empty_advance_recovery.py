from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest

import pytest

import conftest
from batch.helpers import request
from conftest import project as project_fixture
from conftest import Poise, WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from runner.helpers import finding, inspect, resolution
from runner.test_runner_paths import edit, result, setup_project


class EmptyAdvanceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.monkeypatch = pytest.MonkeyPatch()
        self.monkeypatch.setattr(conftest, "git", self._quiet_git)
        self.project = project_fixture.__wrapped__(
            Path(self.temporary.name), self.monkeypatch
        )

    def tearDown(self):
        self.monkeypatch.undo()
        self.temporary.cleanup()

    @staticmethod
    def _quiet_git(root, *args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def _handoff(self, runtime, request_id, payload=None):
        return WorkTools(runtime).invoke(
            request(
                "handoff",
                {
                    "request_id": request_id,
                    "reason": "release the accidental empty stage",
                    "result": payload,
                    "commit_message": None,
                    "artifact_paths": [],
                },
            )
        )

    def _recover(self):
        try:
            return WorkTools(WorkPoise(self.project["config_path"], "RECOVERY")).invoke(
                request(
                    "recover_empty_advance",
                    {
                        "task_id": "T1",
                        "reason": "User authorized recovery of the accidental empty advance.",
                    },
                )
            )
        except PoiseError as exc:
            if str(exc) == "Unknown work operation":
                self.fail("public recover_empty_advance operation is missing")
            raise

    def _empty_advance(self, *, release=True):
        runtime = setup_project(self.project, "development")
        context = runtime.bootstrap(task_file=self.project["task_path"])
        edit(context, "development", "verified\n")
        result(context, {})
        runtime.verify()
        inspection = runtime.bootstrap(decision="continue")
        result(inspection, inspect([finding()]))
        verified = runtime.verify()
        active = runtime.bootstrap(decision="continue")
        if release:
            self._handoff(runtime, "empty-advance-handoff")
        return runtime, verified, active

    def test_recovers_empty_advance_to_verified_inspection(self):
        runtime, verified, active = self._empty_advance()

        recovered = self._recover()

        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["stage"], "audit")
        self.assertEqual(recovered["iteration"], verified["iteration"])
        self.assertEqual(active["stage"], "amend")
        stored = runtime.task_queries.record("T1")
        self.assertEqual(stored["status"], "verified")
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
        self.assertEqual(reviewer["stage"], "audit")
        self.assertEqual(
            [item["id"] for item in reviewer["workflow"]["feedback"]["open_findings"]],
            ["F1"],
        )

    def test_rejects_changed_tree(self):
        _, _, active = self._empty_advance()
        edit(active, "development", "changed after handoff\n")

        with self.assertRaisesRegex(PoiseError, "tree|changed"):
            self._recover()

    def test_rejects_submitted_advanced_stage(self):
        runtime, _, active = self._empty_advance(release=False)
        result(active, {"resolutions": [resolution()]})
        self._handoff(runtime, "submitted-advance-handoff", active["result_template"])

        with self.assertRaisesRegex(PoiseError, "empty|submission"):
            self._recover()

    def test_rejects_rework_origin(self):
        runtime = setup_project(self.project, "development")
        context = runtime.bootstrap(task_file=self.project["task_path"])
        edit(context, "development", "verified\n")
        result(context, {})
        runtime.verify()
        runtime.bootstrap(
            decision="rework",
            feedback="This is rework, not advance.",
            rework_stage="draft",
        )
        self._handoff(runtime, "rework-origin-handoff")

        with self.assertRaisesRegex(PoiseError, "user_accept_and_continue"):
            self._recover()


if __name__ == "__main__":
    unittest.main()
