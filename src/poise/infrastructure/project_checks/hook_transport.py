"""Configured native check adoption within the existing ordered dispatcher."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shlex

from ...common import PoiseError
from ...composition import project_checks_tools, check_invocation_tools
from ...interfaces.project_checks import prepare_request
from ...modules.project_checks.contracts import CheckFailure
from ...modules.project_checks.mcp import CheckTransportPolicy, arguments, check_request
from ...modules.session_establishment.domain import CallerIdentity
from ..goal_config import strict_json
from ..project_binding import read_project_checkout
from ..session_establishment import establish_project_session


def shell_words(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars='|&;<>')
    lexer.whitespace_split, lexer.commenters = True, ''
    return list(lexer)


def configured_cli(command, prefix):
    tokens = shell_words(command)
    if tokens[:len(prefix)] != prefix or tokens.count('<') != 1:
        return None
    redirect = tokens.index('<')
    if redirect != len(tokens) - 2:
        return None
    names, tail = {}, tokens[len(prefix):redirect]
    if len(tail) % 2:
        return None
    for i in range(0, len(tail), 2):
        if tail[i] not in ('--config', '--checkout', '--policy', '--view', '--transport-policy', '--invocation') or tail[i] in names:
            return None
        names[tail[i]] = tail[i + 1]
    if not {'--config', '--checkout', '--policy', '--view', '--transport-policy'} <= names.keys():
        return None
    return tokens, redirect, names


def registered_match(tokens, service):
    weak = []
    for profile in service.configuration.profiles.profiles:
        invocation = service.runtime.resolve(profile, environment=service.execution_environment())
        argv = invocation['argv']
        for start in range(len(tokens)):
            if tokens[start:start + len(argv)] == argv:
                # A real command begins a pipeline/list or follows a known wrapper.
                before = tokens[:start]
                if not before or before[-1] in ('|', ';', '&&', '||') or before[0] in ('env', 'timeout'):
                    return profile['command_id'], True
        script = [word for word in argv[1:] if '/' in word or word.endswith(('.py', '.sh', '.php'))]
        if any(word in tokens for word in script):
            weak.append(profile['command_id'])
    return (weak[0], False) if weak else (None, False)


def tool_event(service, binding, delivery_path, packet, options, resources):
    event, response = packet['event'], deepcopy(packet['response'])
    phase, tool = event['hook_event_name'], event.get('tool_name')
    is_pre = phase == 'PreToolUse'
    previous = response.get('hookSpecificOutput', {})
    if is_pre and previous.get('permissionDecision') == 'deny':
        return response
    policy = CheckTransportPolicy.parse(strict_json(resources[options['policy_path']]))
    native_bound = read_project_checkout(service.settings.project_config, event['cwd'])
    input_value = deepcopy(event['tool_input'])
    transport, selected, request, cli = None, None, None, None
    if tool == options['tool_selectors']['run']:
        selected = arguments(input_value)
        request = check_request(selected)
        chosen = selected['binding']
        config = str(native_bound.config_path) if chosen is None else chosen['config']
        checkout = str(native_bound.checkout) if chosen is None else chosen['checkout']
        checks = options['checks_policy_path'] if chosen is None else chosen['policy']
        transport = 'mcp'
    elif tool == 'Bash':
        command = input_value.get('command')
        if not isinstance(command, str):
            return response
        try:
            cli = configured_cli(command, options['cli_argv'])
        except ValueError:
            return response
        if cli is None:
            if not is_pre:
                return response
            checks_service = project_checks_tools(native_bound, options['checks_policy_path'], dict(os.environ), service.clock)
            name, exact_match = registered_match(shell_words(command), checks_service)
            if name is None:
                return response
            hook = response.setdefault('hookSpecificOutput', {'hookEventName': phase})
            recipe = f"Проверка {name}: используйте {options['tool_selectors']['run']} с selection.id={name}."
            if exact_match:
                hook.update(permissionDecision='deny', permissionDecisionReason=recipe)
            else:
                old = hook.get('additionalContext', '')
                hook['additionalContext'] = old + ('\n' if old else '') + recipe
            return response
        tokens, redirect, names = cli
        if str(Path(names['--transport-policy']).resolve()) != str(Path(options['policy_path']).resolve()):
            raise PoiseError('Configured check transport policy differs from the adopted CLI')
        config, checkout, checks = names['--config'], names['--checkout'], names['--policy']
        limit = native_bound.config['batch']['max_input_bytes']
        source_path = Path(tokens[-1])
        if not source_path.is_absolute():
            source_path = Path(event['cwd']) / source_path
        with source_path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise PoiseError('Check stdin file exceeds configured max_input_bytes')
        request, transport = strict_json(raw.decode('utf-8')), 'cli'
    else:
        return response
    if is_pre:
        prepared = prepare_request(config, checkout, checks, request, service.clock, dict(os.environ))
        bound, checks_service = prepared.bound, prepared.service
    else:
        # Observation never re-admits a consumed diagnostic override or missing executable.
        bound = read_project_checkout(config, checkout)
        checks_service = project_checks_tools(bound, checks, dict(os.environ), service.clock)
    caller = CallerIdentity.native(bound.config['project'], binding['external_session'])
    owner = check_invocation_tools(checks_service, policy, dict(os.environ), caller=caller)
    if is_pre:
        establish_project_session(prepared.bound, caller)
        if prepared.request.operation != 'run':
            return response
        if hashlib.sha256(Path(checks).read_bytes()).hexdigest() != prepared.service.configuration.policy_sha256:
            raise PoiseError('Check policy changed during admission; prepare the current request again')
        source = {'event_receipt': packet['event_receipt'], 'tool_use_id': event['tool_use_id'],
                  'definition_path': binding['definition_path'], 'settings_path': str(service.settings.path),
                  'delivery_path': str(delivery_path)}
        value = owner.prepare(prepared.value, transport, source=source)
        if transport == 'mcp':
            input_value['_invocation'] = value['id']
        else:
            tokens, redirect, names = cli
            tokens[redirect:redirect] = ['--invocation', value['id']]
            input_value['command'] = shlex.join(tokens[:-2]) + ' < ' + shlex.quote(tokens[-1])
        response.setdefault('hookSpecificOutput', {'hookEventName': phase})['updatedInput'] = input_value
        return response
    # Post is observation only. It cannot release a live process on a yielded tool callback.
    identity = input_value.get('_invocation') if transport == 'mcp' else cli[2].get('--invocation')
    if not identity:
        return response
    try:
        value = owner.store.read(identity)
        source = value['source']
        if (value['caller_key'] != caller.key or source is None
                or source['tool_use_id'] != event['tool_use_id']):
            return response
        actual = event.get('tool_response')
        if transport == 'cli' and isinstance(actual, dict) and actual.get('exit_code') is None:
            return response
        if value['state'] == 'prepared':
            owner.store.update(identity, lambda row: row.update(state='not_active'))
    except (PoiseError, OSError, ValueError, KeyError):
        return response
    return response
