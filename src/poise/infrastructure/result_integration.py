from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import uuid

from ..common import descendant, file_digest
from ..execution import method_passed, preview, run_command
from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.result_integration.domain import IntegrationRun
from ..modules.task_cleanup.domain import CleanupIntent, CleanupRun, CommitDisposition
from .locking import exclusive_lock
from .task_cleanup import RuntimeTaskResourceCleanup
from .task_paths import task_root


class RuntimeResultIntegration:
    """Update, test, publish, and clean one accepted task result."""

    def __init__(self, runtime):
        self.h = runtime
        self.cleanup = RuntimeTaskResourceCleanup(runtime)

    def _target_ref(self):
        name = self.h.cfg["git"]["base_ref"]
        return name if name.startswith("refs/heads/") else f"refs/heads/{name}"

    @staticmethod
    def _short_branch(ref):
        return ref.removeprefix("refs/heads/")

    def _run(self, cwd, *args, env=None):
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), *args], capture_output=True, text=True,
                timeout=self.h.cfg["limits"]["git_seconds"],
                env=os.environ if env is None else env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoiseError(f"Git {args[0]} did not complete: {exc}") from exc
        return {
            "argv": ["git", "-C", str(cwd), *args],
            "actual_exit_code": result.returncode,
            "stdout": result.stdout[-self.h.cfg["limits"]["preview_chars"]:],
            "stderr": result.stderr[-self.h.cfg["limits"]["preview_chars"]:],
        }

    def _git(self, cwd, *args, env=None):
        receipt = self._run(cwd, *args, env=env)
        if receipt["actual_exit_code"] != 0:
            raise PoiseError(f"Git {args[0]}: {receipt['stderr']}")
        return receipt["stdout"] if "-z" in args else receipt["stdout"].strip()

    def _optional_ref(self, cwd, ref):
        receipt = self._run(cwd, "rev-parse", "--verify", "--quiet", ref)
        return None if receipt["actual_exit_code"] else receipt["stdout"].strip()

    @staticmethod
    def _identity(intent):
        return hashlib.sha256(
            f"{intent.task_id}\0{intent.request_id}".encode()
        ).hexdigest()[:12]

    def _temporary_backup_directory(self, task_id, request_id):
        identity = hashlib.sha256(f"{task_id}\0{request_id}".encode()).hexdigest()[:12]
        return str(
            descendant(self.h.state, self.h.paths["runtime"])
            / "result-integration" / task_id / identity
        )

    def _load(self, task_id):
        record = self.h.task_queries.record(task_id)
        if record is None:
            raise PoiseError("Integration task does not exist")
        pending = record["pending"]
        if pending is None:
            return record, None
        if pending.get("schema") == "existing-task-worktree-1":
            return record, IntegrationRun.restore(pending)
        run = IntegrationRun.recover_legacy(
            pending, record["branch"], record["worktree"],
            self._temporary_backup_directory(task_id, pending["intent"]["request_id"]),
        )
        self._save(task_id, run, pending["version"])
        return record, run

    def _save(self, task_id, run, expected_version):
        with self.h.store.unit_of_work() as uow:
            data, version = uow.execution.load(task_id)
            current = data["pending"]
            current_version = -1 if current is None else current.get("version")
            if current_version != expected_version:
                raise VersionConflict("Result integration state changed concurrently")
            data["pending"] = run.to_storage()
            uow.execution.save(task_id, data, version)
        self.h.store.event(
            self.h.session, task_id, "result_integration.state",
            {"request_id": run.intent.request_id, "status": run.status,
             "phase": run.phase, "version": run.version},
        )

    @staticmethod
    def _same_intent(run, intent):
        return run.intent.identity() == intent.identity()

    @staticmethod
    def _completed_replay(run, intent):
        saved = run.intent.identity()
        received = intent.identity()
        saved.pop("expected_target_commit")
        received.pop("expected_target_commit")
        return (run.status == "integrated" and saved == received
                and intent.expected_target_commit == run.target_after)

    def _new_run(self, record, intent):
        return IntegrationRun.new(
            intent, record["branch"], record["worktree"],
            self._temporary_backup_directory(intent.task_id, intent.request_id),
        )

    def _actor(self):
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": self.h.cfg["git"]["author_name"],
            "GIT_AUTHOR_EMAIL": self.h.cfg["git"]["author_email"],
            "GIT_COMMITTER_NAME": self.h.cfg["git"]["author_name"],
            "GIT_COMMITTER_EMAIL": self.h.cfg["git"]["author_email"],
        }

    def _contains(self, repository, ancestor, descendant_ref="HEAD"):
        return self._run(
            repository, "merge-base", "--is-ancestor", ancestor, descendant_ref
        )["actual_exit_code"] == 0

    def _validate_source(self, record, intent, repository, run=None):
        if record["status"] != "completed":
            raise PoiseError("Only a completed accepted task result can be integrated")
        report = record["last_report"]
        if not isinstance(report, dict) or report.get("commit") != intent.expected_source_commit:
            raise PoiseError("Expected source commit does not match the completed task result")
        source = Path(record["worktree"])
        if not source.is_dir() or source.resolve() == repository.resolve():
            raise PoiseError("Recorded task worktree is unavailable or invalid")
        branch = self._git(source, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch != self._short_branch(record["branch"]):
            raise PoiseError("Task worktree is not on the recorded task branch")
        if run is None:
            if self._git(source, "rev-parse", "HEAD") != intent.expected_source_commit:
                raise PoiseError("Task worktree HEAD changed after task completion")
            if self._git(source, "status", "--porcelain"):
                raise PoiseError("Task worktree must be clean before integration")
        elif not self._contains(source, run.accepted_commit):
            raise PoiseError("Task branch no longer contains the accepted commit")
        return source

    def _validate_new(self, record, intent, repository):
        self._validate_source(record, intent, repository)
        if self._git(repository, "rev-parse", self._target_ref()) \
                != intent.expected_target_commit:
            raise PoiseError("Expected target commit changed before integration")
        if re.fullmatch(self.h.cfg["git"]["commit_pattern"], intent.authorization) is None:
            raise PoiseError("Integration commit message violates the configured pattern")

    def prepare_source(self, intent):
        repository = Path(self.h.cfg["git"]["repository"]).resolve(strict=True)
        record, run = self._load(intent.task_id)
        if run is None:
            self._validate_new(record, intent, repository)
            return record
        if not self._same_intent(run, intent) and not self._completed_replay(run, intent):
            raise PoiseError("Task integration intent is immutable")
        if run.status == "integrated" or run.phase == "cleanup_pending":
            return None
        self._validate_source(record, intent, repository, run)
        return record

    def _conflicts(self, worktree):
        raw = self._git(worktree, "diff", "--name-only", "--diff-filter=U", "-z")
        return sorted(path for path in raw.split("\0") if path)

    def _record_candidate(self, run, worktree, receipt):
        head = self._git(worktree, "rev-parse", "HEAD")
        if not self._contains(worktree, run.accepted_commit, head):
            raise PoiseError("Integration candidate does not contain the accepted commit")
        if not self._contains(worktree, run.last_included_target, head):
            raise PoiseError("Integration candidate does not contain the observed target")
        ready = run.candidate_ready(head, receipt)
        self._save(run.intent.task_id, ready, run.version)
        return ready

    def _prepare_candidate(self, run):
        worktree = Path(run.task_worktree).resolve(strict=True)
        target = run.last_included_target
        merge_head = self._optional_ref(worktree, "MERGE_HEAD")
        conflicts = self._conflicts(worktree)
        if merge_head is not None and merge_head != target:
            raise PoiseError("Task worktree has an unrelated merge in progress")
        if run.resolutions and merge_head == target:
            for item in run.resolutions:
                path = (worktree / item["path"]).resolve()
                if not path.is_relative_to(worktree):
                    raise PoiseError("Conflict path escapes the task worktree")
                if path.is_file() and re.search(
                    r"(?m)^(?:<{7}|={7}|>{7})(?: |\r?$)",
                    path.read_text(errors="replace"),
                ):
                    raise PoiseError("Unresolved conflict markers remain")
            self._git(worktree, "add", "--", *sorted(run.conflicts))
            if self._conflicts(worktree):
                raise PoiseError("The Git index still contains unresolved conflicts")
            receipt = self._run(
                worktree, "commit", "-m", run.intent.authorization, env=self._actor()
            )
            if receipt["actual_exit_code"] != 0:
                return self._candidate_failed(run, "integration_commit_failed", receipt)
            return self._record_candidate(run, worktree, receipt)
        if merge_head == target and not conflicts:
            receipt = self._run(
                worktree, "commit", "-m", run.intent.authorization, env=self._actor()
            )
            if receipt["actual_exit_code"] != 0:
                return self._candidate_failed(run, "integration_commit_failed", receipt)
            return self._record_candidate(run, worktree, {**receipt, "recovered": True})
        if conflicts and merge_head == target:
            waiting = run.await_resolution(conflicts, {"recovered": True})
            self._save(run.intent.task_id, waiting, run.version)
            return waiting
        head = self._git(worktree, "rev-parse", "HEAD")
        if self._contains(worktree, run.accepted_commit, head) \
                and self._contains(worktree, target, head):
            return self._record_candidate(run, worktree, {"no_op": True, "recovered": True})
        if not self._contains(worktree, run.accepted_commit, head):
            raise PoiseError("Task branch no longer contains the accepted commit")
        if self._git(worktree, "status", "--porcelain"):
            raise PoiseError("Task worktree must be clean before updating from the target")
        receipt = self._run(
            worktree, "merge", "--no-ff", "--no-commit", "--no-edit", target
        )
        conflicts = self._conflicts(worktree)
        if conflicts and self._optional_ref(worktree, "MERGE_HEAD") == target:
            waiting = run.await_resolution(conflicts, receipt)
            self._save(run.intent.task_id, waiting, run.version)
            return waiting
        if receipt["actual_exit_code"] != 0:
            return self._candidate_failed(run, "merge_failed_without_conflicts", receipt)
        if self._optional_ref(worktree, "MERGE_HEAD") is None:
            return self._record_candidate(run, worktree, receipt)
        commit = self._run(
            worktree, "commit", "-m", run.intent.authorization, env=self._actor()
        )
        if commit["actual_exit_code"] != 0:
            return self._candidate_failed(run, "integration_commit_failed", commit)
        return self._record_candidate(run, worktree, commit)

    def _candidate_failed(self, run, reason, receipt):
        failed = run.candidate_failed(reason, receipt)
        self._save(run.intent.task_id, failed, run.version)
        return failed

    def _select_checks(self, record):
        stage = record["process"]["stages"][record["stage_index"]]["id"]
        ids = record["contract"]["checks"][stage]
        methods = {method["id"]: method for method in record["contract"]["methods"]}
        missing = set(ids) - methods.keys()
        if missing:
            raise PoiseError(f"Unknown integration methods: {sorted(missing)}")
        return [methods[method_id] for method_id in ids]

    def _run_checks(self, record, run):
        worktree = Path(run.task_worktree).resolve(strict=True)
        receipts = []
        owner_root = task_root(
            self.h.state,
            self.h.paths,
            run.intent.task_id,
            record["sprint_id"],
        )
        for method in self._select_checks(record):
            cwd = (worktree / method["cwd"]).resolve()
            if not cwd.is_relative_to(worktree) or not cwd.is_dir():
                raise PoiseError("Integration check cwd must stay inside the task worktree")
            environment = {}
            for name in self.h.cfg["environment_names"]:
                if name not in os.environ:
                    raise PoiseError(f"Required environment variable is missing: {name}")
                environment[name] = os.environ[name]
            environment.update(method["environment"])
            check_id = str(uuid.uuid4())
            run_dir = descendant(owner_root, self.h.paths["runs"]) / check_id
            result = run_command(
                method["argv"], cwd, environment, None,
                descendant(run_dir, self.h.paths["stdout"]),
                descendant(run_dir, self.h.paths["stderr"]),
            )
            receipts.append({
                **result, "id": check_id, "method": method["id"],
                "argv": method["argv"], "cwd": str(cwd),
                "expected_exit_code": method["expected_exit_code"],
                "passed": method_passed(method, result),
                "stdout_digest": file_digest(Path(result["stdout"])),
                "stderr_digest": file_digest(Path(result["stderr"])),
                "preview": preview(Path(result["stderr"]),
                                   self.h.cfg["limits"]["preview_chars"]),
            })
        checked = run.checks_recorded(receipts)
        self._save(run.intent.task_id, checked, run.version)
        return checked

    def _publication_lock(self):
        identity = hashlib.sha256(self._target_ref().encode()).hexdigest()[:16]
        return (descendant(self.h.state, self.h.paths["runtime"])
                / "result-integration" / "publication" / f"{identity}.lock")

    def _operation_refs(self, repository):
        result = {}
        for name in (
            "MERGE_HEAD", "MERGE_AUTOSTASH", "AUTO_MERGE", "CHERRY_PICK_HEAD",
            "REVERT_HEAD", "REBASE_HEAD", "BISECT_HEAD", "sequencer",
            "rebase-merge", "rebase-apply",
        ):
            path = Path(self._git(repository, "rev-parse", "--git-path", name))
            if not path.is_absolute():
                path = repository / path
            if path.is_file():
                result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                result[name] = sorted(
                    (str(item.relative_to(path)), hashlib.sha256(item.read_bytes()).hexdigest())
                    for item in path.rglob("*") if item.is_file()
                )
            else:
                result[name] = None
        return result

    def _main_fingerprint(self, repository):
        index = Path(self._git(repository, "rev-parse", "--git-path", "index"))
        if not index.is_absolute():
            index = repository / index
        raw = self._git(
            repository, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
        )
        files = {}
        for name in sorted(path for path in raw.split("\0") if path):
            path = repository / name
            if not path.exists() and not path.is_symlink():
                files[name] = {"kind": "missing"}
                continue
            info = path.lstat()
            if path.is_symlink():
                content, kind = os.readlink(path).encode(), "symlink"
            elif path.is_file():
                content, kind = path.read_bytes(), "file"
            else:
                content, kind = b"", "other"
            files[name] = {
                "kind": kind, "mode": stat.S_IMODE(info.st_mode),
                "digest": hashlib.sha256(content).hexdigest(),
            }
        branch = self._run(repository, "symbolic-ref", "--quiet", "HEAD")
        return {
            "head": self._git(repository, "rev-parse", "HEAD"),
            "branch": (branch["stdout"].strip()
                       if branch["actual_exit_code"] == 0 else None),
            "index": hashlib.sha256(index.read_bytes()).hexdigest(),
            "files": files,
            "status": hashlib.sha256(
                self._git(repository, "status", "--porcelain=v2", "-z").encode()
            ).hexdigest(),
            "operations": self._operation_refs(repository),
        }

    def _publish(self, run, repository):
        target_ref = self._target_ref()
        with exclusive_lock(
            self._publication_lock(), self.h.cfg["limits"]["lock_seconds"],
            self.h.cfg["limits"]["lock_poll_seconds"],
        ):
            current = self._git(repository, "rev-parse", target_ref)
            if current == run.integration_head:
                confirmed = run.publication_confirmed(
                    target_ref, {"kind": "reconciled_fast_forward", "commit": current},
                    no_op=run.integration_head == run.last_included_target, recovered=True,
                )
                self._save(run.intent.task_id, confirmed, run.version)
                return confirmed
            if current != run.last_included_target:
                drifted = run.drifted({"observed": current,
                                       "expected": run.last_included_target})
                self._save(run.intent.task_id, drifted, run.version)
                restarted = drifted.begin_update(current)
                self._save(run.intent.task_id, restarted, drifted.version)
                return restarted
            branch_head = self._git(repository, "rev-parse", run.task_branch)
            if branch_head != run.integration_head:
                return self._publication_failed(
                    run, "task_branch_changed",
                    {"expected": run.integration_head, "observed": branch_head},
                )
            if not self._contains(repository, current, run.integration_head):
                return self._publication_failed(
                    run, "candidate_is_not_fast_forward",
                    {"target": current, "candidate": run.integration_head},
                )
            before = self._main_fingerprint(repository)
            if before["branch"] != target_ref:
                after = self._main_fingerprint(repository)
                return self._publication_failed(
                    run, "target_not_checked_out",
                    {"target_ref": target_ref, "worktree_unchanged": before == after,
                     "before": before, "after": after},
                )
            receipt = self._run(
                repository, "merge", "--ff-only", self._short_branch(run.task_branch)
            )
            if receipt["actual_exit_code"] != 0:
                observed = self._git(repository, "rev-parse", target_ref)
                if observed != current:
                    drifted = run.drifted({**receipt, "observed": observed})
                    self._save(run.intent.task_id, drifted, run.version)
                    restarted = drifted.begin_update(observed)
                    self._save(run.intent.task_id, restarted, drifted.version)
                    return restarted
                after = self._main_fingerprint(repository)
                unchanged = before == after
                return self._publication_failed(
                    run,
                    "fast_forward_blocked" if unchanged
                    else "publication_changed_main_worktree",
                    {**receipt, "worktree_unchanged": unchanged,
                     "before": before, "after": after},
                )
            observed = self._git(repository, "rev-parse", target_ref)
            if observed != run.integration_head:
                raise PoiseError("Successful fast-forward did not publish the candidate head")
            confirmed = run.publication_confirmed(
                target_ref, receipt,
                no_op=run.integration_head == run.last_included_target,
                recovered=False,
            )
            self._save(run.intent.task_id, confirmed, run.version)
            return confirmed

    def _publication_failed(self, run, reason, receipt):
        blocked = run.publication_blocked(reason, receipt)
        self._save(run.intent.task_id, blocked, run.version)
        return blocked

    def _remove_temporary_backups(self, run):
        component = "temporary_backups"
        if run.cleanup[component] == "removed":
            return run
        path = Path(run.temporary_backup_directory)
        runtime_root = descendant(self.h.state, self.h.paths["runtime"])
        resolved = path.resolve()
        if (not resolved.is_relative_to(runtime_root.resolve())
                or resolved == runtime_root.resolve() or path.is_symlink()):
            raise PoiseError("Temporary backup directory escapes the configured runtime root")
        if path.exists():
            shutil.rmtree(path)
        completed = run.cleanup_completed(component, "removed")
        self._save(run.intent.task_id, completed, run.version)
        return completed

    def _cleanup(self, run, repository):
        if run.phase != "cleanup_pending":
            return run
        current_target = self._git(repository, "rev-parse", self._target_ref())
        if not self._contains(repository, run.target_after, current_target):
            raise PoiseError("Current target does not contain the published integration head")
        if not self._contains(repository, run.accepted_commit, current_target):
            raise PoiseError("Current target does not contain the accepted commit")
        disposition = CommitDisposition(
            "integrated",
            expected_commit=run.accepted_commit,
            target_commit=run.target_after,
            integration_request_id=run.intent.request_id,
        )
        task_cleanup = CleanupRun.new(
            CleanupIntent(
                run.intent.request_id,
                run.intent.task_id,
                run.intent.authorization,
                disposition,
            ),
            self.cleanup.resources(run.intent.task_id),
        )
        try:
            self.cleanup.validate(run.intent.task_id, task_cleanup)
        except PoiseError as exc:
            resource, receipt = self.cleanup.validation_failure(
                run.intent.task_id, task_cleanup, exc
            )
            component = {
                "worktree": "task_worktree",
                "branch": "task_branch",
                "temporary": "temporary_backups",
                "temporary_backup": "temporary_backups",
            }[resource.kind]
            blocked = run.cleanup_blocked(
                component, {**receipt, "resource": resource.to_dict(public=True)}
            )
            self._save(run.intent.task_id, blocked, run.version)
            return blocked
        for resource in task_cleanup.resources:
            component = {
                "worktree": "task_worktree",
                "branch": "task_branch",
                "temporary": "temporary_backups",
                "temporary_backup": "temporary_backups",
            }[resource.kind]
            try:
                receipt = self.cleanup.remove(run.intent.task_id, resource)
            except PoiseError as exc:
                receipt = getattr(exc, "receipt", None) or {
                    "reason": f"{resource.kind}_cleanup_failed",
                    "error": str(exc),
                }
                blocked = run.cleanup_blocked(
                    component, {**receipt, "resource": resource.to_dict(public=True)}
                )
                self._save(run.intent.task_id, blocked, run.version)
                return blocked
            if resource.kind == "worktree":
                advanced = run.cleanup_completed("task_worktree", "removed")
                self._save(run.intent.task_id, advanced, run.version)
                run = advanced
            elif resource.kind == "branch":
                advanced = run.cleanup_completed("task_branch", "deleted")
                self._save(run.intent.task_id, advanced, run.version)
                run = advanced
        for component, outcome in (("task_worktree", "removed"),
                                   ("task_branch", "deleted")):
            if run.cleanup[component] != outcome:
                advanced = run.cleanup_completed(component, outcome)
                self._save(run.intent.task_id, advanced, run.version)
                run = advanced
        run = self._remove_temporary_backups(run)
        if run.status == "integrated":
            self.cleanup.finish(run.intent.task_id)
        return run

    def apply(self, intent):
        repository = Path(self.h.cfg["git"]["repository"]).resolve(strict=True)
        record, run = self._load(intent.task_id)
        if run is None:
            self._validate_new(record, intent, repository)
            run = self._new_run(record, intent)
            self._save(intent.task_id, run, -1)
        elif not self._same_intent(run, intent) and not self._completed_replay(run, intent):
            raise PoiseError("Task integration intent is immutable")
        if run.status == "integrated":
            return run.result(replayed=True)
        if run.phase == "checks_failed":
            retried = run.retry_checks()
            self._save(intent.task_id, retried, run.version)
            run = retried
        if run.phase == "candidate_failed":
            retried = run.retry_candidate()
            self._save(intent.task_id, retried, run.version)
            run = retried
        if run.phase == "publication_failed":
            retried = run.retry_publication()
            self._save(intent.task_id, retried, run.version)
            run = retried
        if run.phase == "awaiting_resolution":
            if not intent.resolutions:
                return run.result()
            continued = run.continue_with(intent.resolutions)
            self._save(intent.task_id, continued, run.version)
            run = continued
        while True:
            if run.phase == "prepared":
                observed = self._git(repository, "rev-parse", self._target_ref())
                previous = run.version
                run = run.begin_update(observed)
                self._save(intent.task_id, run, previous)
            if run.phase == "updating":
                run = self._prepare_candidate(run)
                if run.phase in ("awaiting_resolution", "candidate_failed"):
                    return run.result()
            if run.phase == "candidate_ready":
                run = self._run_checks(record, run)
                if run.phase == "checks_failed":
                    return run.result()
            if run.phase == "publishing":
                run = self._publish(run, repository)
                if run.phase == "updating":
                    continue
                if run.phase == "publication_failed":
                    return run.result()
            if run.phase == "cleanup_pending":
                run = self._cleanup(run, repository)
            return run.result()

    def query(self, task_id, request_id):
        if (not isinstance(task_id, str) or not task_id
                or not isinstance(request_id, str) or not request_id):
            raise PoiseError("Integration query requires task_id and request_id")
        _, run = self._load(task_id)
        if run is None or run.intent.request_id != request_id:
            raise PoiseError("Result integration request was not found")
        return run.result()
