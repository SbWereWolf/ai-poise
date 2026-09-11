from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess

from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.result_integration.domain import IntegrationRun


class RuntimeResultIntegration:
    """Git and execution-state adapter; Task lifecycle remains read-only."""

    def __init__(self, runtime):
        self.h = runtime

    def _target_ref(self):
        name = self.h.cfg["git"]["base_ref"]
        return name if name.startswith("refs/heads/") else f"refs/heads/{name}"

    def _run(self, cwd, *args, env=None):
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), *args],
                capture_output=True,
                text=True,
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
        run = None if pending is None else IntegrationRun.restore(pending)
        return record, run

    def _save(self, task_id, run, expected_version):
        with self.h.store.unit_of_work() as uow:
            data, version = uow.execution.load(task_id)
            current = data["pending"]
            current_version = -1 if current is None else IntegrationRun.restore(current).version
            if current_version != expected_version:
                raise VersionConflict("Result integration state changed concurrently")
            data["pending"] = run.to_storage()
            uow.execution.save(task_id, data, version)
        self.h.store.event(self.h.session, task_id, "result_integration.state",
                           {"request_id": run.intent.request_id, "status": run.status,
                            "version": run.version})

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

    def _validate_new(self, record, intent, target, source):
        if record["status"] != "completed":
            raise PoiseError("Only a completed accepted task result can be integrated")
        if record["config_hash"] != self.h.config_hash:
            raise PoiseError("Task result belongs to another execution configuration")
        report = record["last_report"]
        if not isinstance(report, dict) or report.get("commit") != intent.expected_source_commit:
            raise PoiseError("Expected source commit does not match the completed task result")
        if not source.is_dir() or source.resolve() == target.resolve():
            raise PoiseError("Recorded source worktree is unavailable or invalid")
        if self._git(source, "rev-parse", "HEAD") != intent.expected_source_commit:
            raise PoiseError("Source worktree HEAD changed after task completion")
        if self._git(source, "symbolic-ref", "--quiet", "--short", "HEAD") != record["branch"]:
            raise PoiseError("Source worktree is not on the recorded task branch")
        if self._git(source, "status", "--porcelain"):
            raise PoiseError("Source worktree must be clean before integration")
        target_ref = self._target_ref()
        if self._git(target, "symbolic-ref", "--quiet", "HEAD") != target_ref:
            raise PoiseError("Configured target branch is not checked out at the repository path")
        if self._git(target, "rev-parse", "HEAD") != intent.expected_target_commit:
            raise PoiseError("Expected target commit changed before integration")
        if re.fullmatch(self.h.cfg["git"]["commit_pattern"], intent.authorization) is None:
            raise PoiseError("Integration commit message violates the configured pattern")
        self._preflight(target, intent.expected_source_commit, intent.expected_target_commit)

    @staticmethod
    def _split_paths(raw):
        return {path for path in raw.split("\0") if path}

    def _dirty_paths(self, target):
        staged = self._git(target, "diff", "--cached", "--name-only", "--no-renames", "-z")
        return self._split_paths(staged) | self._worktree_paths(target)

    def _worktree_paths(self, target, env=None):
        unstaged = self._git(
            target, "diff", "--name-only", "--no-renames", "-z", env=env
        )
        untracked = self._git(
            target, "ls-files", "--others", "--exclude-standard", "-z", env=env
        )
        return self._split_paths(unstaged + untracked)

    def _incoming_paths(self, target, target_before, source):
        merge_base = self._git(target, "merge-base", target_before, source)
        raw = self._git(
            target, "diff", "--name-only", "--no-renames", "-z", merge_base, source
        )
        return self._split_paths(raw)

    @staticmethod
    def _overlapping_paths(local_paths, incoming_paths):
        overlaps = set()
        for local in local_paths:
            for incoming in incoming_paths:
                if (local == incoming or local.startswith(incoming + "/")
                        or incoming.startswith(local + "/")):
                    overlaps.update((local, incoming))
        return sorted(overlaps)

    def _git_path(self, target, name):
        path = Path(self._git(target, "rev-parse", "--git-path", name))
        return path if path.is_absolute() else target / path

    def _unresolved_operation(self, target):
        refs = (
            ("MERGE_HEAD", "git merge --abort"),
            ("CHERRY_PICK_HEAD", "git cherry-pick --abort"),
            ("REVERT_HEAD", "git revert --abort"),
        )
        for marker, recovery in refs:
            if self._optional_ref(target, marker) is not None:
                return marker, recovery
        for marker in ("rebase-merge", "rebase-apply"):
            if self._git_path(target, marker).exists():
                return marker, "git rebase --abort"
        if self._git(target, "ls-files", "--unmerged", "-z"):
            return "unmerged index", "resolve the index conflicts or abort the pending Git operation"
        return None

    def _preflight(self, target, source, expected_head):
        head = self._git(target, "rev-parse", "HEAD")
        if head != expected_head:
            raise PoiseError("Expected target commit changed before integration")
        unresolved = self._unresolved_operation(target)
        if unresolved is not None:
            marker, recovery = unresolved
            raise PoiseError(
                f"Target has unresolved Git operation {marker}; run {recovery} before integrating"
            )
        local_paths = self._dirty_paths(target)
        incoming_paths = self._incoming_paths(target, head, source)
        overlaps = self._overlapping_paths(local_paths, incoming_paths)
        if overlaps:
            raise PoiseError(
                "Target changes overlap the incoming integration paths: "
                + ", ".join(overlaps)
                + "; commit, move, remove, or resolve those local changes before integrating"
            )
        return {
            "head": head,
            "local_paths": sorted(local_paths),
            "incoming_paths": sorted(incoming_paths),
        }

    def _integration_index(self, target, intent):
        identity = f"{intent.task_id}\0{intent.request_id}".encode()
        name = f"poise-integration-{hashlib.sha256(identity).hexdigest()}.index"
        return self._git_path(target, name)

    def _index_env(self, target, intent):
        return {**os.environ, "GIT_INDEX_FILE": str(self._integration_index(target, intent))}

    def _prepare_index(self, target, intent):
        index = self._integration_index(target, intent)
        if index.exists():
            index.unlink()
        env = self._index_env(target, intent)
        self._git(target, "read-tree", "HEAD", env=env)
        return env

    def _drop_index(self, target, intent):
        index = self._integration_index(target, intent)
        if index.exists():
            index.unlink()

    def _sync_target_index(self, target, incoming_paths):
        if incoming_paths:
            self._git(
                target, "restore", "--staged", "--source=HEAD", "--",
                *sorted(incoming_paths)
            )

    @staticmethod
    def _saved_preflight(run):
        preflight = None if run.merge is None else run.merge.get("preflight")
        if not isinstance(preflight, dict):
            raise PoiseError("Pending integration preflight is missing; inspect and abort the merge")
        return preflight

    def _reject_post_preflight_drift(self, target, run, env, allowed=()):
        preflight = self._saved_preflight(run)
        permitted = set(preflight["local_paths"]) | set(allowed)
        unexpected = self._worktree_paths(target, env=env) - permitted
        if unexpected:
            raise PoiseError(
                "Target incoming paths changed after integration preflight: "
                + ", ".join(sorted(unexpected))
                + "; restore the pending merge output or abort the merge before retrying"
            )

    def _conflicts(self, target, env=None):
        raw = self._git(
            target, "diff", "--name-only", "--diff-filter=U", "-z", env=env
        )
        return sorted(path for path in raw.split("\0") if path)

    def _actor(self):
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": self.h.cfg["git"]["author_name"],
            "GIT_AUTHOR_EMAIL": self.h.cfg["git"]["author_email"],
            "GIT_COMMITTER_NAME": self.h.cfg["git"]["author_name"],
            "GIT_COMMITTER_EMAIL": self.h.cfg["git"]["author_email"],
        }

    def _integrate(self, record, run, intent, target):
        source = intent.expected_source_commit
        if run.status == "prepared":
            preflight = self._preflight(target, source, intent.expected_target_commit)
            started = run.start({"target_before": preflight["head"], "preflight": preflight})
            self._save(intent.task_id, started, run.version)
            run = started
            if self._run(target, "merge-base", "--is-ancestor", source, "HEAD")["actual_exit_code"] == 0:
                merged = run.integrated(self._git(target, "rev-parse", "HEAD"), {"no_op": True})
                self._save(intent.task_id, merged, run.version)
                return merged
            env = self._prepare_index(target, intent)
            receipt = self._run(
                target, "merge", "--no-ff", "--no-commit", "--no-edit", source, env=env
            )
            receipt["preflight"] = preflight
            conflicts = self._conflicts(target, env=env)
            if conflicts and self._optional_ref(target, "MERGE_HEAD") == source:
                waiting = run.await_resolution(conflicts, receipt)
                self._save(intent.task_id, waiting, run.version)
                return waiting
            if receipt["actual_exit_code"] != 0:
                if self._optional_ref(target, "MERGE_HEAD") is None:
                    self._drop_index(target, intent)
                blocked = run.blocked("merge_failed_without_conflicts", receipt)
                self._save(intent.task_id, blocked, run.version)
                return blocked
        elif run.status == "awaiting_resolution":
            env = self._index_env(target, intent)
            if not self._integration_index(target, intent).is_file():
                raise PoiseError("Pending conflict integration index is missing; inspect and abort the merge")
            if self._git(target, "rev-parse", "HEAD") != run.target_before \
                    or self._optional_ref(target, "MERGE_HEAD") != source:
                raise PoiseError("Pending conflict no longer belongs to this integration")
            continued = run.continue_with(intent.resolutions)
            self._reject_post_preflight_drift(
                target, run, env, allowed=run.conflicts
            )
            for item in continued.resolutions:
                path = target / item["path"]
                if not path.resolve().is_relative_to(target.resolve()):
                    raise PoiseError("Conflict path escapes the target worktree")
                if path.is_file() and re.search(r"(?m)^(?:<{7}|={7}|>{7})(?: |\r?$)", path.read_text(errors="replace")):
                    raise PoiseError("Unresolved conflict markers remain")
            self._git(target, "add", "--all", "--", *sorted(run.conflicts), env=env)
            if self._conflicts(target, env=env):
                raise PoiseError("The Git index still contains unresolved conflicts")
            self._save(intent.task_id, continued, run.version)
            run = continued
        elif run.status == "running":
            merge_head = self._optional_ref(target, "MERGE_HEAD")
            head = self._git(target, "rev-parse", "HEAD")
            source_present = self._run(
                target, "merge-base", "--is-ancestor", source, head
            )["actual_exit_code"] == 0
            if merge_head != source:
                if source_present:
                    incoming = self._incoming_paths(target, run.target_before, source)
                    self._sync_target_index(target, incoming)
                    self._drop_index(target, intent)
                    recovered = run.integrated(head, {"recovered": True})
                    self._save(intent.task_id, recovered, run.version)
                    return recovered
                raise PoiseError("Unknown merge outcome requires inspection")
        if run.status == "running":
            env = {**self._actor(), **self._index_env(target, intent)}
            self._reject_post_preflight_drift(target, run, env)
            receipt = self._run(target, "commit", "-m", intent.authorization, env=env)
            if receipt["actual_exit_code"] != 0:
                blocked = run.blocked("integration_commit_failed", receipt)
                self._save(intent.task_id, blocked, run.version)
                return blocked
            incoming = self._incoming_paths(target, run.target_before, source)
            self._sync_target_index(target, incoming)
            self._drop_index(target, intent)
            merged = run.integrated(self._git(target, "rev-parse", "HEAD"), receipt)
            self._save(intent.task_id, merged, run.version)
            return merged
        return run

    def _cleanup(self, record, run, target):
        if run.status != "cleanup_pending":
            return run
        if self._git(target, "rev-parse", "HEAD") != run.target_after:
            raise PoiseError("Target moved after integration; cleanup requires inspection")
        if self._run(target, "merge-base", "--is-ancestor", run.intent.expected_source_commit,
                     run.target_after)["actual_exit_code"] != 0:
            raise PoiseError("Integrated target does not contain the accepted source")
        source = Path(record["worktree"])
        if run.cleanup["worktree"] != "removed":
            if source.exists():
                receipt = self._run(target, "worktree", "remove", str(source))
                if receipt["actual_exit_code"] != 0:
                    blocked = run.cleanup_blocked("worktree", receipt)
                    self._save(run.intent.task_id, blocked, run.version)
                    return blocked
            removed = run.worktree_removed()
            self._save(run.intent.task_id, removed, run.version)
            run = removed
        if run.cleanup["branch"] != "deleted":
            branch_ref = f"refs/heads/{record['branch']}"
            if self._optional_ref(target, branch_ref) is not None:
                receipt = self._run(target, "branch", "-d", record["branch"])
                if receipt["actual_exit_code"] != 0:
                    blocked = run.cleanup_blocked("branch", receipt)
                    self._save(run.intent.task_id, blocked, run.version)
                    return blocked
            deleted = run.branch_deleted()
            self._save(run.intent.task_id, deleted, run.version)
            run = deleted
        return run

    def apply(self, intent):
        target = Path(self.h.cfg["git"]["repository"]).resolve(strict=True)
        record, run = self._load(intent.task_id)
        if run is None:
            self._validate_new(record, intent, target, Path(record["worktree"]))
            run = IntegrationRun.new(intent)
            self._save(intent.task_id, run, -1)
        elif not self._same_intent(run, intent) and not self._completed_replay(run, intent):
            raise PoiseError("Task integration intent is immutable")
        if run.status == "integrated":
            return run.result(replayed=True)
        if run.status in ("prepared", "running", "awaiting_resolution"):
            run = self._integrate(record, run, intent, target)
        elif run.status == "blocked":
            commit_retry = run.failure["reason"] == "integration_commit_failed"
            if commit_retry:
                env = self._index_env(target, intent)
                if (self._git(target, "rev-parse", "HEAD") != run.target_before
                        or self._optional_ref(target, "MERGE_HEAD") != intent.expected_source_commit
                        or self._conflicts(target, env=env)):
                    return run.result()
            elif (self._git(target, "rev-parse", "HEAD") != run.target_before
                  or self._optional_ref(target, "MERGE_HEAD") is not None):
                return run.result()
            retry = run.retry_blocked()
            self._save(intent.task_id, retry, run.version)
            run = self._integrate(record, retry, intent, target)
        if run.status in ("awaiting_resolution", "blocked"):
            return run.result()
        run = self._cleanup(record, run, target)
        return run.result()

    def query(self, task_id, request_id):
        if not isinstance(task_id, str) or not task_id or not isinstance(request_id, str) or not request_id:
            raise PoiseError("Integration query requires task_id and request_id")
        _, run = self._load(task_id)
        if run is None or run.intent.request_id != request_id:
            raise PoiseError("Result integration request was not found")
        return run.result()
