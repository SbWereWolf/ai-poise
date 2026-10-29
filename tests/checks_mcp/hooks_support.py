"""Actual install/event/native-entry clients, never a check handler replica."""
from copy import deepcopy
import json
import os
import subprocess
import sys

from poise.common import PoiseError
from tests.hook_transport.architecture_support import Chain, literal as architecture_literal
from tests.hook_transport.helpers import event

from .support import CheckHookUnavailable, FIXTURES, literal


def hooks(case, *, after_action='continue'):
    chain = Chain(case.scene.project, case.root / 'native-hooks', 'F13_CHECK_INVOCATION',
                  general=case.scene.general)
    chain.session = 'f13-checks-isolated-native-session'
    chain.install([])  # Healthy current public installation control.
    base = architecture_literal('handler-configuration.json')['handlers'][0]
    base.update(id='checks', mode='sync', implementation={'kind': 'builtin', 'name': 'check-invocation'})
    options = literal('hook-options.json')
    options['policy_path'] = str(case.policy_path)
    options['checks_policy_path'] = str(case.scene.policy_path)
    options['cli_argv'][0] = sys.executable
    base['options'] = options
    after = chain.command('after')
    if after_action == 'deny':
        after['implementation']['argv'] = [sys.executable, '-B', str(FIXTURES / 'deny_handler.py')]
    else:
        assert after_action == 'continue'
    path = chain.configuration('checks-chain', [chain.command('before'), base, after])
    definition = deepcopy(chain.definition)
    definition['events'] = [row['descriptor'] for row in architecture_literal('native-events.json')
                            if row['descriptor']['event'] in ('SessionStart', 'SessionEnd', 'PreToolUse', 'PostToolUse')]
    for item in definition['events']:
        if item['event'] in ('PreToolUse', 'PostToolUse'):
            item['matcher'] = 'Bash|mcp__repo_checks__run|mcp__repo_checks__cancel'
    definition['routes'] = {row['event']: [] for row in definition['events']}
    definition['routes']['PreToolUse'] = [path]
    definition['routes']['PostToolUse'] = [path]
    try:
        installed = chain.service.install({'request_id': 'f13-install-checks',
                                           'expected_revision': chain.service.revision(), 'definition': definition})
    except PoiseError as error:
        if str(error) == 'Unregistered builtin handler; select a registered implementation':
            raise CheckHookUnavailable('F13_CHECK_HOOK_MISSING: actual public install rejected new builtin') from error
        raise
    assert installed['status'] == 'installed'
    chain.installation, chain.definition = installed, definition
    chain.invoke()
    case.cleanup_callbacks.append(chain.finish)
    return chain


def native(case, chain, name, tool, arguments, *, key='f13-actual-tool', response=None,
           python=None, isolated=False):
    packet = event(name, session=chain.session)
    packet.update(cwd=str(case.scene.checkout), tool_name=tool, tool_use_id=key, tool_input=deepcopy(arguments))
    if name == 'PostToolUse':
        packet['tool_response'] = deepcopy(response)
    folder = case.root / ('hook-' + str(len(list(case.root.glob('hook-*')))))
    folder.mkdir()
    environment = {k: v for k, v in chain.general.env.items()
                   if k not in ('POISE_CALLER_BINDING', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID')}
    environment.update(CODEX_THREAD_ID=chain.session, CODEX_SESSION_ID=chain.session)
    if isolated:
        environment.pop('PYTHONPATH', None)
    argv = [python or sys.executable, *(['-I'] if isolated else []), '-B', '-m', 'poise', 'hook', '--settings', str(chain.service.settings.path),
            '--definition', chain.installation['definition_path']]
    actual = subprocess.run(argv, input=json.dumps(packet).encode(), cwd=case.scene.checkout,
                            env=environment, capture_output=True, timeout=20)
    (folder / 'request.json').write_text(json.dumps(packet, ensure_ascii=False))
    (folder / 'stdout.json').write_bytes(actual.stdout)
    (folder / 'stderr.log').write_bytes(actual.stderr)
    assert actual.returncode == 0, (actual.returncode, actual.stdout, actual.stderr)
    return json.loads(actual.stdout), environment


def native_peer(case, chain):
    environment = {k: v for k, v in case.environment.items() if k != 'POISE_CALLER_BINDING'}
    environment.update(CODEX_THREAD_ID=chain.session, CODEX_SESSION_ID=chain.session)
    peer = case.peer(environment=environment)
    peer.initialize()
    return peer
