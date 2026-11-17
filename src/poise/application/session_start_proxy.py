"""SessionStart-specific native callback orchestration."""
from ..modules.hook_transport.session_start_proxy import (
    InitialSessionStartProjection, InitialSessionStartResult,
)
from .initial_hook_proxy import run_callbacks


class InitialSessionStartProxy:
    def __init__(self, executor):
        self.executor = executor

    def run(self, snapshot, raw, event):
        return run_callbacks(self.executor, snapshot, raw, event,
            InitialSessionStartProjection.selected(snapshot['groups'], event.get('source', '')),
            InitialSessionStartResult())
