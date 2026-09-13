from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _load_contract():
    try:
        from poise.application.requirements_registry import RequirementsCommands
        from poise.application.tasks import TaskCommands
        from poise.infrastructure.requirements_registry import RequirementsStore
        from poise.modules.requirements_registry.domain import RequirementsRegistry
        from poise.modules.requirements_registry.service import TaskRequirementsGate
    except (ImportError, ModuleNotFoundError):
        return None
    return {
        "RequirementsCommands": RequirementsCommands,
        "TaskCommands": TaskCommands,
        "RequirementsStore": RequirementsStore,
        "RequirementsRegistry": RequirementsRegistry,
        "TaskRequirementsGate": TaskRequirementsGate,
    }


CONTRACT = _load_contract()


def requirement(identifier: str, level: str, status: str, text: str) -> dict:
    return {
        "id": identifier,
        "level": level,
        "status": status,
        "text": text,
    }


def put(value: dict) -> dict:
    return {"kind": "put_requirement", "requirement": value}


def link(system: str, application: str) -> dict:
    return {"kind": "link", "system": system, "application": application}


def populated_registry():
    registry = CONTRACT["RequirementsRegistry"].empty()
    return registry.apply(
        [
            put(requirement("SYS-1", "system", "current", "System protects durable state.")),
            put(requirement("SYS-2", "system", "current", "System preserves audit provenance.")),
            put(requirement("APP-1", "application", "current", "AI poise owns registry writes.")),
            put(requirement("APP-2", "application", "future", "AI poise proposes missing chains.")),
            put(requirement("APP-OLD", "application", "obsolete", "Retired application rule.")),
            link("SYS-1", "APP-1"),
            link("SYS-2", "APP-1"),
            link("SYS-1", "APP-2"),
        ],
        max_items=20,
    )


class RequirementsRegistryDomainTests(unittest.TestCase):
    def test_statuses_many_to_many_and_nonobsolete_coverage(self):
        registry = populated_registry()

        self.assertEqual(
            registry.coverage(),
            {
                "application_without_system": [],
                "system_without_application": [],
            },
        )
        self.assertEqual(
            registry.requirement("APP-2")["status"],
            "future",
        )
        with self.assertRaisesRegex(Exception, "status"):
            registry.apply(
                [put(requirement("BAD", "system", "unknown", "Invalid status."))],
                max_items=20,
            )

    def test_task_plan_returns_full_text_chains_and_immutable_snapshot(self):
        registry = populated_registry()
        plan = registry.plan_task(
            [
                {
                    "text": "Task must preserve registry provenance.",
                    "applications": ["APP-1", "APP-2"],
                }
            ]
        )

        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["gaps"], [])
        chain = plan["chains"][0]
        self.assertEqual(chain["task_text"], "Task must preserve registry provenance.")
        self.assertEqual(
            [item["text"] for item in chain["applications"]],
            ["AI poise owns registry writes.", "AI poise proposes missing chains."],
        )
        self.assertEqual(
            [item["text"] for item in chain["systems"]],
            ["System protects durable state.", "System preserves audit provenance."],
        )
        snapshot = json.loads(json.dumps(plan["snapshot"]))
        changed = registry.apply(
            [put(requirement("SYS-1", "system", "current", "Changed live text."))],
            max_items=20,
        )
        self.assertEqual(
            snapshot["requirements"]["SYS-1"]["text"],
            "System protects durable state.",
        )
        self.assertEqual(changed.requirement("SYS-1")["text"], "Changed live text.")

    def test_task_plan_reports_actionable_full_text_gaps_and_proposals(self):
        registry = CONTRACT["RequirementsRegistry"].empty().apply(
            [put(requirement("APP-GAP", "application", "current", "Application has no system parent."))],
            max_items=20,
        )

        missing_application = registry.plan_task(
            [{"text": "Task needs an application owner.", "applications": []}]
        )
        self.assertEqual(missing_application["status"], "gaps")
        self.assertEqual(
            missing_application["gaps"][0]["proposal"],
            {"level": "application", "text": "Task needs an application owner."},
        )
        missing_system = registry.plan_task(
            [{"text": "Task uses an incomplete application.", "applications": ["APP-GAP"]}]
        )
        self.assertEqual(missing_system["status"], "gaps")
        self.assertEqual(
            missing_system["gaps"][0]["application_text"],
            "Application has no system parent.",
        )
        self.assertEqual(
            missing_system["gaps"][0]["proposal"]["level"],
            "system",
        )


class RequirementsRegistryStorageAndApiTests(unittest.TestCase):
    def test_separate_store_applies_atomic_idempotent_batches_and_queries(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = CONTRACT["RequirementsStore"](
                root / "requirements.sqlite",
                root / "requirements.lock",
                lock_seconds=1.0,
                poll_seconds=0.01,
            )
            commands = CONTRACT["RequirementsCommands"](store, max_items=20)
            request = {
                "request_id": "seed-1",
                "expected_revision": 0,
                "operations": [
                    put(requirement("SYS", "system", "future", "Future system requirement.")),
                    put(requirement("APP", "application", "future", "Future application requirement.")),
                    link("SYS", "APP"),
                ],
            }

            first = commands.apply(request)
            replay = commands.apply(request)
            self.assertEqual(first["revision"], 1)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(
                commands.query(
                    [
                        {"id": "coverage", "kind": "coverage"},
                        {"id": "registry", "kind": "registry"},
                    ]
                )["results"][0]["value"],
                {"application_without_system": [], "system_without_application": []},
            )
            before = commands.query([{"id": "registry", "kind": "registry"}])
            with self.assertRaisesRegex(Exception, "unknown|level|link"):
                commands.apply(
                    {
                        "request_id": "invalid-1",
                        "expected_revision": 1,
                        "operations": [
                            put(requirement("PARTIAL", "system", "current", "Must roll back.")),
                            link("MISSING", "APP"),
                        ],
                    }
                )
            self.assertEqual(
                commands.query([{"id": "registry", "kind": "registry"}]),
                before,
            )
            self.assertTrue((root / "requirements.sqlite").is_file())
            self.assertFalse((root / "tasks.sqlite").exists())

    def test_query_prepares_task_snapshot_then_gate_rejects_drift_and_gaps(self):
        registry = populated_registry()
        gate = CONTRACT["TaskRequirementsGate"].enabled(lambda: registry)
        task_links = [
            {
                "text": "Task must preserve registry provenance.",
                "applications": ["APP-1"],
            }
        ]
        snapshot = registry.plan_task(task_links)["snapshot"]
        contract = {
            "requirements": ["Task must preserve registry provenance."],
            "requirements_snapshot": snapshot,
        }

        self.assertEqual(gate.validate(contract), snapshot)
        drifted = json.loads(json.dumps(contract))
        drifted["requirements_snapshot"]["requirements"]["SYS-1"]["text"] = "Forged text."
        with self.assertRaisesRegex(Exception, "snapshot"):
            gate.validate(drifted)
        with self.assertRaisesRegex(Exception, "requirements_snapshot"):
            gate.validate({"requirements": contract["requirements"]})

    def test_common_task_creation_preflight_always_invokes_the_explicit_gate(self):
        class RejectingGate:
            def __init__(self):
                self.called = False

            def validate(self, _contract):
                self.called = True
                raise ValueError("registry gate called")

        class Tree:
            def existing_paths(self, _revision, _paths):
                raise AssertionError("repository tree must not run before registry gate")

        gate = RejectingGate()
        commands = CONTRACT["TaskCommands"](lambda: None, Tree(), gate)
        with self.assertRaisesRegex(ValueError, "registry gate called"):
            commands.prepare_creation(
                {"id": "T", "goal_type": "development"},
                {},
                [],
                "base-commit",
            )
        self.assertTrue(gate.called)


class RequirementsRegistryDeliveryTests(unittest.TestCase):
    def test_repository_delivery_declares_config_bootstrap_docs_and_skill_links(self):
        root = REPOSITORY_ROOT
        project = json.loads(
            (root / "config/projects/ai-poise/project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(project["paths"]["requirements_database"], "database/requirements.sqlite")
        self.assertEqual(project["paths"]["requirements_lock"], "database/requirements.lock")
        bootstrap = json.loads(
            (root / "delivery/requirements-bootstrap.json").read_text(encoding="utf-8")
        )
        self.assertTrue(bootstrap["requirements"])
        documentation = (root / "docs/workflows/requirements-registry.md").read_text(encoding="utf-8")
        self.assertIn("System → Application → Task", documentation)
        self.assertIn("requirements.sqlite", documentation)
        workflow_skill = (root / ".agents/skills/poise/SKILL.md").read_text(encoding="utf-8")
        development_skill = (root / ".agents/skills/poise-development/SKILL.md").read_text(encoding="utf-8")
        link_text = "docs/workflows/requirements-registry.md"
        self.assertIn(link_text, workflow_skill)
        self.assertIn(link_text, development_skill)


def run(mode: str) -> int:
    if CONTRACT is None:
        print("REQUIREMENTS_REGISTRY_NOT_IMPLEMENTED")
        return 1
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    for case in (
        RequirementsRegistryDomainTests,
        RequirementsRegistryStorageAndApiTests,
    ):
        suite.addTests(loader.loadTestsFromTestCase(case))
    if mode == "--delivery":
        suite.addTests(loader.loadTestsFromTestCase(RequirementsRegistryDeliveryTests))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.stderr.write(stream.getvalue())
        return 1
    print(
        "REQUIREMENTS_REGISTRY_DELIVERY_OK"
        if mode == "--delivery"
        else "REQUIREMENTS_REGISTRY_CORE_OK"
    )
    return 0


if __name__ == "__main__":
    selected = sys.argv[1] if len(sys.argv) == 2 else None
    if selected not in ("--core", "--delivery"):
        raise SystemExit("usage: test_registry_contract.py --core|--delivery")
    raise SystemExit(run(selected))
