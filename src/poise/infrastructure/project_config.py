"""Revision-aware live project configuration publication and recovery."""
from __future__ import annotations
from copy import deepcopy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import tempfile

from ..common import configured_root, configured_storage_path, descendant, digest, load_config, load_process_migration_config, validate_candidate_config
from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.goal_config.domain import GoalTypeDefinition, PROCESS_FIELDS, BatchValidationError
from ..modules.projects.domain import field_at, path_key
from .goal_config import atomic_write, read_document
from .locking import exclusive_lock
from .projects import confined, ProjectSettings, FileProjectSetup, valid_project_probe_readiness


def copy_state_tree(source, staging):
    shutil.copytree(source, staging)


def publish_state_tree(staging, destination):
    os.rename(staging, destination)


def switch_project_config(path, content, mode):
    atomic_write(path, content, mode)


def remove_source_state(source):
    shutil.rmtree(source)


def encoded(value, indent):
    return (json.dumps(value, ensure_ascii=False, indent=indent, allow_nan=False) + "\n").encode()


def same_tree(left, right):
    if not left.is_dir() or not right.is_dir():
        return False
    def files(root):
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
        }
    return files(left) == files(right)


class FileProjectConfigUpdate:
    def __init__(self, settings: ProjectSettings):
        self.settings = settings

    def _config_path(self, value):
        path = Path(value).resolve()
        if not path.is_file() or not path.is_relative_to(self.settings.root):
            raise PoiseError("config_path must select an existing managed project")
        return path

    def _receipt(self, root, relative):
        return descendant(root, relative)

    def _source(self, config_path, updates):
        try:
            return load_config(config_path)
        except BatchValidationError as failure:
            config = read_document(config_path)
            if not isinstance(config.get("processes"), dict) or not config["processes"]:
                raise failure
            root = config_path.parent
            processes = {}
            legacy = set()
            legacy_fields = PROCESS_FIELDS - {"worktree_required"}
            for goal, relative in config["processes"].items():
                process = read_document(descendant(root, relative))
                if set(process) == legacy_fields and process.get("goal_type") == goal:
                    legacy.add(goal)
                else:
                    GoalTypeDefinition.parse(process)
                processes[goal] = process
            explicit = {
                item.get("goal_type")
                for item in updates
                if isinstance(item, dict)
                and any(
                    isinstance(change, dict)
                    and change.get("op") == "set_worktree_required"
                    and type(change.get("value")) is bool
                    for change in item.get("changes", ())
                )
            }
            if not legacy or not legacy <= explicit:
                raise failure
            return root, config, processes

    def _known_revisions(self, root):
        known = set()
        config_path = str(root / self.settings.raw["manifest"])
        setup = root / self.settings.raw["receipt"]
        candidates = [setup]
        candidates.extend((root / "operations").glob("*.json"))
        candidates.extend((self.settings.root / "operations").glob("*.json"))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                saved = read_document(path)
                result = saved.get("result", {})
                if result.get("config_path") != config_path:
                    continue
                for key in ("prior_revision", "revision"):
                    if isinstance(result.get(key), str):
                        known.add(result[key])
            except PoiseError:
                continue
        return known

    def stage_criteria_lock(self):
        """The coordinator holds this before the Task lock, including recovery."""
        s = self.settings
        return exclusive_lock(s.lock, s.raw["lock_seconds"], s.raw["lock_poll_seconds"])

    @staticmethod
    def _receipt_digest(value):
        return (isinstance(value, str) and len(value) == 64
                and all(c in "0123456789abcdef" for c in value))

    def _stage_criteria_update_receipt(self, saved, path):
        """Validate native source/completion records before nested field access."""
        result = saved.get("result")
        required = {"status", "changed", "prior_revision", "revision", "config_path",
                    "readiness", "receipt_path", "replayed"}
        if (set(saved) != {"schema", "request_id", "request_digest", "result"}
                or saved.get("schema") != "project-config-receipt-1"
                or not isinstance(saved.get("request_id"), str) or not saved["request_id"]
                or not self._receipt_digest(saved.get("request_digest"))
                or not isinstance(result, dict)
                or not required <= result.keys()
                or result.keys() - required - {"state_relocation"}
                or result.get("status") != "updated"
                or type(result.get("changed")) is not bool
                or type(result.get("replayed")) is not bool
                or not self._receipt_digest(result.get("prior_revision"))
                or not self._receipt_digest(result.get("revision"))
                or not isinstance(result.get("config_path"), str) or not result["config_path"]
                or result.get("receipt_path") != str(path)
                or not valid_project_probe_readiness(result.get("readiness"))
                or ("state_relocation" in result and not isinstance(result["state_relocation"], dict))):
            raise PoiseError(f"Invalid project migration receipt: {path}")
        return result

    def _stage_criteria_pending_document(self, saved):
        if (set(saved) != {"schema", "request_id", "request_digest", "before", "after"}
                or saved.get("schema") != "project-config-pending-1"
                or not isinstance(saved.get("request_id"), str) or not saved["request_id"]
                or not self._receipt_digest(saved.get("request_digest"))):
            raise PoiseError("Invalid project pending publication")
        for key in ("before", "after"):
            value = saved[key]
            if (not isinstance(value, dict) or set(value) != {"config", "processes"}
                    or not self._receipt_digest(value.get("config"))
                    or not isinstance(value.get("processes"), dict)
                    or not all(isinstance(k, str) and k and self._receipt_digest(v)
                               for k, v in value["processes"].items())):
                raise PoiseError("Invalid project pending publication components")
        return saved

    def stage_criteria_source(self, config_path, revision):
        """Select only one validated native record proving the source revision."""
        setup_path = config_path.parent / self.settings.raw["receipt"]
        setup = read_document(setup_path)  # Already validated by binding admission.
        if setup["result"]["revision"] == revision:
            return {"path": str(setup_path), "digest": digest(setup),
                    "revision": revision, "revision_field": "revision", "schema": "project-receipt-1"}
        for directory in (config_path.parent / "operations", self.settings.root / "operations"):
            for path in sorted(directory.glob("*.json")):
                saved = read_document(path)
                if saved.get("schema") != "project-config-receipt-1":
                    continue
                result = saved.get("result")
                if isinstance(result, dict) and result.get("config_path") != str(config_path):
                    continue
                result = self._stage_criteria_update_receipt(saved, path)
                for key in ("revision", "prior_revision"):
                    if result[key] == revision:
                        return {"path": str(path), "digest": digest(saved), "revision": revision,
                                "revision_field": key, "schema": "project-config-receipt-1"}
        raise PoiseError("Live project revision is not known")

    def require_stage_criteria_source(self, publication):
        source = publication["source"]
        path = Path(source["path"])
        saved = read_document(path)
        if digest(saved) != source["digest"] or saved.get("schema") != source["schema"]:
            raise PoiseError("Migration source receipt changed")
        if source["schema"] == "project-config-receipt-1":
            result = self._stage_criteria_update_receipt(saved, path)
        else:
            result = saved["result"]  # Setup record is validated by binding admission.
        if (result.get("config_path") != publication["config_path"]
                or result.get(source["revision_field"]) != publication["revision"]):
            raise PoiseError("Migration source receipt revision changed")

    def stage_criteria_binding(self, config_path):
        try:
            s = ProjectSettings(self.settings.path)
        except OSError as exc:
            raise PoiseError(f"Cannot load selected project settings: {exc}") from exc
        if s.raw != self.settings.raw or s.root != self.settings.root:
            raise PoiseError("Migration publication owner changed")
        config_path = Path(config_path).resolve()
        if (not config_path.is_relative_to(s.root) or config_path.parent == s.root
                or config_path.name != s.raw["manifest"]):
            raise PoiseError("Project settings do not own the selected configuration")
        config = read_document(config_path)
        registry = FileProjectSetup(s)._registry()
        entry = registry["projects"].get(config["project"])
        if entry != {"config_path": str(config_path.relative_to(s.root))}:
            raise PoiseError("Project registry binding differs from the selected configuration")
        receipt_path = config_path.parent / s.raw["receipt"]
        if not receipt_path.is_file():
            raise PoiseError("Project setup receipt is missing")
        receipt = read_document(receipt_path)
        result = receipt.get("result")
        if not isinstance(result, dict):
            raise PoiseError("Invalid project setup receipt result")
        if result.get("config_path") != str(config_path):
            raise PoiseError("Project setup receipt belongs to another configuration")
        sha256 = self._receipt_digest
        if (set(receipt) != {"schema", "request_id", "request_digest", "template", "files", "result"}
                or receipt["schema"] != "project-receipt-1"
                or not isinstance(receipt["request_id"], str) or not receipt["request_id"]
                or not sha256(receipt["request_digest"])
                or not isinstance(receipt["template"], dict)
                or set(receipt["template"]) != {"id", "version", "digest"}
                or not all(isinstance(receipt["template"][k], str) and receipt["template"][k]
                           for k in ("id", "version"))
                or not sha256(receipt["template"]["digest"])
                or not isinstance(receipt["files"], dict)
                or not receipt["files"] or not all(isinstance(k, str) and k and sha256(v)
                                                   for k, v in receipt["files"].items())
                or set(result) != {"status", "project", "revision", "config_path",
                                   "process_count", "readiness", "replayed"}
                or type(result.get("process_count")) is not int or result["process_count"] < 0
                or type(result.get("replayed")) is not bool
                or not valid_project_probe_readiness(result.get("readiness"))
                or receipt["result"].get("project") != config["project"]
                or receipt["result"].get("status") != "created"
                or not sha256(receipt["result"].get("revision"))):
            raise PoiseError("Invalid project setup receipt")
        return {"kind": "project", "settings_path": str(s.path), "settings": deepcopy(s.raw),
                "root": str(s.root), "config_path": str(config_path),
                "registry": digest(registry), "setup_receipt": digest(receipt),
                "targets": {goal: str(descendant(config_path.parent, value))
                            for goal, value in config["processes"].items()},
                "lock": {"path": str(s.lock), "device": s.lock.stat().st_dev,
                         "inode": s.lock.stat().st_ino}}

    def stage_criteria_snapshot(self, config_path):
        binding = self.stage_criteria_binding(config_path)
        root, config, processes = load_process_migration_config(config_path)
        for directory in (self.settings.root / "operations", root / "operations"):
            for path in directory.glob("*.json.pending"):
                pending = self._stage_criteria_pending_document(read_document(path))
                if pending["before"]["config"] == digest(config):
                    raise PoiseError("Project has an unrelated pending publication")
        return {"binding": binding, "config": config, "processes": processes,
                "bytes": {goal: descendant(root, relative).read_bytes()
                          for goal, relative in config["processes"].items()},
                "revision": digest({"config": config, "processes": processes})}

    @staticmethod
    def _component_digests(config, processes):
        return {"config": digest(config),
                "processes": {goal: digest(value) for goal, value in processes.items()}}

    def _stage_criteria_receipt(self, publication):
        config_path = Path(publication["config_path"])
        if self.stage_criteria_binding(config_path) != publication["binding"]:
            raise PoiseError("Migration publication owner changed")
        self.require_stage_criteria_source(publication)
        request = publication["request"]
        receipt_path = self._receipt(self.settings.root, request["receipt_path"])
        if not receipt_path.is_file():
            return None
        saved = read_document(receipt_path)
        expected = digest({"config": publication["config"], "processes": publication["candidate"]})
        result = self._stage_criteria_update_receipt(saved, receipt_path)
        if (set(saved) != {"schema", "request_id", "request_digest", "result"}
                or saved["schema"] != "project-config-receipt-1"
                or saved["request_id"] != request["request_id"]
                or saved["request_digest"] != digest(request)
                or result.get("config_path") != str(config_path)
                or result.get("prior_revision") != publication["revision"]
                or result.get("revision") != expected
                or result.get("receipt_path") != str(receipt_path)
                or result.get("status") != "updated"):
            raise PoiseError("Project migration receipt differs from its intent")
        _, config, processes = load_config(config_path)
        if config != publication["config"] or processes != publication["candidate"]:
            raise PoiseError("Published migration process drifted")
        return saved

    def completed_stage_criteria(self, publication):
        """Read-only completion requires validated receipt and pending retirement."""
        saved = self._stage_criteria_receipt(publication)
        if saved is None:
            return None
        receipt_path = self._receipt(self.settings.root, publication["request"]["receipt_path"])
        if receipt_path.with_name(receipt_path.name + ".pending").exists():
            return None
        return saved

    def _stage_criteria_pending(self, publication):
        request = publication["request"]
        return {"schema": "project-config-pending-1", "request_id": request["request_id"],
                "request_digest": digest(request),
                "before": self._component_digests(publication["config"], publication["processes"]),
                "after": self._component_digests(publication["config"], publication["candidate"])}

    def _require_stage_criteria_pending(self, publication, path):
        pending = self._stage_criteria_pending(publication)
        saved = self._stage_criteria_pending_document(read_document(path))
        if saved.get("request_digest") != pending["request_digest"]:
            raise PoiseError("Different request uses the pending request receipt")
        if saved != pending:
            raise PoiseError("Project pending publication candidate changed")

    def publish_stage_criteria(self, publication):
        """Publish one historical candidate; caller already holds both locks."""
        done = self._stage_criteria_receipt(publication)
        if done is not None:
            receipt_path = self._receipt(self.settings.root, publication["request"]["receipt_path"])
            pending_path = receipt_path.with_name(receipt_path.name + ".pending")
            if pending_path.exists():
                self._require_stage_criteria_pending(publication, pending_path)
                pending_path.unlink()
            return done["result"]
        request = publication["request"]
        config_path = Path(publication["config_path"])
        root, live_config, live_processes = load_process_migration_config(config_path)
        if live_config != publication["config"]:
            raise PoiseError("Unmanaged manifest change during recovery")
        receipt_path = self._receipt(self.settings.root, request["receipt_path"])
        pending_path = receipt_path.with_name(receipt_path.name + ".pending")
        pending = self._stage_criteria_pending(publication)
        replayed = pending_path.is_file()
        if replayed:
            self._require_stage_criteria_pending(publication, pending_path)
        for goal, live in live_processes.items():
            if live not in (publication["processes"][goal], publication["candidate"][goal]):
                raise PoiseError("Unmanaged process change during recovery")
        if not replayed and live_processes != publication["processes"]:
            raise PoiseError("Partial project publication lacks its pending intent")
        self._validate_candidate(root, live_config, publication["candidate"])
        if not replayed:
            atomic_write(pending_path, encoded(pending, self.settings.raw["json_indent"]),
                         self.settings.raw["file_mode"])
        current_revision = digest({"config": live_config, "processes": live_processes})
        return self._publish_candidate_locked(
            request, config_path, live_config, live_processes, live_config,
            publication["candidate"], receipt_path, pending_path, replayed,
            current_revision, {"repository": "not_checked", "remote": "not_checked"}, None)

    def _publish_candidate_locked(self, request, config_path, live_config, live_processes,
                                  candidate_config, candidate_processes, receipt_path,
                                  pending_path, replayed, current_revision, readiness, locks):
        settings = self.settings
        root = config_path.parent
        for goal, process in candidate_processes.items():
            if process != live_processes[goal]:
                atomic_write(descendant(root, candidate_config["processes"][goal]),
                             encoded(process, settings.raw["json_indent"]), settings.raw["file_mode"])
        relocation = None
        if request["state_relocation"] is not None:
            relocation = self._relocate(config_path, live_config, candidate_config,
                                        request["state_relocation"], locks, replayed)
        elif candidate_config != live_config:
            atomic_write(config_path, encoded(candidate_config, settings.raw["json_indent"]),
                         settings.raw["file_mode"])
        _, final_config, final_processes = load_config(config_path)
        if final_config != candidate_config or final_processes != candidate_processes:
            raise PoiseError("Project publication readback differs from candidate")
        revision = digest({"config": final_config, "processes": final_processes})
        result = {"status": "updated", "changed": revision != request["expected_revision"],
                  "prior_revision": request["expected_revision"], "revision": revision,
                  "config_path": str(config_path), "readiness": readiness,
                  "receipt_path": str(receipt_path),
                  "replayed": replayed or current_revision != request["expected_revision"]}
        if relocation is not None:
            result["state_relocation"] = relocation
        atomic_write(receipt_path, encoded({"schema": "project-config-receipt-1",
            "request_id": request["request_id"], "request_digest": digest(request),
            "result": result}, settings.raw["json_indent"]), settings.raw["file_mode"])
        pending_path.unlink(missing_ok=True)
        return result

    def _manifest(self, config, edits, *, workspace=False):
        candidate = deepcopy(config)
        changed = set()
        for item in edits:
            if not isinstance(item, dict) or set(item) != {"path", "value"}:
                raise PoiseError("manifest edit requires path and value")
            path = path_key(item["path"])
            if path[0] in {"paths", "processes", "project", "schema"} and not (
                    workspace and path == ('paths', 'worktrees')):
                raise PoiseError(f"Manifest field {path[0]} has a dedicated owner")
            # Named optional C004 capability, not permission to create arbitrary fields.
            if path not in (("development_routing",), ("source_reader",), ("telemetry_delivery",), ("task_planning",)):
                field_at(config, path)
            if any(path[:len(old)] == old or old[:len(path)] == path for old in changed):
                raise PoiseError("Conflicting manifest edits")
            parent = field_at(candidate, path[:-1])
            parent[path[-1]] = deepcopy(item["value"])
            changed.add(path)
        return candidate

    def _processes(self, processes, updates):
        candidate = deepcopy(processes)
        seen = set()
        for item in updates:
            if not isinstance(item, dict) or set(item) != {"goal_type", "expected_revision", "changes"}:
                raise PoiseError("process update requires goal_type, expected_revision and changes")
            goal = item["goal_type"]
            if goal in seen or goal not in candidate:
                raise PoiseError("Unknown or duplicate process update")
            current = candidate[goal]
            built = GoalTypeDefinition.build_configuration(goal, current, item["changes"]).data
            if digest(current) != item["expected_revision"] and built != current:
                raise VersionConflict("Process revision is stale")
            candidate[goal] = built
            seen.add(goal)
        return candidate

    def _validate_candidate(self, root, config, processes):
        with tempfile.TemporaryDirectory(prefix=".project-update-", dir=root.parent) as temporary:
            candidate_root = Path(temporary)
            for goal, relative in config["processes"].items():
                path = descendant(candidate_root, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(encoded(processes[goal], self.settings.raw["json_indent"]))
            manifest = candidate_root / self.settings.raw["manifest"]
            manifest.write_bytes(encoded(config, self.settings.raw["json_indent"]))
            validate_candidate_config(manifest, root)

    def _relocation_state(self, config_path, config, move):
        if not isinstance(move, dict) or set(move) != {"expected_source", "destination", "source_disposition"}:
            raise PoiseError("state relocation requires exact fields")
        source = Path(move["expected_source"]).resolve()
        destination = Path(move["destination"]).resolve()
        disposition = move["source_disposition"]
        overlap = source == destination or source.is_relative_to(destination) or destination.is_relative_to(source)
        if disposition not in {"retain", "delete_after_publish"} or overlap:
            raise PoiseError("Invalid state relocation")
        configured = configured_root(config_path.parent, config["paths"]["state"])
        already_switched = configured == destination
        if not already_switched and configured != source:
            raise VersionConflict("State source does not match live configuration")
        if destination.exists() and not already_switched:
            if not source.exists() or not same_tree(source, destination):
                raise PoiseError("State destination must be absent or verified recovery data")
        if not source.exists() and not (already_switched and destination.is_dir()):
            raise PoiseError("State source is missing; empty replacement refused")
        return source, destination, disposition, already_switched

    @staticmethod
    def _staging_path(destination):
        return destination.parent / f".{destination.name}.project-update"

    def _relocate(self, config_path, live_config, candidate_config, move, locks, recovering):
        source, destination, disposition, already_switched = self._relocation_state(
            config_path, live_config, move
        )
        staging = self._staging_path(destination)
        if not destination.exists():
            if staging.exists():
                if not recovering:
                    raise PoiseError("State relocation staging already exists; refusing unverified deletion")
                shutil.rmtree(staging)
            copy_state_tree(source, staging)
            publish_state_tree(staging, destination)
        if source.exists() and not same_tree(source, destination):
            raise PoiseError("Relocated state verification failed")
        if not already_switched:
            destination_lock = descendant(destination, candidate_config["paths"]["lock"])
            locks.enter_context(exclusive_lock(
                destination_lock,
                self.settings.raw["lock_seconds"],
                self.settings.raw["lock_poll_seconds"],
            ))
        candidate_config["paths"]["state"] = str(destination)
        switch_project_config(config_path, encoded(candidate_config, self.settings.raw["json_indent"]), self.settings.raw["file_mode"])
        if disposition == "delete_after_publish" and source.exists():
            remove_source_state(source)
        return {"status": "completed", "source_disposition": disposition,
                "source": str(source), "destination": str(destination)}

    def apply(self, request):
        settings = self.settings
        workspace = request['schema'] == 'project-worktree-root-1'
        config_path = self._config_path(request["config_path"])
        root, live_config, live_processes = self._source(
            config_path, request["process_updates"]
        )
        receipt_path = self._receipt(settings.root, request["receipt_path"])
        pending_path = receipt_path.with_name(receipt_path.name + ".pending")
        request_digest = digest(request)
        lock = confined(settings.root, settings.raw["lock"])
        try:
            with ExitStack() as locks:
                locks.enter_context(exclusive_lock(
                    lock, settings.raw["lock_seconds"], settings.raw["lock_poll_seconds"]
                ))
                if receipt_path.is_file():
                    saved = read_document(receipt_path)
                    if saved.get("request_digest") != request_digest:
                        raise VersionConflict("Different request uses the same request receipt")
                    return {**saved["result"], "replayed": True}
                replayed = False
                if pending_path.is_file():
                    pending = read_document(pending_path)
                    if pending.get("request_digest") != request_digest:
                        raise VersionConflict("Different request uses the pending request receipt")
                    replayed = True
                root, live_config, live_processes = self._source(
                    config_path, request["process_updates"]
                )
                current_revision = digest({"config": live_config, "processes": live_processes})
                if not workspace and request["expected_revision"] not in self._known_revisions(root):
                    raise VersionConflict("External project revision is not known")
                if not replayed and current_revision != request["expected_revision"]:
                    raise VersionConflict("Live project differs from the expected known revision")
                if request["state_relocation"] is not None:
                    _, destination, _, _ = self._relocation_state(
                        config_path, live_config, request["state_relocation"]
                    )
                    if not replayed and self._staging_path(destination).exists():
                        raise PoiseError(
                            "State relocation staging already exists; refusing unverified deletion"
                        )
                state = configured_root(root, live_config["paths"]["state"])
                if request['state_relocation'] is not None:
                    locks.enter_context(exclusive_lock(
                        descendant(state, live_config['batch']['artifact_lock']),
                        live_config['limits']['lock_seconds'], live_config['limits']['lock_poll_seconds']))
                state_lock = descendant(state, live_config["paths"]["lock"])
                locks.enter_context(exclusive_lock(
                    state_lock, settings.raw["lock_seconds"], settings.raw["lock_poll_seconds"]
                ))
                if request['state_relocation'] is not None:
                    # Task status is not a writer barrier: freeze the actual
                    # Requirements and asynchronous telemetry stores while copying.
                    for value in (live_config['paths']['requirements_lock'],
                                  live_config['accounting']['storage']['lock']):
                        locks.enter_context(exclusive_lock(
                            configured_storage_path(state, value), settings.raw['lock_seconds'],
                            settings.raw['lock_poll_seconds']))
                candidate_config = self._manifest(live_config, request["manifest_edits"], workspace=workspace)
                candidate_processes = self._processes(live_processes, request["process_updates"])
                if request["state_relocation"] is not None:
                    candidate_config["paths"]["state"] = request["state_relocation"]["destination"]
                before = self._component_digests(live_config, live_processes)
                after = self._component_digests(candidate_config, candidate_processes)
                if replayed:
                    if pending.get("before") is None or pending.get("after") is None:
                        raise VersionConflict("Pending publication lacks recovery digests")
                    live = self._component_digests(live_config, live_processes)
                    if live["config"] not in {pending["before"]["config"], pending["after"]["config"]}:
                        raise VersionConflict("Unmanaged manifest change during recovery")
                    for goal, value in live["processes"].items():
                        if value not in {pending["before"]["processes"].get(goal), pending["after"]["processes"].get(goal)}:
                            raise VersionConflict("Unmanaged process change during recovery")
                self._validate_candidate(root, candidate_config, candidate_processes)
                readiness = FileProjectSetup(settings)._probe(candidate_config, request["probe_repository"])
                if not replayed:
                    atomic_write(pending_path, encoded({"schema": "project-config-pending-1",
                        "request_id": request["request_id"], "request_digest": request_digest,
                        "before": before, "after": after}, settings.raw["json_indent"]),
                        settings.raw["file_mode"])
                return self._publish_candidate_locked(
                    request, config_path, live_config, live_processes, candidate_config,
                    candidate_processes, receipt_path, pending_path, replayed,
                    current_revision, readiness, locks)
        except OSError as exc:
            raise PoiseError(f"Project publication interrupted; retry the same request: {exc}") from exc
