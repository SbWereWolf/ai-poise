"""PreToolUse-specific native callback orchestration."""
from ..modules.hook_transport.pre_tool_use_proxy import (
    InitialPreToolUseProjection, InitialPreToolUseResult,
)
from .initial_hook_proxy import run_callbacks


class InitialPreToolUseProxy:
    def __init__(self, executor):
        self.executor = executor

    def run(self, snapshot, raw, event):
        return run_callbacks(self.executor, snapshot, raw, event,
            InitialPreToolUseProjection.selected(snapshot['groups'], event.get('tool_name', '')),
            InitialPreToolUseResult())
