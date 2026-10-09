"""Application coordinator for exact, persisted task resource cleanup."""
from __future__ import annotations

from dataclasses import replace

from ..modules.foundation.errors import PoiseError
from ..modules.task_cleanup.domain import CleanupIntent, CleanupRun, CommitDisposition


class TaskResourceCleanup:
    def __init__(self, adapter):
        self.adapter = adapter

    def prepare_terminal(self, task_id: str, request_id: str, authorization: str) -> CleanupRun:
        resources = self.adapter.resources(task_id)
        run = CleanupRun.new(CleanupIntent(request_id, task_id, authorization, None), resources)
        try:
            self.adapter.validate(task_id, run)
        except PoiseError as exc:
            resource, receipt = self.adapter.validation_failure(task_id, run, exc)
            run = run.cleanup_blocked(resource, receipt)
        return run

    @staticmethod
    def pending(run: CleanupRun) -> dict:
        return {"kind": "task_cleanup", **run.to_storage()}

    def terminal_result(self, run: CleanupRun) -> dict:
        return run.result()

    def _load(self, task_id: str) -> CleanupRun:
        value = self.adapter.load(task_id)
        if value is None or value.get("kind") != "task_cleanup":
            raise PoiseError("Task has no pending terminal cleanup")
        return CleanupRun.restore(value)

    def query(self, task_id: str, request_id: str) -> dict:
        run = self._load(task_id)
        if run.intent.request_id != request_id:
            raise PoiseError("Unknown cleanup request identity")
        return run.result(replayed=run.complete)

    def apply(self, args: dict, *, internal=False) -> dict:
        if internal:
            return self._apply(args, internal=True)
        with self.adapter.material_scope():
            return self._apply(args, internal=False)

    def _apply(self, args, *, internal):
        intent = CleanupIntent.parse(args, internal=internal)
        value = self.adapter.load(intent.task_id)
        if value is None:
            run = self.prepare_terminal(intent.task_id, intent.request_id, intent.authorization)
            self.adapter.initialize(intent.task_id, self.pending(run))
            if run.complete:
                self.adapter.finish(intent.task_id)
                return run.result()
        elif value.get("kind") != "task_cleanup":
            raise PoiseError("Task has an incompatible pending external operation")
        else:
            run = CleanupRun.restore(value)
        if run.blocker is not None and run.blocker.get("reason") == "dirty_worktree_requires_decision":
            try:
                checkpointed = self.adapter.checkpoint(intent.task_id, run, intent)
            except PoiseError as exc:
                details = getattr(exc, "cleanup_details", None)
                if details is not None and details.get("reason") == "dirty_worktree_requires_decision":
                    blocked = run.with_blocked_intent(intent)
                    if blocked != run:
                        run = blocked
                        self.adapter.save(intent.task_id, self.pending(run))
                    return run.result()
                raise
            if checkpointed != run:
                run = checkpointed
                self.adapter.save(intent.task_id, self.pending(run))
        if run.complete and run.intent.commit_disposition.kind == "no_resources":
            if (run.intent.request_id, run.intent.task_id, run.intent.authorization) != (
                    intent.request_id, intent.task_id, intent.authorization):
                raise PoiseError("Cleanup request immutable intent changed")
            self.adapter.finish(intent.task_id)
            return run.result(replayed=True)
        if run.intent.commit_disposition is not None:
            if run.intent.request_id != intent.request_id:
                raise PoiseError("Cleanup request identity is immutable")
            if (run.intent.task_id, run.intent.authorization) != (intent.task_id, intent.authorization):
                raise PoiseError("Cleanup request immutable intent changed")
            saved = run.intent.commit_disposition.to_dict()
            received = intent.commit_disposition.to_dict()
            if run.intent.commit_disposition.bundle_path is not None:
                saved = {k: v for k, v in saved.items() if k != "bundle_path"}
            if saved != received:
                raise PoiseError("Cleanup request immutable commit disposition changed")
            if run.complete:
                self.adapter.finish(intent.task_id)
                return run.result(replayed=True)
        else:
            # A terminal transition records the resource obligation but does not
            # invent the later user's cleanup request identity or authorization.
            branch = next((resource for resource in run.resources if resource.kind == "branch"), None)
            if branch is not None and intent.commit_disposition.expected_commit != branch.commit:
                raise PoiseError("Commit disposition expected_commit does not match the terminal Task resource")
            run = replace(run, intent=intent, version=run.version + 1)
            self.adapter.save(intent.task_id, self.pending(run))

        if run.blocker is not None:
            run = run.retry_blocked()
            self.adapter.save(intent.task_id, self.pending(run))

        reason = self.adapter.material_gate(intent.task_id)
        if reason and run.resources:
            run = run.cleanup_blocked(run.resources[0], {'reason': reason})
            self.adapter.save(intent.task_id, self.pending(run))
            return run.result()

        try:
            self.adapter.validate(intent.task_id, run)
        except PoiseError as exc:
            resource, receipt = self.adapter.validation_failure(intent.task_id, run, exc)
            run = run.cleanup_blocked(resource, receipt)
            self.adapter.save(intent.task_id, self.pending(run))
            return run.result()

        disposition = run.intent.commit_disposition
        if disposition.kind == "preserved" and disposition.bundle_path is None:
            try:
                path = self.adapter.preserve(intent.task_id, run)
            except PoiseError as exc:
                resource = next((r for r in run.resources if r.kind == "branch"), run.resources[0])
                run = run.cleanup_blocked(resource, {"reason": "commit_preservation_failed", "error": str(exc)})
                self.adapter.save(intent.task_id, self.pending(run))
                return run.result()
            run = run.with_bundle(path)
            self.adapter.save(intent.task_id, self.pending(run))

        while run.resources:
            resource = run.resources[0]
            # Temporary resources may be interleaved, but a branch always follows its worktree.
            if resource.kind == "branch" and any(item.kind == "worktree" for item in run.resources):
                resource = next(item for item in run.resources if item.kind == "worktree")
            try:
                receipt = self.adapter.remove(intent.task_id, resource)
                if receipt.get("actual_exit_code", 0) != 0:
                    raise PoiseError(receipt.get("stderr") or "cleanup effect failed")
            except PoiseError as exc:
                receipt = getattr(exc, "receipt", None) or {"reason": f"{resource.kind}_cleanup_failed", "error": str(exc)}
                run = run.cleanup_blocked(resource, receipt)
                self.adapter.save(intent.task_id, self.pending(run))
                return run.result()
            run = run.resource_removed(resource, receipt)
            self.adapter.save(intent.task_id, self.pending(run))
        self.adapter.finish(intent.task_id)
        return run.result()
