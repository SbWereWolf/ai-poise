from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from ..common import descendant, file_digest
from ..execution import contains, preview, run_command
from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.result_integration.domain import IntegrationRun


class RuntimeResultIntegration:
    """Publish one accepted result from operation-owned Git resources."""

    def __init__(self, runtime):
        self.h = runtime

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

    def _load(self, task_id):
        record = self.h.task_queries.record(task_id)
        if record is None:
            raise PoiseError("Integration task does not exist")
        pending = record["pending"]
        return record, None if pending is None else IntegrationRun.restore(pending)

    def _save(self, task_id, run, expected_version):
        with self.h.store.unit_of_work() as uow:
            data, version = uow.execution.load(task_id)
            current = data["pending"]
            current_version = -1 if current is None else IntegrationRun.restore(current).version
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

    @staticmethod
    def _identity(intent):
        raw = f"{intent.task_id}\0{intent.request_id}".encode()
        return hashlib.sha256(raw).hexdigest()[:12]

    def _new_run(self, record, intent):
        identity = self._identity(intent)
        task_branch = self._short_branch(record["branch"]).replace("/", "-")
        branch = f"refs/heads/integration/{task_branch}-{identity}"
        workspace = (descendant(self.h.state, self.h.paths["worktrees"])
                     / f"{intent.task_id}-integration-{identity}")
        backup = (descendant(self.h.state, self.h.paths["runtime"]) / self.h.session
                  / "result-integration" / intent.task_id / identity)
        return IntegrationRun.new(intent, branch, str(workspace), str(backup))

    def _validate_new(self, record, intent, repository, source):
        if record["status"] != "completed":
            raise PoiseError("Only a completed accepted task result can be integrated")
        report = record["last_report"]
        if not isinstance(report, dict) or report.get("commit") != intent.expected_source_commit:
            raise PoiseError("Expected source commit does not match the completed task result")
        if not source.is_dir() or source.resolve() == repository.resolve():
            raise PoiseError("Recorded source worktree is unavailable or invalid")
        if self._git(source, "rev-parse", "HEAD") != intent.expected_source_commit:
            raise PoiseError("Source worktree HEAD changed after task completion")
        if self._git(source, "symbolic-ref", "--quiet", "--short", "HEAD") \
                != self._short_branch(record["branch"]):
            raise PoiseError("Source worktree is not on the recorded task branch")
        if self._git(source, "status", "--porcelain"):
            raise PoiseError("Source worktree must be clean before integration")
        if self._git(repository, "rev-parse", self._target_ref()) \
                != intent.expected_target_commit:
            raise PoiseError("Expected target commit changed before integration")
        if re.fullmatch(self.h.cfg["git"]["commit_pattern"], intent.authorization) is None:
            raise PoiseError("Integration commit message violates the configured pattern")

    def _ensure_workspace(self, run, repository):
        workspace = Path(run.integration_worktree)
        branch = self._short_branch(run.integration_branch)
        if workspace.exists():
            if not workspace.is_dir() or workspace.resolve() == repository.resolve():
                raise PoiseError("Owned integration workspace is invalid")
            if self._git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
                raise PoiseError("Owned integration workspace is on an unexpected branch")
            return workspace
        workspace.parent.mkdir(parents=True, exist_ok=True)
        if self._optional_ref(repository, run.integration_branch) is None:
            receipt = self._run(repository, "worktree", "add", "-b", branch,
                                str(workspace), run.observed_target)
        else:
            receipt = self._run(repository, "worktree", "add", str(workspace), branch)
        if receipt["actual_exit_code"] != 0:
            raise PoiseError(f"Cannot prepare integration workspace: {receipt['stderr']}")
        return workspace

    def _actor(self):
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": self.h.cfg["git"]["author_name"],
            "GIT_AUTHOR_EMAIL": self.h.cfg["git"]["author_email"],
            "GIT_COMMITTER_NAME": self.h.cfg["git"]["author_name"],
            "GIT_COMMITTER_EMAIL": self.h.cfg["git"]["author_email"],
        }

    def _conflicts(self, workspace):
        raw = self._git(workspace, "diff", "--name-only", "--diff-filter=U", "-z")
        return sorted(path for path in raw.split("\0") if path)

    def _contains_source(self, workspace, source, head="HEAD"):
        return self._run(workspace, "merge-base", "--is-ancestor", source, head)[
            "actual_exit_code"
        ] == 0

    def _record_candidate(self, run, workspace, receipt):
        head = self._git(workspace, "rev-parse", "HEAD")
        if not self._contains_source(workspace, run.accepted_commit, head):
            raise PoiseError("Integration candidate does not contain the accepted commit")
        ready = run.candidate_ready(head, receipt)
        self._save(run.intent.task_id, ready, run.version)
        return ready

    def _prepare_candidate(self, run, repository):
        workspace = self._ensure_workspace(run, repository)
        source = run.accepted_commit
        merge_head = self._optional_ref(workspace, "MERGE_HEAD")
        conflicts = self._conflicts(workspace)
        if run.resolutions and merge_head == source:
            for item in run.resolutions:
                path = (workspace / item["path"]).resolve()
                if not path.is_relative_to(workspace.resolve()):
                    raise PoiseError("Conflict path escapes the integration workspace")
                if path.is_file() and re.search(
                    r"(?m)^(?:<{7}|={7}|>{7})(?: |\r?$)",
                    path.read_text(errors="replace"),
                ):
                    raise PoiseError("Unresolved conflict markers remain")
            self._git(workspace, "add", "--", *sorted(run.conflicts))
            if self._conflicts(workspace):
                raise PoiseError("The Git index still contains unresolved conflicts")
            receipt = self._run(workspace, "commit", "-m", run.intent.authorization,
                                env=self._actor())
            if receipt["actual_exit_code"] != 0:
                raise PoiseError(f"Cannot commit resolved integration: {receipt['stderr']}")
            return self._record_candidate(run, workspace, receipt)
        if merge_head == source and not conflicts:
            receipt = self._run(workspace, "commit", "-m", run.intent.authorization,
                                env=self._actor())
            if receipt["actual_exit_code"] != 0:
                raise PoiseError(f"Cannot recover integration commit: {receipt['stderr']}")
            return self._record_candidate(run, workspace, {**receipt, "recovered": True})
        if conflicts and merge_head == source:
            waiting = run.await_resolution(conflicts, {"recovered": True})
            self._save(run.intent.task_id, waiting, run.version)
            return waiting
        if merge_head is not None:
            raise PoiseError("Owned integration workspace has an unrelated merge in progress")
        head = self._git(workspace, "rev-parse", "HEAD")
        if head != run.observed_target:
            if (self._contains_source(workspace, source, head)
                    and self._contains_source(workspace, run.observed_target, head)):
                return self._record_candidate(run, workspace, {"recovered": True})
            self._git(workspace, "reset", "--hard", run.observed_target)
        else:
            self._git(workspace, "reset", "--hard", run.observed_target)
        if self._contains_source(workspace, source):
            return self._record_candidate(run, workspace, {"no_op": True})
        receipt = self._run(
            workspace, "merge", "--no-ff", "--no-commit", "--no-edit", source
        )
        conflicts = self._conflicts(workspace)
        if conflicts and self._optional_ref(workspace, "MERGE_HEAD") == source:
            waiting = run.await_resolution(conflicts, receipt)
            self._save(run.intent.task_id, waiting, run.version)
            return waiting
        if receipt["actual_exit_code"] != 0:
            failed = run.candidate_failed("merge_failed_without_conflicts", receipt)
            self._save(run.intent.task_id, failed, run.version)
            return failed
        commit = self._run(workspace, "commit", "-m", run.intent.authorization,
                           env=self._actor())
        if commit["actual_exit_code"] != 0:
            raise PoiseError(f"Cannot commit integration candidate: {commit['stderr']}")
        return self._record_candidate(run, workspace, commit)

    def _select_checks(self, record):
        stage = record["process"]["stages"][record["stage_index"]]["id"]
        ids = record["contract"]["checks"][stage]
        methods = {method["id"]: method for method in record["contract"]["methods"]}
        missing = set(ids) - methods.keys()
        if missing:
            raise PoiseError(f"Unknown integration methods: {sorted(missing)}")
        return [methods[method_id] for method_id in ids]

    def _run_checks(self, record, run):
        workspace = Path(run.integration_worktree).resolve()
        receipts = []
        task_root = descendant(self.h.state, self.h.paths["tasks"]) / run.intent.task_id
        for method in self._select_checks(record):
            cwd = (workspace / method["cwd"]).resolve()
            if not cwd.is_relative_to(workspace) or not cwd.is_dir():
                raise PoiseError("Integration check cwd must stay inside its workspace")
            environment = {}
            for name in self.h.cfg["environment_names"]:
                if name not in os.environ:
                    raise PoiseError(f"Required environment variable is missing: {name}")
                environment[name] = os.environ[name]
            environment.update(method["environment"])
            check_id = str(uuid.uuid4())
            run_dir = descendant(task_root, self.h.paths["runs"]) / check_id
            result = run_command(
                method["argv"], cwd, environment, method["timeout_seconds"],
                descendant(run_dir, self.h.paths["stdout"]),
                descendant(run_dir, self.h.paths["stderr"]),
            )
            passed = (
                not result["timed_out"]
                and result["actual_exit_code"] == method["expected_exit_code"]
                and all(contains(Path(result["stdout"]), text)
                        for text in method["stdout_contains"])
                and all(contains(Path(result["stderr"]), text)
                        for text in method["stderr_contains"])
            )
            receipts.append({
                **result, "id": check_id, "method": method["id"],
                "argv": method["argv"], "cwd": str(cwd),
                "expected_exit_code": method["expected_exit_code"], "passed": passed,
                "stdout_digest": file_digest(Path(result["stdout"])),
                "stderr_digest": file_digest(Path(result["stderr"])),
                "preview": preview(Path(result["stderr"]),
                                   self.h.cfg["limits"]["preview_chars"]),
            })
        checked = run.checks_recorded(receipts)
        self._save(run.intent.task_id, checked, run.version)
        return checked

    def _publish(self, run, repository):
        target_ref = self._target_ref()
        current = self._git(repository, "rev-parse", target_ref)
        if current == run.integration_head:
            confirmed = run.publication_confirmed(
                target_ref,
                {"kind": "reconciled_target_ref", "commit": current},
                no_op=run.integration_head == run.observed_target,
                recovered=True,
            )
            self._save(run.intent.task_id, confirmed, run.version)
            return confirmed
        if current != run.observed_target:
            drifted = run.drifted({"observed": current, "expected": run.observed_target})
            self._save(run.intent.task_id, drifted, run.version)
            restarted = drifted.begin_candidate(current)
            self._save(run.intent.task_id, restarted, drifted.version)
            return restarted
        if run.integration_head == run.observed_target:
            confirmed = run.publication_confirmed(
                target_ref,
                {"kind": "accepted_commit_is_ancestor", "commit": run.integration_head},
                no_op=True,
                recovered=False,
            )
            self._save(run.intent.task_id, confirmed, run.version)
            return confirmed
        receipt = self._run(repository, "update-ref", target_ref, run.integration_head,
                            run.observed_target)
        if receipt["actual_exit_code"] != 0:
            current = self._git(repository, "rev-parse", target_ref)
            if current != run.observed_target:
                drifted = run.drifted({**receipt, "observed": current})
                self._save(run.intent.task_id, drifted, run.version)
                restarted = drifted.begin_candidate(current)
                self._save(run.intent.task_id, restarted, drifted.version)
                return restarted
            raise PoiseError(f"Atomic target publication failed: {receipt['stderr']}")
        confirmed = run.publication_confirmed(
            target_ref, receipt, no_op=False, recovered=False
        )
        self._save(run.intent.task_id, confirmed, run.version)
        return confirmed

    def _remove_worktree(self, run, component, path, repository):
        if run.cleanup[component] == "removed":
            return run
        if path.exists():
            receipt = self._run(repository, "worktree", "remove", str(path))
            if receipt["actual_exit_code"] != 0:
                blocked = run.cleanup_blocked(component, receipt)
                self._save(run.intent.task_id, blocked, run.version)
                return blocked
        completed = run.cleanup_completed(component, "removed")
        self._save(run.intent.task_id, completed, run.version)
        return completed

    def _delete_branch(self, run, component, branch, repository, target):
        if run.cleanup[component] == "deleted":
            return run
        ref = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
        commit = self._optional_ref(repository, ref)
        if commit is not None:
            ancestry = self._run(
                repository, "merge-base", "--is-ancestor", commit, target
            )
            if ancestry["actual_exit_code"] != 0:
                blocked = run.cleanup_blocked(component, {
                    **ancestry,
                    "reason": "owned_branch_is_not_merged_into_current_target",
                    "target": target,
                })
                self._save(run.intent.task_id, blocked, run.version)
                return blocked
            receipt = self._run(repository, "update-ref", "-d", ref, commit)
            if receipt["actual_exit_code"] != 0:
                blocked = run.cleanup_blocked(component, receipt)
                self._save(run.intent.task_id, blocked, run.version)
                return blocked
        completed = run.cleanup_completed(component, "deleted")
        self._save(run.intent.task_id, completed, run.version)
        return completed

    def _remove_temporary_backups(self, run):
        component = "temporary_backups"
        if run.cleanup[component] == "removed":
            return run
        path = Path(run.temporary_backup_directory)
        runtime_root = descendant(self.h.state, self.h.paths["runtime"])
        resolved = path.resolve()
        if not resolved.is_relative_to(runtime_root.resolve()) \
                or resolved == runtime_root.resolve():
            raise PoiseError("Temporary backup directory escapes the configured runtime root")
        if path.is_symlink():
            raise PoiseError("Temporary backup directory must not be a symlink")
        if path.exists():
            shutil.rmtree(path)
        completed = run.cleanup_completed(component, "removed")
        self._save(run.intent.task_id, completed, run.version)
        return completed

    def _cleanup(self, record, run, repository):
        if run.phase != "cleanup_pending":
            return run
        current_target = self._git(repository, "rev-parse", self._target_ref())
        if not self._contains_source(repository, run.target_after, current_target):
            raise PoiseError("Current target does not contain the published integration head")
        if not self._contains_source(repository, run.accepted_commit, current_target):
            raise PoiseError("Current target does not contain the accepted commit")
        for component, path in (
            ("integration_worktree", Path(run.integration_worktree)),
            ("task_worktree", Path(record["worktree"])),
        ):
            run = self._remove_worktree(run, component, path, repository)
            if run.cleanup[component] == "blocked":
                return run
        for component, branch in (
            ("integration_branch", run.integration_branch),
            ("task_branch", record["branch"]),
        ):
            run = self._delete_branch(
                run, component, branch, repository, current_target
            )
            if run.cleanup[component] == "blocked":
                return run
        return self._remove_temporary_backups(run)

    def apply(self, intent):
        repository = Path(self.h.cfg["git"]["repository"]).resolve(strict=True)
        record, run = self._load(intent.task_id)
        if run is None:
            self._validate_new(record, intent, repository, Path(record["worktree"]))
            run = self._new_run(record, intent)
            self._save(intent.task_id, run, -1)
        elif not self._same_intent(run, intent) and not self._completed_replay(run, intent):
            raise PoiseError("Task integration intent is immutable")
        if run.status == "integrated":
            return run.result(replayed=True)
        if run.phase == "checks_failed":
            return run.result()
        if run.phase == "candidate_failed":
            retried = run.retry_candidate()
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
                run = run.begin_candidate(observed)
                self._save(intent.task_id, run, previous)
            if run.phase == "preparing_candidate":
                run = self._prepare_candidate(run, repository)
                if run.phase == "awaiting_resolution":
                    return run.result()
            if run.phase == "candidate_ready":
                run = self._run_checks(record, run)
                if run.phase == "checks_failed":
                    return run.result()
            if run.phase == "publishing":
                run = self._publish(run, repository)
                if run.phase == "preparing_candidate":
                    continue
            if run.phase == "cleanup_pending":
                run = self._cleanup(record, run, repository)
            return run.result()

    def query(self, task_id, request_id):
        if (not isinstance(task_id, str) or not task_id
                or not isinstance(request_id, str) or not request_id):
            raise PoiseError("Integration query requires task_id and request_id")
        _, run = self._load(task_id)
        if run is None or run.intent.request_id != request_id:
            raise PoiseError("Result integration request was not found")
        return run.result()
