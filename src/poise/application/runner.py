"""Shared stage application service. No SQL, filesystem or goal-type switches."""
from .tasks import TaskCommands


class StageRunner:
    def __init__(self, tasks: TaskCommands):
        self.tasks = tasks

    def context(self, task_id):
        return self.tasks.workflow_context(task_id)

    def submit(self, task_id, actor, payload):
        return self.tasks.submit(task_id, actor, payload)

    def matching_submission_digest(self, task_id, actor, payload):
        return self.tasks.matching_submission_digest(task_id, actor, payload)

    def verified(self, task_id, actor, digest, report, artifacts, *, packet_digest, permanent_artifacts):
        return self.tasks.mark_verified(task_id, actor, digest, report, artifacts,
                                        packet_digest=packet_digest, permanent_artifacts=permanent_artifacts)

    def accept(self, task_id, actor, advance, entry_tree, **admission):
        return self.tasks.accept(task_id, actor, advance, entry_tree, **admission)

    def rework(self, task_id, actor, feedback, entry_tree, target, **admission):
        return self.tasks.rework(task_id, actor, feedback, entry_tree, target, **admission)

    def rework_failed(self, task_id, actor, feedback, entry_tree, execution_key, target, **admission):
        return self.tasks.rework_failed(task_id,actor,feedback,entry_tree,execution_key,target, **admission)

    def record_observations(self, task_id, actor, tree, execution_key, receipts):
        return self.tasks.record_observations(task_id,actor,tree,execution_key,receipts)

    def recover_pending_checks(
        self, task_id, actor, submission_digest, tree, execution_key, receipts
    ):
        return self.tasks.recover_pending_checks(
            task_id, actor, submission_digest, tree, execution_key, receipts
        )

    def assess_evidence(self, task_id, actor, tree, execution_key):
        return self.tasks.assess_evidence(task_id,actor,tree,execution_key)

    def begin_check_attempt(self, task_id, actor, tree, execution_key, methods, limit, expected_version, submission_digest, identity):
        return self.tasks.begin_check_attempt(task_id, actor, tree, execution_key, methods, limit, expected_version, submission_digest, identity)

    def current_check_attempt(self, task_id, actor, tree, execution_key, methods):
        return self.tasks.current_check_attempt(task_id, actor, tree, execution_key, methods)

    def record_check_termination(self, task_id, actor, expected, run_id, outcome):
        return self.tasks.record_check_termination(task_id, actor, expected, run_id, outcome)

    def start_check_run(self, task_id, actor, expected, run_id):
        return self.tasks.start_check_run(task_id, actor, expected, run_id)
