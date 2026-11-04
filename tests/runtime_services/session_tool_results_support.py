"""Transparent clients/observers for the reviewed session-output contract.

No parser, source classifier, storage publication or permission is implemented
here. Synthetic identities belong only to isolated fixture callers.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import UUID

from tests.runtime_services.tool_results_support import (
    Scenario, literal, write_json,
)


class OutputCapabilityMissing(AssertionError):
    """An exact absent public producer capability, never an import error."""


def prepare_owner():
    from poise.modules.result_views import tool_results
    function = getattr(tool_results, 'prepare_views', None)
    if not callable(function):
        raise OutputCapabilityMissing('F08_PREPARE_VIEWS_MISSING')
    return function


def cases():
    return literal('session-parser-cases.json')


class ParserCalls:
    """Observe actual Python calls without replacing their arguments/result."""
    def __init__(self, function):
        self.code = function.__code__
        self.inputs, self.results = [], []

    def observe(self, frame, event, value):
        if frame.f_code is self.code:
            if event == 'call':
                self.inputs.append(deepcopy(frame.f_locals['record']['response']))
            elif event == 'return':
                self.results.append(deepcopy(value))

    def __enter__(self):
        self.previous = sys.getprofile()
        sys.setprofile(self.observe)
        return self

    def __exit__(self, *_):
        sys.setprofile(self.previous)


class SessionScenario(Scenario):
    def __init__(self, project, parent, *, caller='session-output-a'):
        super().__init__(project, parent)
        self.policy.update(self.fill(literal('session-policy.additions.json')))
        # Existing public direct-caller identity; no host identity is inherited.
        write_json(Path(self.env['POISE_CALLER_BINDING']),
                   {'schema': 'poise-caller-binding-1', 'caller_id': caller})
        self.save_policy()

    def input(self, case='bash-success', *, key='f08-tool-a'):
        data = cases()[case]
        return self.request(request_id=key, tool=data['tool'],
                            response=data['response'], exit_code=data['exit_code'],
                            streams={'stdout': None, 'stderr': None})

    def direct(self, request, audience='agent'):
        code, text, err = self.invoke(request, audience)
        assert not err, ('F08_PUBLIC_RESPONSE_STDERR', code, err)
        return code, json.loads(text)

    def stored(self, request=None):
        request = self.input() if request is None else request
        value, saved = self.capture(request, audience='machine')
        return value, saved

    def run_identity(self, value):
        observed = value.get('run_id')
        assert isinstance(observed, str), 'F08_SESSION_RUN_UUID_MISSING'
        assert str(UUID(observed)) == observed, 'F08_SESSION_RUN_UUID_INVALID'
        if 'full_path' not in value:
            # Retained internal layout is a storage assertion, not agent output.
            value = self.owner().store.latest()
            assert value['run_id'] == observed
        path = Path(value['full_path'])
        assert observed in path.parent.name, 'F08_RUN_DIRECTORY_HAS_NO_UUID'
        assert path.is_absolute() and path.is_relative_to(self.root)
        assert path.is_file(), 'F08_FULL_FILE_MISSING'
        expanded, last = Path(value['expanded_path']), Path(value['last_path'])
        assert expanded.is_file() and expanded.parent == path.parent
        assert last.is_file() and last.is_relative_to(self.root)
        assert json.loads(last.read_bytes()) == {'uuid': observed}
        return observed, path, expanded, last

    def service(self, operation):
        request = {'schema': 'poise-tool-result-request-1', 'operation': operation}
        code, value = self.direct(request)
        assert code == 0 and value.get('status') == operation, (
            'F08_LAST_SERVICE_MISSING', operation, code, value)
        return value

    def native(self, response, *, key='native-tool-a', tool='Bash', present=True):
        value = self.request('hook.template.json')
        event = value['event']
        event.update(session_id='isolated-output-session', turn_id='isolated-output-turn',
                     tool_use_id=key, tool_name=tool, cwd=str(self.checkout))
        if present:
            event['tool_response'] = deepcopy(response)
        else:
            event.pop('tool_response')
        value.update(request_id=key, streams={'stdout': None, 'stderr': None},
                     capture={'state': 'unknown', 'reason': 'native source not yet classified'},
                     exit_code=response.get('exit_code') if isinstance(response, dict) else None)
        return value


def actual_sources(saved):
    from poise.infrastructure.tool_results import FileStoredSource
    return {name: None if item is None else FileStoredSource(item)
            for name, item in {'response': saved['_response_source'],
                               **saved['streams']}.items()}


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}
