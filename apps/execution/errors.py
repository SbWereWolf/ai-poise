"""Public Execution errors shared without importing the shell composition root."""


class ExecutionError(Exception):
    pass


class StageCommitError(ExecutionError):
    """Observed facts after staging began; no automatic mutation retry or rollback."""

    def __init__(self, *, phase, effect, before_head, after_head, candidate_tree, detail):
        self.phase = phase
        self.effect = effect
        self.before_head = before_head
        self.after_head = after_head
        self.candidate_tree = candidate_tree
        self.detail = detail
        super().__init__(f'Git {phase}: {detail}; HEAD effect={effect}, '
                         f'before={before_head}, after={after_head}. '
                         'Inspect the actual Git state before continuing; preserve WIP.')
