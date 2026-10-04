from __future__ import annotations

import hashlib
import json
import locale
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

from ..common import descendant, digest, file_digest, prohibit_git_push
from ..execution import capture_declared_outputs, inspect_declared_output_receipts, method_passed, preview, run_command
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
        prohibit_git_push(["git", *args])
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), *args], capture_output=True,
                timeout=self.h.cfg["limits"]["git_seconds"],
                env=os.environ if env is None else env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoiseError(f"Git {args[0]} did not complete: {exc}") from exc
        # Match subprocess text encoding without translating path CR/CRLF.
        encoding = "utf-8" if sys.flags.utf8_mode else locale.getencoding()
        return {
            "argv": ["git", "-C", str(cwd), *args],
            "actual_exit_code": result.returncode,
            "stdout": result.stdout.decode(encoding, errors="strict"),
            "stderr": result.stderr.decode(encoding, errors="strict"),
        }

    def _git(self, cwd, *args, env=None):
        receipt = self._run(cwd, *args, env=env)
        if receipt["actual_exit_code"] != 0:
            raise PoiseError(f"Git {args[0]}: {receipt['stderr']}")
        return receipt["stdout"] if "-z" in args else receipt["stdout"].removesuffix("\n")

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

    def _read_state(self, task_id):
        """Decode through the existing domain owner without persisting recovery."""
        record = self.h.task_queries.record(task_id)
        if record is None:
            raise PoiseError("Integration task does not exist")
        pending = record["pending"]
        if pending is None:
            return record, None, None
        if not isinstance(pending, dict):
            raise PoiseError("Saved result integration state is invalid")
        try:
            if pending.get("schema") == "existing-task-worktree-1":
                return record, IntegrationRun.restore(pending), None
            run = IntegrationRun.recover_legacy(
                pending, record["branch"], record["worktree"],
                self._temporary_backup_directory(task_id, pending["intent"]["request_id"]),
            )
            return record, run, pending["version"]
        except (KeyError, TypeError, ValueError) as exc:
            raise PoiseError("Saved result integration state is invalid") from exc

    def _load(self, task_id):
        record, run, legacy_version = self._read_state(task_id)
        if legacy_version is not None:
            self._save(task_id, run, legacy_version)
        return record, run

    def _destination_refusal(self, reason, facts):
        diagnostic = {
            **facts, "reason": reason,
            "correction": (
                "Владелец конфигурации должен исправить привязку репозитория; "
                "оператор должен восстановить объявленную целевую ветку в нужном "
                "checkout, сохранив рабочие изменения. Автоматического переключения "
                "веток и синхронизации в существующую Task другого хранилища нет."
            ),
        }
        raise PoiseError(
            "Назначение интеграции не соответствует договору: "
            + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
        )

    def _observe_destination(self, root, facts, prefix):
        environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        try:
            resolved = Path(root).resolve(strict=True)
            facts[prefix + "_root"] = self._git(
                resolved, "rev-parse", "--show-toplevel", env=environment,
            )
            facts[prefix + "_common_directory"] = self._git(
                resolved, "rev-parse", "--path-format=absolute", "--git-common-dir",
                env=environment,
            )
            branch = self._run(resolved, "symbolic-ref", "--quiet", "HEAD", env=environment)
            if branch["actual_exit_code"] not in (0, 1):
                raise PoiseError("Git could not observe the checked-out branch")
            facts[prefix + "_branch"] = (
                branch["stdout"].strip() if branch["actual_exit_code"] == 0 else "detached"
            )
            return resolved
        except (OSError, TypeError, ValueError, PoiseError) as exc:
            facts["observation_error"] = str(exc)
            self._destination_refusal(prefix + "_unavailable", facts)

    def _validate_destination(self, record):
        facts = {
            "configured_repository": self.h.cfg["git"]["repository"],
            "configured_target_ref": self._target_ref(),
            "registered_task_worktree": record["worktree"],
            "registered_task_branch": record["branch"],
        }
        repository = self._observe_destination(facts["configured_repository"], facts, "observed")
        source = self._observe_destination(facts["registered_task_worktree"], facts, "source")
        if Path(facts["observed_root"]).resolve() != repository:
            self._destination_refusal("publication_checkout_root_mismatch", facts)
        if Path(facts["source_root"]).resolve() != source or source == repository:
            self._destination_refusal("task_checkout_root_mismatch", facts)
        if Path(facts["observed_common_directory"]).resolve() \
                != Path(facts["source_common_directory"]).resolve():
            self._destination_refusal("repository_mismatch", facts)
        if facts["observed_branch"] != facts["configured_target_ref"]:
            self._destination_refusal("target_not_checked_out", facts)
        source_ref = "refs/heads/" + self._short_branch(record["branch"])
        if facts["source_branch"] != source_ref:
            self._destination_refusal("task_branch_mismatch", facts)
        environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        try:
            self._git(repository, "rev-parse", "--verify", self._target_ref(), env=environment)
            listed = self._git(repository, "worktree", "list", "--porcelain", "-z", env=environment)
        except PoiseError as exc:
            facts["observation_error"] = str(exc)
            self._destination_refusal("destination_registration_unavailable", facts)
        entries, entry = [], {}
        for field in listed.split("\0"):
            if not field:
                if entry:
                    entries.append(entry)
                    entry = {}
                continue
            key, _, value = field.partition(" ")
            entry[key] = value
        if entry:
            entries.append(entry)
        if not any(
            Path(item["worktree"]).resolve() == source and item.get("branch") == source_ref
            for item in entries if "worktree" in item
        ):
            self._destination_refusal("task_worktree_not_registered", facts)
        return repository

    def _admit(self, intent):
        record, run, legacy_version = self._read_state(intent.task_id)
        if run is not None and not self._same_intent(run, intent) \
                and not self._completed_replay(run, intent):
            raise PoiseError("Task integration intent is immutable")
        if run is None or run.requires_destination_admission:
            repository = self._validate_destination(record)
        else:
            repository = Path(self.h.cfg["git"]["repository"]).resolve(strict=True)
        if legacy_version is not None:
            self._save(intent.task_id, run, legacy_version)
        return record, run, repository

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
        record, run, repository = self._admit(intent)
        if run is None:
            self._validate_new(record, intent, repository)
            return record
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
        selected = []
        for method in record["contract"]["methods"]:
            plan = method["verification_plan"]
            if plan["green_stages"] and plan["change_surface"]:
                selected.append(method)
        return selected

    def _complete_candidate_proof(self, record, run):
        methods = self._select_checks(record)
        if not methods:
            return True
        latest_batch = next(
            (entry.get("details") for entry in reversed(run.history)
             if entry.get("event") == "checks_passed"), None,
        )
        if not isinstance(latest_batch, dict):
            return False
        batch_ids = latest_batch.get("check_ids")
        if (not isinstance(batch_ids, list) or len(batch_ids) != len(methods)
                or any(not isinstance(check_id, str) for check_id in batch_ids)
                or len(batch_ids) != len(set(batch_ids))):
            return False
        worktree, head = self._checked_workspace(run)
        tree = self._git(worktree, "rev-parse", "HEAD^{tree}")
        if len(run.checks) < len(methods):
            return False
        batch = run.checks[-len(methods):]
        if [item.get("id") if isinstance(item, dict) else None for item in batch] != batch_ids:
            return False
        earlier_ids = {
            item.get("id") for item in run.checks[:-len(methods)]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if earlier_ids.intersection(batch_ids):
            return False
        contract_digest = self._contract_digest(record)
        owner_root = task_root(
            self.h.state, self.h.paths, run.intent.task_id, record["sprint_id"],
        )
        for method, receipt in zip(methods, batch, strict=True):
            if not isinstance(receipt, dict):
                return False
            check_id = receipt.get("id")
            try:
                uuid.UUID(check_id)
            except (TypeError, ValueError, AttributeError):
                return False
            run_dir = descendant(owner_root, self.h.paths["runs"]) / check_id
            stdout = descendant(run_dir, self.h.paths["stdout"])
            stderr = descendant(run_dir, self.h.paths["stderr"])
            expected = {
                "method": method["id"],
                "integration_head": head,
                "verified_tree": tree,
                "argv": method["argv"],
                "cwd": str((worktree / method["cwd"]).resolve()),
                "expected_exit_code": method["expected_exit_code"],
                "definition_digest": digest(method),
                "contract_digest": contract_digest,
                "passed": True,
                "timed_out": False,
                "cancelled": False,
                "capture_complete": True,
                "actual_exit_code": method["expected_exit_code"],
                "stdout": str(stdout),
                "stderr": str(stderr),
            }
            if any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
                   for key, value in expected.items()):
                return False
            try:
                if (not stdout.is_file() or not stderr.is_file()
                        or stdout.is_symlink() or stderr.is_symlink()
                        or receipt.get("stdout_digest") != file_digest(stdout)
                        or receipt.get("stderr_digest") != file_digest(stderr)
                        or not method_passed(method, receipt)):
                    return False
                if inspect_declared_output_receipts(method.get('outputs', []), receipt.get('outputs')) != (True, True):
                    return False
                if any(output['status'] == 'captured'
                       and output['path'] != str(run_dir / 'outputs' / output['id'])
                       for output in receipt['outputs']):
                    return False
            except (OSError, KeyError, ValueError):
                return False
        return True

    def _checked_workspace(self, run, *, exact_head=True):
        """Observe only: a bad retry must not advance the durable state machine."""
        worktree = Path(run.task_worktree).resolve(strict=True)
        branch = self._git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch != self._short_branch(run.task_branch):
            raise PoiseError("Integration worktree is not on its recorded task branch")
        if self._git(worktree, "status", "--porcelain"):
            raise PoiseError("Integration task worktree must be clean; preserve and commit authorized WIP before retry")
        if any(value is not None for value in self._operation_refs(worktree).values()):
            raise PoiseError("Integration task worktree has an unfinished Git operation")
        head = self._git(worktree, "rev-parse", "HEAD")
        if exact_head and head != run.integration_head:
            raise PoiseError("Integration candidate changed after its recorded preflight")
        for ancestor in (run.accepted_commit, run.last_included_target, run.integration_head):
            if ancestor is None or not self._contains(worktree, ancestor, head):
                raise PoiseError("Integration candidate lost accepted/target/previous candidate ancestry")
        return worktree, head

    def _retry_checks(self, record, run, repository):
        source = self._validate_source(record, run.intent, repository, run)
        if (source.resolve() != Path(run.task_worktree).resolve()
                or self._short_branch(record["branch"]) != self._short_branch(run.task_branch)):
            raise PoiseError("Integration task binding changed")
        worktree, head = self._checked_workspace(run, exact_head=False)
        common = self._git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
        main_common = self._git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common != main_common:
            raise PoiseError("Integration task worktree belongs to another repository")
        retried = run.retry_checks(head)
        self._save(run.intent.task_id, retried, run.version)
        return retried

    def _run_checks(self, record, run):
        worktree, head = self._checked_workspace(run)
        verified_tree = self._git(worktree, "rev-parse", "HEAD^{tree}")
        receipts = []
        owner_root = task_root(
            self.h.state,
            self.h.paths,
            run.intent.task_id,
            record["sprint_id"],
        )
        for method in self._select_checks(record):
            self._checked_workspace(run)
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
            declared_output_dir = run_dir / "declared-outputs"
            declared_output_dir.mkdir(parents=True, exist_ok=True)
            environment["POISE_RUN_OUTPUT_DIR"] = str(declared_output_dir)
            result = run_command(
                method["argv"], cwd, environment, None,
                descendant(run_dir, self.h.paths["stdout"]),
                descendant(run_dir, self.h.paths["stderr"]),
            )
            self._checked_workspace(run)
            outputs, outputs_complete = capture_declared_outputs(
                method.get("outputs", []), declared_output_dir, run_dir / "outputs",
            )
            receipts.append({
                **result, "id": check_id, "method": method["id"],
                "definition_digest": digest(method),
                "contract_digest": self._contract_digest(record),
                "integration_head": head, "verified_tree": verified_tree,
                "argv": method["argv"], "cwd": str(cwd),
                "expected_exit_code": method["expected_exit_code"],
                "passed": method_passed(method, result) and outputs_complete,
                "outputs": outputs,
                "stdout_digest": file_digest(Path(result["stdout"])),
                "stderr_digest": file_digest(Path(result["stderr"])),
                "preview": preview(Path(result["stderr"]),
                                   self.h.cfg["limits"]["preview_chars"]),
            })
        checked = run.checks_recorded(receipts)
        self._save(run.intent.task_id, checked, run.version)
        return checked

    @staticmethod
    def _contract_digest(record):
        return digest({"contract": record["contract"], "task_version": record["version"]})

    def _has_current_checks(self, record, run):
        return self._complete_candidate_proof(record, run)

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
            try:
                self._checked_workspace(run)
            except PoiseError as exc:
                return self._publication_failed(run, "task_worktree_changed", {"error": str(exc)})
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

    @staticmethod
    def _regular_config_source(worktree, relative):
        source = worktree / relative
        current = worktree
        for part in Path(relative).parts[:-1]:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                return None
            if not stat.S_ISDIR(mode):
                raise PoiseError("Declared configuration has an unsafe parent")
        try:
            mode = source.lstat().st_mode
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(mode) or not stat.S_IMODE(mode) & 0o444:
            raise PoiseError("Declared configuration is not a regular file")
        return source

    @staticmethod
    def _private_directory(path):
        if path.exists() or path.is_symlink():
            mode = path.lstat().st_mode
            if not stat.S_ISDIR(mode) or stat.S_IMODE(mode) & 0o077:
                raise PoiseError("Configuration recovery parent is unsafe")
        else:
            path.mkdir(mode=0o700)

    def _backup_configuration(self, run):
        def source_identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mode,
                    info.st_mtime_ns, info.st_ctime_ns)

        paths = run.intent.config_backup_paths
        if not paths or run.cleanup["task_worktree"] == "removed":
            return run, True
        record = self.h.task_queries.record(run.intent.task_id)
        recovery = task_root(
            self.h.state, self.h.paths, run.intent.task_id, record["sprint_id"]
        ) / "configuration-recovery" / self._identity(run.intent)
        worktree = Path(run.task_worktree)
        prior = next((item["details"]["files"] for item in run.history
                      if item.get("event") == "configuration_backup_completed"), None)
        files = []
        failure_reason = "configuration_backup_failed"
        try:
            self._private_directory(recovery.parent)
            self._private_directory(recovery)
            for relative in paths:
                source = self._regular_config_source(worktree, relative)
                if source is None:
                    files.append({"path": relative, "status": "absent"})
                    continue
                fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        failure_reason = "configuration_source_changed"
                        raise PoiseError("Declared configuration changed during backup")
                    content = stream.read()
                    if source_identity(info) != source_identity(os.fstat(stream.fileno())):
                        failure_reason = "configuration_source_changed"
                        raise PoiseError("Declared configuration changed during backup")
                destination = recovery
                for part in Path(relative).parts[:-1]:
                    destination = destination / part
                    self._private_directory(destination)
                destination = destination / Path(relative).name
                digest = hashlib.sha256(content).hexdigest()
                if destination.exists() or destination.is_symlink():
                    mode = destination.lstat().st_mode
                    if (not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077
                            or file_digest(destination) != digest):
                        raise PoiseError("Existing configuration backup conflicts with source")
                else:
                    fd = os.open(
                        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                    )
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                failure_reason = "configuration_source_changed"
                if source_identity(info) != source_identity(source.lstat()):
                    raise PoiseError("Declared configuration changed during backup")
                failure_reason = "configuration_backup_failed"
                files.append({
                    "path": relative, "status": "copied", "sha256": digest,
                    "source_mode": stat.S_IMODE(info.st_mode),
                    "recovery_path": str(destination),
                })
        except (OSError, PoiseError):
            blocked = run.cleanup_blocked(
                "task_worktree", {"reason": failure_reason}
            )
            self._save(run.intent.task_id, blocked, run.version)
            return blocked, False
        if prior is not None:
            if files != prior:
                blocked = run.cleanup_blocked(
                    "task_worktree", {"reason": "configuration_backup_changed"}
                )
                self._save(run.intent.task_id, blocked, run.version)
                return blocked, False
            return run, True
        completed = run.configuration_backup_completed(files)
        self._save(run.intent.task_id, completed, run.version)
        return completed, True

    def _cleanup(self, run, repository):
        if run.phase != "cleanup_pending":
            return run
        current_target = self._git(repository, "rev-parse", self._target_ref())
        if not self._contains(repository, run.target_after, current_target):
            raise PoiseError("Current target does not contain the published integration head")
        if not self._contains(repository, run.accepted_commit, current_target):
            raise PoiseError("Current target does not contain the accepted commit")
        run, backed_up = self._backup_configuration(run)
        if not backed_up:
            return run
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

    @staticmethod
    def _require_absent_cleanup_path(path, root, label):
        """Check lexical parents too: resolving a dangling link hides substitution."""
        path, root = Path(path), Path(root)
        if not path.is_relative_to(root) or path == root:
            raise PoiseError(f'{label} cleanup path is outside its configured root')
        current = path
        while True:
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                mode = None
            except OSError as exc:
                raise PoiseError(f'{label} cleanup path could not be verified: {current}') from exc
            if mode is not None:
                if stat.S_ISLNK(mode):
                    raise PoiseError(f'{label} cleanup path has a symlink: {current}')
                if current == path:
                    raise PoiseError(f'{label} resource was recreated after cleanup: {path}')
                if not stat.S_ISDIR(mode):
                    raise PoiseError(f'{label} cleanup parent is not a directory: {current}')
            if current == root:
                if mode is None:
                    raise PoiseError(f'{label} cleanup root could not be verified: {root}')
                return
            current = current.parent

    def _verify_terminal_cleanup(self, run, repository, execution):
        publication = run.publication
        if (run.status != 'integrated' or run.phase != 'integrated'
                or run.cleanup != {'task_worktree': 'removed', 'task_branch': 'deleted',
                                   'temporary_backups': 'removed'}):
            raise PoiseError('Cleanup is not proven complete for ownership reconciliation')
        if (not isinstance(publication, dict) or publication.get('status') != 'confirmed'
                or not run.integration_head or run.integration_head != run.target_after
                or publication.get('commit') != run.target_after
                or publication.get('target_ref') != self._target_ref()
                or publication.get('last_included_target') != run.last_included_target):
            raise PoiseError('Publication proof does not match the completed integration')
        report = execution['last_report']
        if (not isinstance(report, dict)
                or report.get('commit') != run.accepted_commit
                or run.accepted_commit != run.intent.expected_source_commit):
            raise PoiseError('Terminal ownership proof does not match the accepted result')
        if (execution['worktree'] != run.task_worktree
                or execution['branch'] != run.task_branch
                or not run.task_branch
                or self._short_branch(run.task_branch) == self._short_branch(self._target_ref())):
            raise PoiseError('Cleanup worktree/branch identity changed')
        expected_tree = self.h.state / self.h.paths['worktrees'] / run.intent.task_id
        expected_backup = self._temporary_backup_directory(run.intent.task_id, run.intent.request_id)
        if (Path(run.task_worktree) != expected_tree
                or run.temporary_backup_directory != expected_backup):
            raise PoiseError('Cleanup paths do not match the exact recorded Task scope')
        current = self._git(repository, 'rev-parse', self._target_ref())
        if (not self._contains(repository, run.target_after, current)
                or not self._contains(repository, run.accepted_commit, current)):
            raise PoiseError('Publication is not contained in the current target')
        self._require_absent_cleanup_path(run.task_worktree, self.h.state, 'Worktree')
        self._require_absent_cleanup_path(run.temporary_backup_directory, self.h.state, 'Backup')
        # Do not infer ref absence from an arbitrary failed Git command.
        branch_ref = 'refs/heads/' + self._short_branch(run.task_branch)
        branch = self._run(repository, 'show-ref', '--verify', '--quiet', branch_ref)
        if branch['actual_exit_code'] == 0:
            raise PoiseError('Task branch was recreated after cleanup')
        if branch['actual_exit_code'] != 1:
            raise PoiseError('Task branch cleanup could not be verified')

    def _reconcile_terminal_ownership(self, run, repository):
        from ..application.ownership import reconcile_integrated_worktree_in

        with exclusive_lock(
            self._publication_lock(), self.h.cfg['limits']['lock_seconds'],
            self.h.cfg['limits']['lock_poll_seconds'],
        ):
            with self.h.store.unit_of_work() as uow:
                reconcile_integrated_worktree_in(
                    uow, self.h.session, run.intent.task_id, run.to_storage(),
                    lambda execution: self._verify_terminal_cleanup(run, repository, execution),
                )

    def apply(self, intent):
        record, run, repository = self._admit(intent)
        if run is None:
            self._validate_new(record, intent, repository)
            run = self._new_run(record, intent)
            self._save(intent.task_id, run, -1)
        if run.status == "integrated":
            self._reconcile_terminal_ownership(run, repository)
            return run.result(replayed=True)
        if run.phase == "checks_failed":
            run = self._retry_checks(record, run, repository)
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
                run = run.begin_update(observed)
                self._save(intent.task_id, run, previous)
            if run.phase == "updating":
                run = self._prepare_candidate(run)
                if run.phase in ("awaiting_resolution", "candidate_failed"):
                    return run.result()
            if run.phase == "candidate_ready":
                record = self.h.task_queries.record(intent.task_id)
                run = self._run_checks(record, run)
                if run.phase == "checks_failed":
                    return run.result()
            if run.phase in ("publishing", "publication_failed"):
                record = self.h.task_queries.record(intent.task_id)
                if not self._complete_candidate_proof(record, run):
                    rechecking = run.recheck_publication()
                    self._save(intent.task_id, rechecking, run.version)
                    run = rechecking
                    continue
                if run.phase == "publication_failed":
                    retried = run.retry_publication()
                    self._save(intent.task_id, retried, run.version)
                    run = retried
            if run.phase == "publishing":
                try:
                    current_checks = self._has_current_checks(record, run)
                except PoiseError as exc:
                    run = self._publication_failed(
                        run, "task_worktree_changed", {"error": str(exc)},
                    )
                    return run.result()
                if not current_checks:
                    renewed = run.require_current_checks()
                    self._save(intent.task_id, renewed, run.version)
                    run = renewed
                    continue
                run = self._publish(run, repository)
                if run.phase == "updating":
                    continue
                if run.phase == "publication_failed":
                    return run.result()
            if run.phase == "cleanup_pending":
                run = self._cleanup(run, repository)
            if run.status == "integrated":
                self._reconcile_terminal_ownership(run, repository)
            return run.result()

    def query(self, task_id, request_id):
        if (not isinstance(task_id, str) or not task_id
                or not isinstance(request_id, str) or not request_id):
            raise PoiseError("Integration query requires task_id and request_id")
        _, run = self._load(task_id)
        if run is None or run.intent.request_id != request_id:
            raise PoiseError("Result integration request was not found")
        return run.result()
