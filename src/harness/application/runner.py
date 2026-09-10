"""Shared stage application service. No SQL, filesystem or goal-type switches."""
from .tasks import TaskCommands


class StageRunner:
    def __init__(self, tasks: TaskCommands):
        self.tasks = tasks

    def context(self, task_id):
        return self.tasks.workflow_context(task_id)

    def submit(self, task_id, actor, payload):
        return self.tasks.submit(task_id, actor, payload)

    def verified(self, task_id, actor, digest, report, artifacts):
        return self.tasks.mark_verified(task_id, actor, digest, report, artifacts)

    def accept(self, task_id, actor, advance, entry_tree):
        return self.tasks.accept(task_id, actor, advance, entry_tree)

    def rework(self, task_id, actor, feedback, entry_tree, target):
        return self.tasks.rework(task_id, actor, feedback, entry_tree, target)

    def record_observations(self, task_id, actor, tree, execution_key, receipts):
        return self.tasks.record_observations(task_id,actor,tree,execution_key,receipts)

    def assess_evidence(self, task_id, actor, tree, execution_key):
        return self.tasks.assess_evidence(task_id,actor,tree,execution_key)
