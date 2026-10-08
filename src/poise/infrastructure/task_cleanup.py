"""Git/filesystem effects for exact task-owned resource cleanup."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..artifacts import inspect_paths
from ..modules.foundation.errors import PoiseError
from ..modules.task_cleanup.domain import CleanupIntent, CleanupRun, TaskOwnedResource
from .git_transport import run_git_receipt


class CleanupEffectError(PoiseError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


class RuntimeTaskResourceCleanup:
    def __init__(self, runtime):
        self.h = runtime

    def _run(self, cwd, *args, env=None):
        return run_git_receipt(cwd, args, self.h.cfg['limits']['git_seconds'],
                               os.environ if env is None else env)

    def _git(self, cwd, *args):
        receipt = self._run(cwd, *args)
        if receipt["actual_exit_code"]:
            raise CleanupEffectError(f"Git {args[0]}: {receipt['stderr']}", receipt)
        return receipt['stdout'] if '-z' in args else receipt['stdout'].removesuffix('\n')

    def resource_root(self, task_id: str, kind: str | None = None) -> Path:
        root = (self.h.runtime.parent / "task-cleanup" / task_id).resolve()
        if kind is None:
            return root
        if kind not in ("temporary", "temporary_backup"):
            raise PoiseError("Only temporary cleanup resource kinds can be registered")
        return root / kind

    def register(self, task_id: str, kind: str, path: Path | str) -> dict:
        if self.h.task_queries.record(task_id) is None:
            raise PoiseError("Task does not exist")
        root = self.resource_root(task_id, kind)
        candidate = Path(path)
        if candidate.is_symlink():
            raise PoiseError("Registered cleanup resource cannot be a symlink")
        records = inspect_paths([str(candidate)], {"runtime": root}, {"runtime": task_id})
        record = records[0]
        descriptor = {"kind": kind, "path": record["path"], "digest": record["digest"]}
        packed = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        registry = self.resource_root(task_id) / ".registry"
        if registry.is_symlink():
            raise PoiseError("Cleanup resource registry cannot be a symlink")
        registry.mkdir(parents=True, exist_ok=True)
        target = registry / f"{hashlib.sha256(packed.encode()).hexdigest()}.json"
        if target.exists():
            if target.is_symlink() or target.read_text(encoding="utf-8") != packed + "\n":
                raise PoiseError("Cleanup resource registration identity changed")
        else:
            target.write_text(packed + "\n", encoding="utf-8")
        return descriptor

    def _registered(self, task_id: str) -> list[dict]:
        registry = self.resource_root(task_id) / ".registry"
        if not registry.exists():
            return []
        if registry.is_symlink() or not registry.is_dir():
            raise PoiseError("Cleanup resource registry ownership changed")
        resources = []
        for source in sorted(registry.iterdir()):
            if source.is_symlink() or not source.is_file() or source.suffix != ".json":
                raise PoiseError("Cleanup resource registry contains an unknown entry")
            try:
                value = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PoiseError(f"Cleanup resource registration is unreadable: {exc}") from exc
            owned = TaskOwnedResource.parse(value)
            if owned.kind not in ("temporary", "temporary_backup"):
                raise PoiseError("Cleanup resource registry contains a non-temporary resource")
            packed = json.dumps(owned.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if source.name != f"{hashlib.sha256(packed.encode()).hexdigest()}.json":
                raise PoiseError("Cleanup resource registration digest changed")
            if not Path(owned.path).resolve().is_relative_to(self.resource_root(task_id, owned.kind)):
                raise PoiseError("Registered cleanup resource escaped its task-owned root")
            resources.append(owned.to_dict())
        return resources

    def resources(self, task_id: str) -> list[dict]:
        record = self.h.task_queries.record(task_id)
        if record is None: raise PoiseError("Task does not exist")
        resources = []
        worktree = record.get("worktree")
        if worktree is not None and Path(worktree).is_dir():
            expected = (self.h.state / self.h.paths["worktrees"] / task_id).resolve()
            if Path(worktree).is_symlink() or Path(worktree).resolve() != expected:
                raise PoiseError("Recorded worktree is outside the exact task-owned path")
            identity = str(Path(self._git(Path(worktree), "rev-parse", "--git-dir")).resolve())
            resources.append({"kind": "worktree", "identity": identity, "path": str(Path(worktree))})
        branch = record.get("branch")
        commit = None
        if branch:
            base = self.h.cfg["git"]["base_ref"]
            if branch == base or f"refs/heads/{branch}" == base:
                raise PoiseError("Task cleanup cannot own the configured target branch")
            observed = self._run(Path(self.h.cfg["git"]["repository"]), "rev-parse", "--verify", f"refs/heads/{branch}")
            if not observed["actual_exit_code"]: commit = observed["stdout"].strip()
        if branch and commit:
            resources.append({"kind": "branch", "name": branch, "commit": commit})
        resources.extend(sorted(self._registered(task_id), key=lambda item: (item["kind"], item["path"])))
        return resources

    def load(self, task_id):
        record = self.h.task_queries.record(task_id)
        if record is None: raise PoiseError("Task does not exist")
        if record["status"] != "cancelled":
            raise PoiseError("Cleanup requires a terminal cancelled Task")
        return record["pending"]

    def save(self, task_id, pending):
        with self.h.store.unit_of_work() as uow:
            data, version = uow.execution.load(task_id)
            current = data["pending"]
            if not isinstance(current, dict) or current.get("kind") != "task_cleanup":
                raise PoiseError("Task cleanup state changed concurrently")
            saved = CleanupRun.restore(current)
            received = CleanupRun.restore(pending)
            if received.version != saved.version + 1:
                if pending == current: return
                raise PoiseError("Task cleanup state changed concurrently")
            data["pending"] = pending
            uow.execution.save(task_id, data, version)

    def initialize(self, task_id, pending):
        with self.h.store.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            if task.state.status.value != "cancelled":
                raise PoiseError("Cleanup initialization requires a terminal cancelled Task")
            data, version = uow.execution.load(task_id)
            if data["pending"] is not None:
                raise PoiseError("Task acquired a pending external operation during cleanup initialization")
            data["pending"] = pending
            uow.execution.save(task_id, data, version)

    def finish(self, task_id):
        root = self.resource_root(task_id)
        registry = root / ".registry"
        if registry.is_dir() and not registry.is_symlink():
            for source in registry.iterdir():
                if source.is_symlink() or not source.is_file():
                    continue
                try:
                    owned = TaskOwnedResource.parse(json.loads(source.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError, PoiseError):
                    continue
                path = Path(owned.path)
                if not path.exists() and not path.is_symlink():
                    source.unlink()
            try:
                registry.rmdir()
            except OSError:
                pass
        for kind in ("temporary", "temporary_backup"):
            directory = root / kind
            if directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    pass
        if root.is_dir():
            try:
                root.rmdir()
            except OSError:
                pass

    def _branch(self, run):
        return next((item for item in run.resources if item.kind == "branch"), None)

    def _validate_worktree(self, task_id: str, run: CleanupRun):
        worktree = next((item for item in run.resources if item.kind == "worktree"), None)
        if worktree is None or not Path(worktree.path).exists():
            return worktree
        path = Path(worktree.path)
        expected = (self.h.state / self.h.paths["worktrees"] / task_id).resolve()
        if path.is_symlink() or path.resolve() != expected:
            error = PoiseError("owned worktree path changed after terminal transition")
            error.cleanup_resource = worktree
            error.cleanup_details = {
                "reason": "owned_resource_changed",
                "recovery": "Restore the exact task worktree ownership before replaying cleanup.",
            }
            raise error
        identity = str(Path(self._git(path, "rev-parse", "--git-dir")).resolve())
        if identity != worktree.identity:
            error = PoiseError("owned worktree identity changed after terminal transition")
            error.cleanup_resource = worktree
            error.cleanup_details = {
                "reason": "owned_resource_changed",
                "recovery": "Restore the exact task worktree ownership before replaying cleanup.",
            }
            raise error
        if self._git(path, "status", "--porcelain", "--untracked-files=all"):
            error = PoiseError("owned worktree contains uncommitted changes")
            error.cleanup_resource = worktree
            error.cleanup_details = {
                "reason": "dirty_worktree_requires_decision",
                "recovery": (
                    "Commit all worktree changes and submit a new cleanup request whose disposition "
                    "names that checkpoint commit, or discard the changes and replay the existing request."
                ),
            }
            raise error
        return worktree

    def checkpoint(self, task_id: str, run: CleanupRun, intent: CleanupIntent) -> CleanupRun:
        worktree = self._validate_worktree(task_id, run)
        branch = self._branch(run)
        if worktree is None or branch is None:
            raise PoiseError("Dirty-worktree checkpoint requires the exact worktree and branch resources")
        path = Path(worktree.path)
        actual_branch = self._git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
        if actual_branch != branch.name:
            raise PoiseError("Checkpoint worktree is not on the exact task branch")
        actual_commit = self._git(path, "rev-parse", "HEAD")
        disposition = intent.commit_disposition
        if disposition is None or disposition.expected_commit != actual_commit:
            raise PoiseError("Checkpoint cleanup disposition must name the current task branch commit")
        if actual_commit == branch.commit:
            return run
        ancestry = self._run(path, "merge-base", "--is-ancestor", branch.commit, actual_commit)
        if ancestry["actual_exit_code"] != 0:
            raise PoiseError("Checkpoint commit must be a fast-forward descendant of the terminal commit")
        checkpoint = TaskOwnedResource("branch", name=branch.name, commit=actual_commit)
        return run.checkpointed(checkpoint, intent)

    def validate(self, task_id: str, run: CleanupRun):
        self._validate_worktree(task_id, run)
        branch = self._branch(run)
        if branch is None: return
        repository = Path(self.h.cfg["git"]["repository"])
        receipt = self._run(repository, "rev-parse", "--verify", f"refs/heads/{branch.name}")
        actual = None if receipt["actual_exit_code"] else receipt["stdout"].strip()
        # Absence may be the persisted replay of a successful exact deletion
        # whose following state write was interrupted. A different live commit
        # is ownership drift and must block before any cleanup effect.
        if actual is not None and actual != branch.commit:
            error = PoiseError("owned branch changed after terminal transition")
            error.cleanup_details = {"reason": "owned_resource_changed", "expected_commit": branch.commit,
                                     "actual_commit": actual, "recovery": "Preserve or reconcile the changed branch, then create a new authorized cleanup decision."}
            raise error

    def validation_failure(self, task_id, run, exc):
        branch = self._branch(run)
        details = getattr(exc, "cleanup_details", {"reason": "ownership_validation_failed", "error": str(exc)})
        resource = getattr(exc, "cleanup_resource", None)
        return resource or branch or run.resources[0], details

    def preserve(self, task_id: str, run: CleanupRun) -> str:
        branch = self._branch(run)
        if branch is None: raise PoiseError("No task commit reference to preserve")
        record = self.h.task_queries.record(task_id)
        root = self.h._roots(record)["task"] / "cleanup"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{run.intent.request_id}.bundle"
        if path.is_symlink(): raise PoiseError("Preservation bundle path is a symlink")
        if not path.exists():
            self._git(Path(self.h.cfg["git"]["repository"]), "bundle", "create", str(path), f"refs/heads/{branch.name}")
        self._git(Path(self.h.cfg["git"]["repository"]), "bundle", "verify", str(path))
        heads = self._git(Path(self.h.cfg["git"]["repository"]), "bundle", "list-heads", str(path))
        if not any(line.split(maxsplit=1)[0] == branch.commit for line in heads.splitlines() if line.strip()):
            raise PoiseError("Preservation bundle does not contain the exact terminal commit")
        self.h.register_artifact_paths([str(path)], record)
        return str(path)

    def remove(self, task_id: str, resource: TaskOwnedResource) -> dict:
        if resource.kind in ("temporary", "temporary_backup"):
            raw = Path(resource.path)
            if raw.is_symlink():
                raise PoiseError("registered resource ownership changed to a symlink")
            path = raw.resolve()
            root = self.resource_root(task_id, resource.kind).resolve()
            if not path.is_relative_to(root): raise PoiseError("resource path is outside task-owned runtime root")
            if not path.is_file(): return {"status": "removed", "already_absent": True}
            if hashlib.sha256(path.read_bytes()).hexdigest() != resource.digest:
                raise PoiseError("registered resource digest changed")
            path.unlink()
            return {"status": "removed", "path": str(path)}
        repository = Path(self.h.cfg["git"]["repository"])
        if resource.kind == "worktree":
            path = Path(resource.path)
            if not path.exists(): return {"status": "removed", "already_absent": True}
            expected = (self.h.state / self.h.paths["worktrees"] / task_id).resolve()
            if path.is_symlink() or path.resolve() != expected:
                raise PoiseError("worktree path is not the exact task-owned path")
            actual = str(Path(self._git(path, "rev-parse", "--git-dir")).resolve())
            if actual != resource.identity: raise PoiseError("worktree owner identity changed")
            receipt = self._run(repository, "worktree", "remove", str(path))
        else:
            receipt = self._run(repository, "update-ref", "-d", f"refs/heads/{resource.name}", resource.commit)
        if receipt["actual_exit_code"]:
            receipt["reason"] = f"{resource.kind}_cleanup_failed"
            receipt["recovery"] = "Resolve the Git lock or ownership issue and replay this exact cleanup request."
            raise CleanupEffectError(receipt["stderr"] or "cleanup effect failed", receipt)
        return {**receipt, "status": "removed"}
