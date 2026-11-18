"""PostToolUse native selection and synchronous contribution policy."""
from .initial_proxy import InitialHookProjection
from .session_start_proxy import InitialSessionStartResult


class InitialPostToolUseProjection(InitialHookProjection):
    event_name = 'PostToolUse'
    allow_async = True
    wildcard_all = True


class InitialPostToolUseResult(InitialSessionStartResult):
    event_name = 'PostToolUse'
