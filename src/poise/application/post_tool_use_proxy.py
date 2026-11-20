"""Coordinate admitted original PostToolUse callbacks without transport I/O."""
from ..modules.hook_transport.post_tool_use_proxy import (
    InitialPostToolUseProjection, InitialPostToolUseResult,
)
from .initial_hook_proxy import run_callbacks


class InitialPostToolUseProxy:
    def __init__(self, executor):
        self.executor = executor

    def run(self, snapshot, raw, event):
        return run_callbacks(self.executor, snapshot, raw, event,
            InitialPostToolUseProjection.selected(snapshot['groups'], event.get('tool_name', '')),
            InitialPostToolUseResult())
