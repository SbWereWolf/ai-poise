"""Git/filesystem effects for exact task-owned resource cleanup."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

from ..modules.foundation.errors import PoiseError
from ..modules.task_cleanup.domain import CleanupRun, TaskOwnedResource


class CleanupEffectError(PoiseError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


class RuntimeTaskResourceCleanup:
    def __init__(self, runtime):
        self.h = runtime

    def _run(self, cwd, *args, env=None):
        try:
            result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                    text=True, timeout=self.h.cfg["limits"]["git_seconds"],
                                    env=os.environ if env is None else env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoiseError(f"Git {args[0]} did not complete: {exc}") from exc
        return {"argv": ["git", "-C", str(cwd), *args], "actual_exit_code": result.returncode,
                "stdout": result.stdout[-self.h.cfg["limits"]["preview_chars"]:],
                "stderr": result.stderr[-self.h.cfg["limits"]["preview_chars"]:]}

    def _git(self, cwd, *args):
        receipt = self._run(cwd, *args)
        if receipt["actual_exit_code"]:
            raise CleanupEffectError(f"Git {args[0]}: {receipt['stderr']}", receipt)
        return receipt["stdout"].strip()

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
        return resources

    def load(self, task_id):
        record = self.h.task_queries.record(task_id)
        if record is None: raise PoiseError("Task does not exist")
        if record["status"] not in ("cancelled", "superseded"):
            raise PoiseError("Cleanup requires a terminal cancelled or superseded Task")
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

    def _branch(self, run):
        return next((item for item in run.resources if item.kind == "branch"), None)

    def validate(self, task_id: str, run: CleanupRun):
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
        return branch or run.resources[0], details

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
            path = Path(resource.path).resolve()
            root = (self.h.runtime / "task-cleanup" / task_id).resolve()
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
