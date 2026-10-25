"""Revision-aware live project configuration publication and recovery."""
from __future__ import annotations
from copy import deepcopy
from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path
import shutil
import tempfile

from ..common import configured_root, configured_storage_path, descendant, digest, load_config, load_workflow_migration_config, validate_candidate_config
from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.foundation.validation import nonempty
from ..modules.goal_config.domain import GoalTypeDefinition, WORKFLOW_FIELDS, BatchValidationError
from ..modules.projects.domain import field_at, path_key
from .goal_config import atomic_write, read_document
from .locking import exclusive_lock
from .workflow_files import workflow_path
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

    def transition_harness(self, request):
        """Publish a general profile through the existing project owner.

        The selected profile's managed parent owns request-derived receipt and
        pending siblings. No additional storage root or packet field is inferred.
        """
        from ..modules.projects.harness import HarnessProject
        from .projects import publish_registry
        settings = self.settings
        paths = []
        for key in ('task_config_path', 'harness_config_path'):
            selected = Path(request[key])
            if not selected.is_absolute():
                raise PoiseError(f'{key}: select an absolute managed project path')
            try:
                relative = str(selected.relative_to(settings.root))
            except ValueError as exc:
                raise PoiseError(f'{key}: path is outside the configured project owner root') from exc
            paths.append(confined(settings.root, relative))
        task_path, profile_path = paths
        if task_path == profile_path:
            raise PoiseError('Task and general profiles require distinct paths')
        profile = HarnessProject.parse(request['profile'])
        root, task, workflows = self._source(task_path, [])
        if (task['project'] != request['project_id'] or profile.raw['project'] != task['project']
                or Path(profile.raw['git']['repository']).resolve() != Path(task['git']['repository']).resolve()
                or Path(profile.raw['runtime_resources']['project_setup']).resolve() != settings.path):
            raise PoiseError('project_binding: select matching project, repository and configuration owner')
        FileProjectSetup(settings)._probe(task, True)
        if profile.task_adapter is not None:
            if Path(profile.task_adapter.config_path).resolve() != task_path:
                raise PoiseError('task_adapter_mismatch: select the registered full Task manifest')
            general_state = configured_root(profile_path.parent, profile.raw['paths']['state'])
            task_state = configured_root(root, task['paths']['state'])
            if general_state != task_state or any(
                    descendant(general_state, profile.raw['paths'][key]) != descendant(task_state, task['paths'][key])
                    for key in ('database', 'lock')):
                raise PoiseError('task_adapter_mismatch: identity storage differs')
        receipt = profile_path.with_name(profile_path.name + '.' + digest(request['request_id']) + '.publication.json')
        pending_path = receipt.with_name(receipt.name + '.pending')
        request_digest = digest(request)
        project_state = configured_root(root, task['paths']['state'])
        project_lock = descendant(project_state, task['paths']['lock'])
        try:
            with exclusive_lock(project_lock, settings.raw['lock_seconds'], settings.raw['lock_poll_seconds']):
                if receipt.is_file():
                    saved = read_document(receipt)
                    if saved['request_digest'] != request_digest:
                        raise VersionConflict('Different transition uses the same request identity')
                    return {**saved['result'], 'replayed': True}
                with FileProjectSetup(settings).registry_transition() as registry:
                    entry = registry['projects'].get(request['project_id'])
                    if entry is None or (settings.root / entry['config_path']).resolve() != task_path:
                        raise PoiseError('project_binding: restore the existing configured Task association')
                    if 'harness_config_path' in entry and (settings.root / entry['harness_config_path']).resolve() != profile_path:
                        raise PoiseError('project_binding: selected profile differs from the registered general profile')
                    root, task, workflows = self._source(task_path, [])
                    current_profile = read_document(profile_path) if profile_path.is_file() else None
                    base = {'config': task, 'workflows': workflows}
                    if 'harness_config_path' in entry:
                        current_revision = digest({**base, 'profile': current_profile, 'registry': registry})
                    else:
                        current_revision = digest(base)
                    after_registry = deepcopy(registry)
                    after_registry['projects'][request['project_id']]['harness_config_path'] = str(profile_path.relative_to(settings.root))
                    after_revision = digest({**base, 'profile': profile.raw, 'registry': after_registry})
                    components = {'profile': digest(current_profile), 'registry': digest(registry)}
                    after = {'profile': digest(profile.raw), 'registry': digest(after_registry)}
                    if pending_path.is_file():
                        pending = read_document(pending_path)
                        if pending['request_digest'] != request_digest:
                            raise VersionConflict('Different transition uses the pending request identity')
                        if any(components[k] not in (pending['before'][k], pending['after'][k]) for k in components):
                            raise VersionConflict('Pending publication encountered foreign drift; restore its recorded components')
                        if pending['after'] != after:
                            raise VersionConflict('Pending publication source changed; restore its recorded source before retry')
                    else:
                        if current_revision != request['expected_revision']:
                            raise VersionConflict(
                                f'Live project differs from expected_revision {request["expected_revision"]}; '
                                f'actual revision {current_revision}. Review the current project/profile state, '
                                f'then submit a new request_id with expected_revision={current_revision} '
                                'and the intended profile')
                        pending = {'schema': 'project-config-pending-1', 'request_digest': request_digest,
                                   'before': components, 'after': after}
                        atomic_write(pending_path, encoded(pending, settings.raw['json_indent']), settings.raw['file_mode'])
                    atomic_write(profile_path, encoded(profile.raw, settings.raw['json_indent']), settings.raw['file_mode'])
                    publish_registry(settings, after_registry)
                    result = {'status': 'updated', 'project': request['project_id'], 'config_path': str(task_path),
                              'harness_config_path': str(profile_path), 'revision': after_revision}
                    atomic_write(receipt, encoded({'schema': 'project-config-receipt-1',
                        'request_digest': request_digest, 'result': result}, settings.raw['json_indent']), settings.raw['file_mode'])
                    pending_path.unlink()
                    return result
        except OSError as exc:
            raise PoiseError(
                f'Profile publication interrupted; retry the same request to inspect publication: {exc}') from exc

    def bootstrap_runtime_resources(self, request):
        """Publish the first association using only the explicit setup owner."""
        from .project_resources import require_project_association, observe_hook_association
        settings = self.settings
        config_path = self._config_path(request['config_path'])
        receipt = self._receipt(settings.root, request['receipt_path'])
        pending_path = receipt.with_name(receipt.name + '.pending')
        request_digest = digest(request)
        try:
            root, config, workflows = load_config(config_path)
            resources = request['runtime_resources']
            require_project_association(settings, config_path, config, resources)
            state = configured_root(root, config['paths']['state'])
            if any(path.is_relative_to(state) for path in
                   (settings.lock, settings.path, settings.registry, receipt)):
                raise PoiseError('Bootstrap authority, fence and receipt must remain outside mutable state')
            for item in resources['hooks']:
                observe_hook_association(item, config_path)
            revision = digest({'config': config, 'workflows': workflows})
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
                if revision != request['expected_revision']:
                    raise VersionConflict('Bootstrap project revision changed since observation')
                candidate = deepcopy(config)
                candidate['runtime_resources'] = deepcopy(resources)
                self._validate_candidate(root, candidate, workflows)
                pending = {'schema': 'project-runtime-resources-bootstrap-pending-1',
                           'request_digest': request_digest,
                           'before_revision': revision,
                           'after_revision': digest({'config': candidate, 'workflows': workflows}),
                           'candidate': candidate}
                atomic_write(pending_path, encoded(pending, settings.raw['json_indent']),
                             settings.raw['file_mode'])
            if revision == pending['before_revision']:
                atomic_write(config_path, encoded(candidate, settings.raw['json_indent']),
                             settings.raw['file_mode'])
            _, final_config, final_workflows = load_config(config_path)
            after = digest({'config': final_config, 'workflows': final_workflows})
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
            if not isinstance(config.get("workflows"), dict) or not config["workflows"]:
                raise failure
            root = config_path.parent
            workflows = {}
            legacy = set()
            legacy_fields = WORKFLOW_FIELDS - {"worktree_required"}
            for goal, relative in config["workflows"].items():
                workflow = read_document(workflow_path(config_path, relative))
                if set(workflow) == legacy_fields and workflow.get("goal_type") == goal:
                    legacy.add(goal)
                else:
                    GoalTypeDefinition.parse(workflow)
                workflows[goal] = workflow
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
            return root, config, workflows

    def stage_criteria_lock(self):
        """Publication uses its own store boundary; no installation-wide lease."""
        return nullcontext()

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

    def _stage_criteria_bootstrap_source_receipt(self, saved, path):
        """Admit the native first-association record as a source only."""
        result = saved.get("result")
        required = {"schema", "status", "request_id", "config_path", "prior_revision",
                    "revision", "before_revision", "after_revision", "replayed"}
        if (set(saved) != {"schema", "request_id", "request_digest", "result"}
                or saved.get("schema") != "project-config-receipt-1"
                or not isinstance(result, dict) or set(result) != required
                or result.get("schema") != "project-runtime-resources-bootstrap-result-1"
                or result.get("status") != "completed"
                or not self._receipt_digest(saved.get("request_digest"))
                or not all(self._receipt_digest(result.get(key)) for key in
                           ("prior_revision", "revision", "before_revision", "after_revision"))
                or result.get("before_revision") != result.get("prior_revision")
                or result.get("after_revision") != result.get("revision")
                or result.get("prior_revision") == result.get("revision")
                or type(result.get("replayed")) is not bool):
            raise PoiseError(f"Invalid project bootstrap receipt: {path}")
        try:
            nonempty(saved["request_id"], "request_id")
            nonempty(result["request_id"], "result.request_id")
            nonempty(result["config_path"], "result.config_path")
        except PoiseError as exc:
            raise PoiseError(f"Invalid project bootstrap receipt: {path}") from exc
        if saved["request_id"] != result["request_id"]:
            raise PoiseError(f"Invalid project bootstrap receipt: {path}")
        return result

    def _stage_criteria_source_receipt(self, saved, path):
        """Share source admission without broadening completion admission."""
        result = saved.get("result")
        if (isinstance(result, dict)
                and result.get("schema") == "project-runtime-resources-bootstrap-result-1"):
            return self._stage_criteria_bootstrap_source_receipt(saved, path)
        return self._stage_criteria_update_receipt(saved, path)

    def _stage_criteria_pending_document(self, saved):
        if (set(saved) != {"schema", "request_id", "request_digest", "before", "after"}
                or saved.get("schema") != "project-config-pending-1"
                or not isinstance(saved.get("request_id"), str) or not saved["request_id"]
                or not self._receipt_digest(saved.get("request_digest"))):
            raise PoiseError("Invalid project pending publication")
        for key in ("before", "after"):
            value = saved[key]
            if (not isinstance(value, dict) or set(value) != {"config", "workflows"}
                    or not self._receipt_digest(value.get("config"))
                    or not isinstance(value.get("workflows"), dict)
                    or not all(isinstance(k, str) and k and self._receipt_digest(v)
                               for k, v in value["workflows"].items())):
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
                result = self._stage_criteria_source_receipt(saved, path)
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
            result = self._stage_criteria_source_receipt(saved, path)
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
                                   "workflow_count", "readiness", "replayed"}
                or type(result.get("workflow_count")) is not int or result["workflow_count"] < 0
                or type(result.get("replayed")) is not bool
                or receipt["result"].get("project") != config["project"]
                or receipt["result"].get("status") != "created"
                or not sha256(receipt["result"].get("revision"))):
            raise PoiseError("Invalid project setup receipt")
        return {"kind": "project", "settings_path": str(s.path), "settings": deepcopy(s.raw),
                "root": str(s.root), "config_path": str(config_path),
                "registry": digest(registry), "setup_receipt": digest(receipt),
                "targets": {goal: str(workflow_path(config_path, value))
                            for goal, value in config["workflows"].items()}}

    def stage_criteria_snapshot(self, config_path):
        binding = self.stage_criteria_binding(config_path)
        root, config, workflows = load_workflow_migration_config(config_path)
        for directory in (self.settings.root / "operations", root / "operations"):
            for path in directory.glob("*.json.pending"):
                pending = self._stage_criteria_pending_document(read_document(path))
                if pending["before"]["config"] == digest(config):
                    raise PoiseError("Project has an unrelated pending publication")
        return {"binding": binding, "config": config, "workflows": workflows,
                "bytes": {goal: workflow_path(config_path, relative).read_bytes()
                          for goal, relative in config["workflows"].items()},
                "revision": digest({"config": config, "workflows": workflows})}

    @staticmethod
    def _component_digests(config, workflows):
        return {"config": digest(config),
                "workflows": {goal: digest(value) for goal, value in workflows.items()}}

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
        expected = digest({"config": publication["config"], "workflows": publication["candidate"]})
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
        _, config, workflows = load_config(config_path)
        if config != publication["config"] or workflows != publication["candidate"]:
            raise PoiseError("Published migration workflow drifted")
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
                "before": self._component_digests(publication["config"], publication["workflows"]),
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
        root, live_config, live_workflows = load_workflow_migration_config(config_path)
        if live_config != publication["config"]:
            raise PoiseError("Unmanaged manifest change during recovery")
        receipt_path = self._receipt(self.settings.root, request["receipt_path"])
        pending_path = receipt_path.with_name(receipt_path.name + ".pending")
        pending = self._stage_criteria_pending(publication)
        replayed = pending_path.is_file()
        if replayed:
            self._require_stage_criteria_pending(publication, pending_path)
        for goal, live in live_workflows.items():
            if live not in (publication["workflows"][goal], publication["candidate"][goal]):
                raise PoiseError("Unmanaged workflow change during recovery")
        if not replayed and live_workflows != publication["workflows"]:
            raise PoiseError("Partial project publication lacks its pending intent")
        self._validate_candidate(root, live_config, publication["candidate"])
        if not replayed:
            atomic_write(pending_path, encoded(pending, self.settings.raw["json_indent"]),
                         self.settings.raw["file_mode"])
        current_revision = digest({"config": live_config, "workflows": live_workflows})
        return self._publish_candidate_locked(
            request, config_path, live_config, live_workflows, live_config,
            publication["candidate"], receipt_path, pending_path, replayed,
            current_revision, {"repository": "not_checked", "remote": "not_checked"}, None)

    def _publish_candidate_locked(self, request, config_path, live_config, live_workflows,
                                  candidate_config, candidate_workflows, receipt_path,
                                  pending_path, replayed, current_revision, readiness, locks):
        settings = self.settings
        root = config_path.parent
        for goal, workflow in candidate_workflows.items():
            if workflow != live_workflows[goal]:
                atomic_write(workflow_path(config_path, candidate_config["workflows"][goal]),
                             encoded(workflow, settings.raw["json_indent"]), settings.raw["file_mode"])
        relocation = None
        if request["state_relocation"] is not None:
            relocation = self._relocate(config_path, live_config, candidate_config,
                                        request["state_relocation"], locks, replayed, pending_path, request)
        elif candidate_config != live_config:
            atomic_write(config_path, encoded(candidate_config, settings.raw["json_indent"]),
                         settings.raw["file_mode"])
        _, final_config, final_workflows = load_config(config_path)
        if final_config != candidate_config or final_workflows != candidate_workflows:
            raise PoiseError("Project publication readback differs from candidate")
        revision = digest({"config": final_config, "workflows": final_workflows})
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
            if path[0] in {"paths", "workflows", "project", "schema"} and not (
                    workspace and path == ('paths', 'worktrees')):
                raise PoiseError(f"Manifest field {path[0]} has a dedicated owner")
            # Named optional C004 capability, not permission to create arbitrary fields.
            if path not in (("development_routing",), ("source_reader",), ("telemetry_delivery",), ("task_planning",), ("task_ids",)):
                field_at(config, path)
            if any(path[:len(old)] == old or old[:len(path)] == path for old in changed):
                raise PoiseError("Conflicting manifest edits")
            parent = field_at(candidate, path[:-1])
            parent[path[-1]] = deepcopy(item["value"])
            changed.add(path)
        return candidate

    def _workflows(self, workflows, updates):
        candidate = deepcopy(workflows)
        seen = set()
        for item in updates:
            if not isinstance(item, dict) or set(item) != {"goal_type", "expected_revision", "changes"}:
                raise PoiseError("workflow update requires goal_type, expected_revision and changes")
            goal = item["goal_type"]
            if goal in seen or goal not in candidate:
                raise PoiseError("Unknown or duplicate workflow update")
            current = candidate[goal]
            built = GoalTypeDefinition.build_configuration(goal, current, item["changes"]).data
            if digest(current) != item["expected_revision"] and built != current:
                raise VersionConflict("Workflow revision is stale")
            candidate[goal] = built
            seen.add(goal)
        return candidate

    def _validate_candidate(self, root, config, workflows):
        from ..common import load_config_document
        manifest = root / self.settings.raw['manifest']
        load_config_document(manifest, config, workflow_documents=workflows)

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
        root, live_config, live_workflows = self._source(
            config_path, request["workflow_updates"]
        )
        receipt_path = self._receipt(settings.root, request["receipt_path"])
        pending_path = receipt_path.with_name(receipt_path.name + ".pending")
        request_digest = digest(request)
        try:
            with ExitStack() as locks:
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
                root, live_config, live_workflows = self._source(
                    config_path, request["workflow_updates"]
                )
                current_revision = digest({"config": live_config, "workflows": live_workflows})
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
                candidate_workflows = self._workflows(live_workflows, request["workflow_updates"])
                if request["state_relocation"] is not None:
                    candidate_config["paths"]["state"] = request["state_relocation"]["destination"]
                    if 'runtime_resources' not in candidate_config:
                        raise PoiseError('State relocation requires explicit runtime resources association')
                    candidate_config['runtime_resources']['hooks'] = [
                        {'installation_id': item['installation_id'], 'settings_path': item['settings_path']}
                        for item in request['state_relocation']['hook_destinations']]
                before = self._component_digests(live_config, live_workflows)
                after = self._component_digests(candidate_config, candidate_workflows)
                if replayed:
                    if pending.get("before") is None or pending.get("after") is None:
                        raise VersionConflict("Pending publication lacks recovery digests")
                    live = self._component_digests(live_config, live_workflows)
                    if live["config"] not in {pending["before"]["config"], pending["after"]["config"]}:
                        raise VersionConflict("Unmanaged manifest change during recovery")
                    for goal, value in live["workflows"].items():
                        if value not in {pending["before"]["workflows"].get(goal), pending["after"]["workflows"].get(goal)}:
                            raise VersionConflict("Unmanaged workflow change during recovery")
                self._validate_candidate(root, candidate_config, candidate_workflows)
                readiness = FileProjectSetup(settings)._probe(candidate_config, request["probe_repository"])
                if not replayed:
                    atomic_write(pending_path, encoded({"schema": "project-config-pending-1",
                        "request_id": request["request_id"], "request_digest": request_digest,
                        "before": before, "after": after}, settings.raw["json_indent"]),
                        settings.raw["file_mode"])
                return self._publish_candidate_locked(
                    request, config_path, live_config, live_workflows, candidate_config,
                    candidate_workflows, receipt_path, pending_path, replayed,
                    current_revision, readiness, locks)
        except OSError as exc:
            raise PoiseError(f"Project publication interrupted; retry the same request: {exc}") from exc
