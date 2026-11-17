"""Execute distinct original callbacks with their event-owned result policy."""
from ..modules.foundation.errors import DomainError


def run_callbacks(executor, snapshot, raw, event, selected, result):
    executor.begin(snapshot, raw, event)
    for position, handler in selected:
        outcome = executor.execute(position, handler)
        try:
            result.add(outcome)
        except DomainError as exc:
            result.failed = True
            result.diagnostics.append((str(exc) + '\n').encode())
    return result.complete()
