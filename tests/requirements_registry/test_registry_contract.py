from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
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
        from poise.common import load_config
        from poise.infrastructure.requirements_registry import (
            RequirementsStore,
            TaskRequirementsSnapshotStore,
        )
        from poise.modules.requirements_registry.domain import RequirementsRegistry
        from poise.modules.requirements_registry.service import TaskRequirementsGate
    except (ImportError, ModuleNotFoundError):
        return None
    return {
        "RequirementsCommands": RequirementsCommands,
        "TaskCommands": TaskCommands,
        "RequirementsStore": RequirementsStore,
        "TaskRequirementsSnapshotStore": TaskRequirementsSnapshotStore,
        "RequirementsRegistry": RequirementsRegistry,
        "TaskRequirementsGate": TaskRequirementsGate,
        "load_config": load_config,
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
        registry = populated_registry().apply(
            [
                put(requirement("SYS-CURRENT-GAP", "system", "current", "Unassigned current system.")),
                put(requirement("SYS-FUTURE-GAP", "system", "future", "Unassigned future system.")),
                put(requirement("SYS-OLD-GAP", "system", "obsolete", "Retired system.")),
                put(requirement("APP-CURRENT-GAP", "application", "current", "Unowned current app.")),
                put(requirement("APP-FUTURE-GAP", "application", "future", "Unowned future app.")),
            ],
            max_items=20,
        )

        self.assertEqual(
            registry.coverage(),
            {
                "application_without_system": ["APP-CURRENT-GAP", "APP-FUTURE-GAP"],
                "system_without_application": ["SYS-CURRENT-GAP", "SYS-FUTURE-GAP"],
            },
        )
        self.assertEqual(registry.requirement("APP-1")["status"], "current")
        self.assertEqual(registry.requirement("APP-2")["status"], "future")
        self.assertEqual(registry.requirement("APP-OLD")["status"], "obsolete")
        self.assertNotIn("APP-OLD", registry.coverage()["application_without_system"])
        self.assertNotIn("SYS-OLD-GAP", registry.coverage()["system_without_application"])
        self.assertEqual(
            registry.coverage(mode="current"),
            {
                "application_without_system": ["APP-CURRENT-GAP"],
                "system_without_application": ["SYS-CURRENT-GAP"],
            },
        )
        with self.assertRaisesRegex(Exception, "obsolete|устар"):
            registry.plan_task([{"text": "New work cannot use a retired requirement.", "applications": ["APP-OLD"]}])
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
        self.assertEqual(snapshot["requirements"]["APP-2"]["status"], "future")
        self.assertEqual(
            snapshot["links"],
            [
                {"system": "SYS-1", "application": "APP-1"},
                {"system": "SYS-1", "application": "APP-2"},
                {"system": "SYS-2", "application": "APP-1"},
            ],
        )
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
                    put(requirement("SYS-GAP", "system", "current", "Disconnected system requirement.")),
                    put(requirement("APP-GAP", "application", "current", "Disconnected application requirement.")),
                    link("SYS", "APP"),
                ],
            }

            first = commands.apply(request)
            replay = commands.apply(request)
            self.assertEqual(first["revision"], 1)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            queried = commands.query(
                [
                    {"id": "chain", "kind": "chain", "requirement_id": "APP"},
                    {"id": "coverage", "kind": "coverage"},
                    {"id": "gaps", "kind": "gaps"},
                    {"id": "registry", "kind": "registry"},
                ]
            )["results"]
            self.assertEqual([item["id"] for item in queried], ["chain", "coverage", "gaps", "registry"])
            self.assertEqual(
                queried[0]["value"],
                {
                    "application": {"id": "APP", "level": "application", "status": "future", "text": "Future application requirement."},
                    "systems": [{"id": "SYS", "level": "system", "status": "future", "text": "Future system requirement."}],
                },
            )
            expected_gaps = {
                "application_without_system": ["APP-GAP"],
                "system_without_application": ["SYS-GAP"],
            }
            self.assertEqual(queried[1]["value"], expected_gaps)
            self.assertEqual(
                queried[2]["value"],
                [
                    {"kind": "application_without_system", "requirement_id": "APP-GAP", "text": "Disconnected application requirement."},
                    {"kind": "system_without_application", "requirement_id": "SYS-GAP", "text": "Disconnected system requirement."},
                ],
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
            with sqlite3.connect(root / "requirements.sqlite") as connection:
                registry_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"requirements", "requirement_links"}.issubset(registry_tables))
            self.assertFalse({"tasks", "task_state", "submissions"} & registry_tables)

    def test_public_bootstrap_import_is_idempotent_and_read_back_from_database(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = CONTRACT["RequirementsStore"](root / "requirements.sqlite", root / "requirements.lock", lock_seconds=1.0, poll_seconds=0.01)
            commands = CONTRACT["RequirementsCommands"](store, max_items=20)
            bootstrap_path = REPOSITORY_ROOT / "delivery/requirements-bootstrap.json"

            first = commands.import_bootstrap(bootstrap_path, request_id="bootstrap-1")
            replay = commands.import_bootstrap(bootstrap_path, request_id="bootstrap-1")
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            registry = commands.query([{"id": "registry", "kind": "registry"}])["results"][0]["value"]
            self.assertIn("bootstrap-system-requirements-traceability", registry["requirements"])
            self.assertEqual(registry["requirements"]["bootstrap-system-requirements-traceability"]["status"], "future")
            chains = commands.query([{"id": "chain", "kind": "chain", "requirement_id": "bootstrap-harness-task-traceability"}])["results"][0]["value"]
            self.assertEqual(chains["systems"][0]["id"], "bootstrap-system-requirements-traceability")

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

    def test_explicit_full_text_agreement_publishes_and_reads_task_db_snapshot(self):
        live = {"registry": populated_registry()}
        with tempfile.TemporaryDirectory() as raw:
            task_database = Path(raw) / "tasks.sqlite"
            snapshots = CONTRACT["TaskRequirementsSnapshotStore"](task_database)
            gate = CONTRACT["TaskRequirementsGate"].enabled(lambda: live["registry"], snapshots)
            task_commands = CONTRACT["TaskCommands"](lambda: None, object(), gate)
            links = [{"text": "Task must preserve registry provenance.", "applications": ["APP-1"]}]
            plan = live["registry"].plan_task(links)
            agreement = {"accepted": True, "chains": plan["chains"]}

            with self.assertRaisesRegex(Exception, "agreement|соглас"):
                task_commands.publish_requirements("TASK-1", links, {"accepted": False, "chains": plan["chains"]})
            published = task_commands.publish_requirements("TASK-1", links, agreement)
            self.assertEqual(task_commands.requirements_snapshot("TASK-1"), published)
            self.assertEqual(published["requirements"]["SYS-1"]["text"], "System protects durable state.")
            self.assertEqual(published["requirements"]["APP-1"]["status"], "current")
            self.assertEqual(published["links"], [{"system": "SYS-1", "application": "APP-1"}, {"system": "SYS-2", "application": "APP-1"}])
            live["registry"] = live["registry"].apply([put(requirement("SYS-1", "system", "obsolete", "Changed after publication."))], max_items=20)
            self.assertEqual(task_commands.requirements_snapshot("TASK-1")["requirements"]["SYS-1"]["text"], "System protects durable state.")
            with self.assertRaisesRegex(Exception, "immutable|exists|существ"):
                task_commands.publish_requirements("TASK-1", links, agreement)

    def test_common_task_creation_preflight_always_invokes_the_explicit_gate(self):
        class RejectingGate:
            def __init__(self):
                self.called = False

            def prepare_contract(self, contract):
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

    def test_historical_task_context_survives_later_live_registry_change(self):
        live = {"registry": populated_registry()}
        gate = CONTRACT["TaskRequirementsGate"].enabled(lambda: live["registry"])
        task_requirements = [
            {
                "text": "Task must preserve registry provenance.",
                "applications": ["APP-1"],
            }
        ]
        plan = live["registry"].plan_task(task_requirements)
        intent = {
            "requirements": ["Task must preserve registry provenance."],
            "requirements_snapshot": plan["snapshot"],
            "requirements_agreement": {
                "accepted": True,
                "chains": plan["chains"],
            },
        }

        _, original = gate.prepare_contract(intent)
        live["registry"] = live["registry"].apply(
            [
                put(
                    requirement(
                        "SYS-1",
                        "system",
                        "obsolete",
                        "Changed after Task publication.",
                    )
                )
            ],
            max_items=20,
        )
        with self.assertRaisesRegex(Exception, "snapshot"):
            gate.prepare_contract(intent)
        cleaned, recovered = gate.prepare_contract(intent, historical=True)
        self.assertNotIn("requirements_snapshot", cleaned)
        self.assertNotIn("requirements_agreement", cleaned)
        self.assertEqual(recovered, original)

        class RestartedNewborn:
            version = 7
            claimed_by = "session"
            process = {}
            sprint_id = None
            restart_history = ({"reason": "repair"},)
            draft = intent

        class Execution:
            def exists(self, task_id):
                return False

        class FirstTasks:
            def action_receipt(self, task_id, request_id, identity):
                return None

            def load_newborn(self, task_id):
                return RestartedNewborn()

            def restart_context(self, task_id):
                return {"config_hash": "saved-config"}

        class SecondTasks:
            def action_receipt(self, task_id, request_id, identity):
                return {"status": "available", "task": task_id, "replayed": True}

        class Uow:
            def __init__(self, tasks):
                self.tasks = tasks
                self.execution = Execution()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        units = iter((Uow(FirstTasks()), Uow(SecondTasks())))
        commands = CONTRACT["TaskCommands"](lambda: next(units), object(), gate)
        prepared = {}

        def prepare_creation(
            candidate,
            process,
            automatic_checks,
            base_revision,
            historical_requirements=False,
        ):
            prepared["historical_requirements"] = historical_requirements
            prepared["intent"] = candidate
            return object()

        commands.prepare_creation = prepare_creation
        ready = commands.ready_newborn(
            "TASK-RESTART",
            "session",
            7,
            [],
            "current-config",
            "ready-restarted-task",
            lambda: "base-revision",
        )
        self.assertTrue(prepared["historical_requirements"])
        self.assertEqual(prepared["intent"]["requirements_snapshot"], plan["snapshot"])
        self.assertTrue(ready["replayed"])

    def test_exact_automatic_creation_replay_skips_live_preflight(self):
        class Allocation:
            request_id = "create-task-1"
            task_id = "TASK-1"
            digest = "stored-digest"
            replayed = True

        class Tasks:
            def allocate(self, intent, policy, reserved_ids=()):
                return Allocation()

        class Uow:
            tasks = Tasks()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class ChangedLiveGate:
            def prepare_contract(self, intent):
                raise AssertionError("exact replay must not revalidate the live registry")

        class Tree:
            def existing_paths(self, revision, paths):
                raise AssertionError("exact replay must not repeat repository preflight")

        commands = CONTRACT["TaskCommands"](lambda: Uow(), Tree(), ChangedLiveGate())
        replay = commands.create(
            {
                "request_id": "create-task-1",
                "task": {
                    "goal_type": "development",
                },
            },
            actor="session",
            process={},
            automatic_checks=[],
            base_metadata={},
            execution=None,
            policy={
                "namespace": {"minimum": 1, "maximum": 9},
                "width": 1,
                "progression": {"first": 1, "step": 1},
            },
            base_revision="base",
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.task_id, "TASK-1")


class RequirementsRegistryDeliveryTests(unittest.TestCase):
    def test_project_config_requires_and_resolves_distinct_requirements_storage(self):
        source_path = REPOSITORY_ROOT / "config/projects/ai-poise/project.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config_root = root / "config"
            config_root.mkdir()
            for relative in source["processes"].values():
                target = config_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((source_path.parent / relative).read_text(encoding="utf-8"), encoding="utf-8")
            source["paths"]["state"] = str(root / "project-data")
            config_path = config_root / "project.json"
            config_path.write_text(json.dumps(source), encoding="utf-8")
            state, config, _ = CONTRACT["load_config"](config_path)
            requirements_db = (state / config["paths"]["requirements_database"]).resolve()
            task_db = (state / config["paths"]["database"]).resolve()
            self.assertEqual(requirements_db, root / "project-data/database/requirements.sqlite")
            self.assertNotEqual(requirements_db, task_db)
            self.assertFalse(requirements_db.is_relative_to(Path(config["git"]["repository"])))

            del source["paths"]["requirements_database"]
            config_path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "requirements_database|paths"):
                CONTRACT["load_config"](config_path)
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["paths"]["state"] = str(root / "project-data")
            del source["paths"]["requirements_lock"]
            config_path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "requirements_lock|paths"):
                CONTRACT["load_config"](config_path)

    def test_task_and_requirements_databases_have_disjoint_owned_schemas(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            requirements_db = root / "requirements.sqlite"
            task_db = root / "tasks.sqlite"
            CONTRACT["RequirementsStore"](requirements_db, root / "requirements.lock", lock_seconds=1.0, poll_seconds=0.01)
            snapshots = CONTRACT["TaskRequirementsSnapshotStore"](task_db)
            snapshots.initialize()
            with sqlite3.connect(requirements_db) as connection:
                requirements_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            with sqlite3.connect(task_db) as connection:
                task_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"requirements", "requirement_links"}.issubset(requirements_tables))
            self.assertIn("task_requirements_snapshots", task_tables)
            self.assertNotIn("task_requirements_snapshots", requirements_tables)
            self.assertFalse({"requirements", "requirement_links"} & task_tables)
            self.assertTrue(requirements_tables.isdisjoint(task_tables))

    def test_repository_delivery_declares_config_bootstrap_docs_and_skill_links(self):
        root = REPOSITORY_ROOT
        project = json.loads(
            (root / "config/projects/ai-poise/project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(project["paths"]["requirements_database"], "database/requirements.sqlite")
        self.assertEqual(project["paths"]["requirements_lock"], "database/requirements.lock")
        bootstrap = json.loads((root / "delivery/requirements-bootstrap.json").read_text(encoding="utf-8"))
        self.assertTrue(bootstrap["system_requirements"])
        self.assertTrue(bootstrap["application_requirements"])
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
    if mode == "--recovery-red":
        for name in (
            "test_historical_task_context_survives_later_live_registry_change",
            "test_exact_automatic_creation_replay_skips_live_preflight",
        ):
            suite.addTest(RequirementsRegistryStorageAndApiTests(name))
    else:
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
        if mode == "--recovery-red":
            print("REQUIREMENTS_REGISTRY_RECOVERY_NOT_IMPLEMENTED")
            return 1
        sys.stderr.write(stream.getvalue())
        return 1
    if mode == "--recovery-red":
        print("REQUIREMENTS_REGISTRY_RECOVERY_OK")
        return 0
    print(
        "REQUIREMENTS_REGISTRY_DELIVERY_OK"
        if mode == "--delivery"
        else "REQUIREMENTS_REGISTRY_CORE_OK"
    )
    return 0


if __name__ == "__main__":
    selected = sys.argv[1] if len(sys.argv) == 2 else None
    if selected not in ("--core", "--delivery", "--recovery-red"):
        raise SystemExit(
            "usage: test_registry_contract.py --core|--delivery|--recovery-red"
        )
    raise SystemExit(run(selected))
