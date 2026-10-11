"""Revision-aware live project configuration publication and recovery."""
from __future__ import annotations
from copy import deepcopy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import tempfile

from ..common import configured_root, configured_storage_path, descendant, digest, load_config, validate_candidate_config
from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.goal_config.domain import GoalTypeDefinition, PROCESS_FIELDS, BatchValidationError
from ..modules.projects.domain import field_at, path_key
from .goal_config import atomic_write, read_document
from .locking import exclusive_lock
from .projects import confined, ProjectSettings, FileProjectSetup


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

    def bootstrap_runtime_resources(self, request):
        """Publish the first association using only the explicit setup owner."""
        from .project_resources import require_project_association, observe_hook_association
        settings = self.settings
        config_path = self._config_path(request['config_path'])
        receipt = self._receipt(settings.root, request['receipt_path'])
        pending_path = receipt.with_name(receipt.name + '.pending')
        request_digest = digest(request)
        try:
            with exclusive_lock(settings.lock, settings.raw['lock_seconds'],
                                settings.raw['lock_poll_seconds']):
                root, config, processes = load_config(config_path)
                resources = request['runtime_resources']
                require_project_association(settings, config_path, config, resources)
                state = configured_root(root, config['paths']['state'])
                if any(path.is_relative_to(state) for path in
                       (settings.lock, settings.path, settings.registry, receipt)):
                    raise PoiseError('Bootstrap authority, fence and receipt must remain outside mutable state')
                for item in resources['hooks']:
                    observe_hook_association(item, config_path)
                revision = digest({'config': config, 'processes': processes})
                if receipt.is_file():
                    saved = read_document(receipt)
                    if (saved.get('request_digest') != request_digest or
                            saved['result']['after_revision'] != revision):
                        raise VersionConflict('Bootstrap request or live project revision changed')
                    return {**saved['result'], 'replayed': True}
                pending = read_document(pending_path) if pending_path.is_file() else None
                if pending is not None:
                    if (pending.get('request_digest') != request_digest or
                            revision not in (pending['before_revision'], pending['after_revision'])):
                        raise VersionConflict('Bootstrap pending publication conflicts with live project')
                    candidate = pending['candidate']
                    if candidate.get('runtime_resources') != resources:
                        raise VersionConflict('Bootstrap pending association changed')
                else:
                    if 'runtime_resources' in config:
                        raise PoiseError('Runtime resources association already exists')
                    if (revision != request['expected_revision'] or
                            revision not in self._known_revisions(root)):
                        raise VersionConflict('Bootstrap project revision is not the expected known revision')
                    candidate = deepcopy(config)
                    candidate['runtime_resources'] = deepcopy(resources)
                    self._validate_candidate(root, candidate, processes)
                    pending = {'schema': 'project-runtime-resources-bootstrap-pending-1',
                               'request_digest': request_digest,
                               'before_revision': revision,
                               'after_revision': digest({'config': candidate, 'processes': processes}),
                               'candidate': candidate}
                    atomic_write(pending_path, encoded(pending, settings.raw['json_indent']),
                                 settings.raw['file_mode'])
                if revision == pending['before_revision']:
                    atomic_write(config_path, encoded(candidate, settings.raw['json_indent']),
                                 settings.raw['file_mode'])
                _, final_config, final_processes = load_config(config_path)
                after = digest({'config': final_config, 'processes': final_processes})
                if after != pending['after_revision']:
                    raise VersionConflict('Bootstrap published project revision changed')
                result = {'schema': 'project-runtime-resources-bootstrap-result-1',
                          'status': 'completed', 'request_id': request['request_id'],
                          'config_path': str(config_path), 'prior_revision': pending['before_revision'],
                          'revision': after, 'before_revision': pending['before_revision'],
                          'after_revision': after, 'replayed': revision != pending['before_revision']}
                atomic_write(receipt, encoded({'schema': 'project-config-receipt-1',
                             'request_digest': request_digest, 'request_id': request['request_id'],
                             'result': result}, settings.raw['json_indent']), settings.raw['file_mode'])
                pending_path.unlink(missing_ok=True)
                return result
        except OSError as exc:
            raise PoiseError(f'Bootstrap publication interrupted; retry the same request: {exc}') from exc

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

    def _relocation_state(self, config_path, config, move, recovering=False):
        if not isinstance(move, dict) or set(move) != {"schema", "expected_source", "destination", "source_disposition", "hook_destinations"}:
            raise PoiseError("state relocation requires exact versioned fields")
        if move["schema"] != "project-state-relocation-1" or not isinstance(move["hook_destinations"], list):
            raise PoiseError("Unsupported state relocation schema or hook destinations")
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
        if destination.exists() and not already_switched and not recovering:
            if not source.exists() or not same_tree(source, destination):
                raise PoiseError("State destination must be absent or verified recovery data")
        if not source.exists() and not (already_switched and destination.is_dir()):
            raise PoiseError("State source is missing; empty replacement refused")
        return source, destination, disposition, already_switched

    @staticmethod
    def _staging_path(destination):
        return destination.parent / f".{destination.name}.project-update"

    def _relocate(self, config_path, live_config, candidate_config, move, locks, recovering,
                  pending_path, request):
        from ..application.project_state_relocation import ProjectStateRelocation
        from .project_state_relocation import FileProjectStateRelocation
        return ProjectStateRelocation(FileProjectStateRelocation(
            self, config_path, live_config, candidate_config, move, locks, pending_path, request,
        )).run()

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
                # A completed receipt replays without re-admitting retired inputs.
                # New and pending effects require complete settings validation
                # before the publication intent or any relocated-store lease.
                if request['state_relocation'] is not None:
                    from .hook_transport import HookSettings
                    for target in request['state_relocation']['hook_destinations']:
                        HookSettings.proposed(target['settings_path'], target['settings'])
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
                        config_path, live_config, request["state_relocation"], replayed
                    )
                    if not replayed and self._staging_path(destination).exists():
                        raise PoiseError(
                            "State relocation staging already exists; refusing unverified deletion"
                        )
                state = configured_root(root, live_config["paths"]["state"])
                moving = request['state_relocation'] is not None
                if moving:
                    state = Path(request['state_relocation']['expected_source']).resolve()
                if moving and state.exists():
                    locks.enter_context(exclusive_lock(
                        descendant(state, live_config['batch']['artifact_lock']),
                        live_config['limits']['lock_seconds'], live_config['limits']['lock_poll_seconds']))
                state_lock = descendant(state, live_config["paths"]["lock"])
                if not moving or state.exists():
                    locks.enter_context(exclusive_lock(
                        state_lock, settings.raw["lock_seconds"], settings.raw["lock_poll_seconds"]
                    ))
                if moving and state.exists():
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
                    if 'runtime_resources' not in candidate_config:
                        raise PoiseError('State relocation requires explicit runtime resources association')
                    candidate_config['runtime_resources']['hooks'] = [
                        {'installation_id': item['installation_id'], 'settings_path': item['settings_path']}
                        for item in request['state_relocation']['hook_destinations']]
                component_digests = lambda config, processes: {
                    "config": digest(config),
                    "processes": {goal: digest(value) for goal, value in processes.items()},
                }
                before = component_digests(live_config, live_processes)
                after = component_digests(candidate_config, candidate_processes)
                if replayed:
                    if pending.get("before") is None or pending.get("after") is None:
                        raise VersionConflict("Pending publication lacks recovery digests")
                    live = component_digests(live_config, live_processes)
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
                for goal, process in candidate_processes.items():
                    if process != live_processes[goal]:
                        atomic_write(descendant(root, candidate_config["processes"][goal]), encoded(process, settings.raw["json_indent"]), settings.raw["file_mode"])
                relocation = None
                if request["state_relocation"] is not None:
                    relocation = self._relocate(
                        config_path, live_config, candidate_config, request["state_relocation"],
                        locks, replayed, pending_path, request,
                    )
                elif candidate_config != live_config:
                    atomic_write(config_path, encoded(candidate_config, settings.raw["json_indent"]), settings.raw["file_mode"])
                _, final_config, final_processes = load_config(config_path)
                revision = digest({"config": final_config, "processes": final_processes})
                result = {"status": "updated", "changed": revision != request["expected_revision"],
                          "prior_revision": request["expected_revision"], "revision": revision,
                          "config_path": str(config_path), "readiness": readiness,
                          "receipt_path": str(receipt_path),
                          "replayed": replayed or current_revision != request["expected_revision"]}
                if relocation is not None:
                    result["state_relocation"] = relocation
                atomic_write(receipt_path, encoded({"schema": "project-config-receipt-1",
                    "request_id": request["request_id"], "request_digest": request_digest,
                    "result": result}, settings.raw["json_indent"]), settings.raw["file_mode"])
                pending_path.unlink(missing_ok=True)
                return result
        except OSError as exc:
            raise PoiseError(f"Project publication interrupted; retry the same request: {exc}") from exc
