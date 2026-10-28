"""Test-owned native wire configuration and non-replacing call observation.

Only isolated fixture identities are used. These inputs preserve the string
shape observed in real PostToolUse10973; they are not new live hook evidence.
"""
from copy import deepcopy
import sys

from tests.runtime_services.tool_results_support import literal


def wire_case(name):
    return literal('native_wire/cases.json')[name]


def select_text(scene):
    scene.policy['parsers']['Bash'].update(
        literal('native_wire/profiles.json')['bash_changes'])
    # One existing explicit basename, usable for text and structured sources.
    scene.policy['session']['full'] = 'full'
    scene.save_policy()


def select_work_text(scene, source_root):
    select_text(scene)
    scene.policy['parsers']['Poise'].update(
        format='saved-work-text', source_root=str(source_root))
    scene.policy['selection'] = literal('native_wire/profiles.json')['selection']
    scene.save_policy()


class NativeCalls:
    """Observe the real public classifier call without replacing any behavior."""
    def __init__(self):
        from poise.modules.result_views.tool_results import native_snapshot
        self.code = native_snapshot.__code__
        self.events = []

    def observe(self, frame, event, value):
        if self.previous is not None:
            self.previous(frame, event, value)
        if frame.f_code is self.code and event == 'call':
            self.events.append(deepcopy(frame.f_locals['event']))

    def __enter__(self):
        self.previous = sys.getprofile()
        sys.setprofile(self.observe)
        return self

    def __exit__(self, *_):
        sys.setprofile(self.previous)
