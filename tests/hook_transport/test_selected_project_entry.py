"""Selected-project work through the actual supplied Bash entry and public CLI."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
from string import Template
import subprocess

import pytest

from conftest import write_json
from hook_transport.helpers import definition, event, \
    host_environment, install, settings
from poise.common import digest
from poise.infrastructure.hook_transport import HookService

PACKET = Path(__file__).with_name(
    'fixtures') / 'selected-project-show.json'


def rows(database):
    with sqlite3.connect('file:' + str(database) + '?mode=ro',
                         uri=True) as db:
        return {
            'tasks': db.execute(
                'SELECT id,status,claimed_by,version FROM tasks ORDER BY id').fetchall(),
            'sessions': db.execute(
                'SELECT id FROM sessions ORDER BY id').fetchall(),
        }


@pytest.fixture
def selected_case(project, tmp_path, request):
    hook_settings = settings(project, tmp_path)
    cfg = project['cfg']
    setup_path = Path(cfg['runtime_resources']['project_setup'])
    setup = json.loads(setup_path.read_text())
    tools = (setup_path.parent / setup['root']).resolve()
    registry = tools / setup['registry']
    other = deepcopy(cfg)
    other['project'] = 'selected-project'
    other['paths']['state'] = str(tools / 'selected-data')
    other['batch']['message_source'] = {'id': 'selected-visible',
                                        'mode': 'agent_reported'}
    other['runtime_resources']['hooks'] = []
    other_path = tools / 'selected-config' / 'project.json'
    for relative in cfg['workflows'].values():
        target = other_path.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project['config_path'].parent / relative, target)
    write_json(other_path, other)
    listing = json.loads(registry.read_text())
    listing['projects']['selected-project'] = {
        'config_path': str(other_path.relative_to(tools)),
    }
    write_json(registry, listing)
    launch_path = write_json(tools / 'selected-launcher.json', {
        'schema': 'stride-launcher-1',
        'poise_root': str(tools),
        'data_root': str(tmp_path),
        'project_id': cfg['project'],
        'project_setup': str(setup_path),
        'command': [str(project['app'] / 'unused-command')],
        'overrides': {},
        'exit_codes': {'rejected': 2},
    })
    raw = json.loads(hook_settings.read_text())
    if getattr(request, 'param', True):
        raw['project_launcher'] = {
            'settings_path': str(launch_path),
            'context_template': 'Use {launcher} --project PROJECT_ID for selected-project operations.',
        }
    write_json(hook_settings, raw)
    service = HookService(hook_settings)
    selected_definition = definition()
    if getattr(request, 'param', None) == 'blocked-capability':
        selected_definition['probes'][0]['stdout_contains'] = [
            'not-ready']
    installed = install(service, selected_definition)
    native = event(session='entry-owner')
    native['cwd'] = str(project['app'])
    context = service.event(installed['definition_path'], native)
    binding = service.latest_binding('entry-owner', 'primary')
    state = project['root'] / cfg['paths']['state']
    return dict(project=project, service=service, installed=installed,
                context=context,
                binding=binding, hook_settings=hook_settings,
                launch_path=launch_path,
                registry=registry, other=other, other_path=other_path,
                tools=tools,
                source_db=state / cfg['paths']['database'],
                selected_db=tools / 'selected-data' / cfg['paths'][
                    'database'])


def invoke(case, *arguments, session='entry-owner',
           missing_identity=False,
           conflicting_identity=False, packet_path=PACKET):
    environment = host_environment(session)
    if missing_identity:
        environment.pop('CODEX_THREAD_ID')
    if conflicting_identity:
        environment['CODEX_SESSION_ID'] = 'different-host-identity'
    return subprocess.run(
        ['bash', case['binding']['launcher'], *arguments],
        input=packet_path.read_bytes(), capture_output=True,
        env=environment, cwd=case['project']['app'],
    )


def full_response(result):
    assert result.returncode == 0, (result.stdout, result.stderr)
    envelope = json.loads(result.stdout)
    response = Path(envelope['response_path'])
    full = json.loads(response.read_text())
    assert full['status'] == 'read_only'
    return envelope, response, full


def test_supplied_entry_uses_explicit_selected_project_without_rebinding_source(
        selected_case):
    case = selected_case
    before = rows(case['source_db'])
    binding_bytes = Path(case['binding']['binding_path']).read_bytes()

    envelope, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))

    assert envelope['session'] == 'entry-owner'
    assert response.is_relative_to(case['tools'] / 'selected-data')
    assert case['selected_db'].is_file()
    assert rows(case['selected_db']) == {'tasks': [], 'sessions': [
        ('entry-owner',)]}
    assert rows(case['source_db']) == before
    assert Path(
        case['binding']['binding_path']).read_bytes() == binding_bytes


def test_explicit_bound_project_keeps_existing_hook_owner(
        selected_case):
    case = selected_case
    envelope, response, _ = full_response(
        invoke(case, '--project', case['project']['cfg']['project']))

    assert envelope['session'] == 'entry-owner'
    assert not response.is_relative_to(
        case['tools'] / 'selected-data')
    assert not case['selected_db'].exists()


def test_absent_selector_keeps_the_existing_bound_entry_contract(
        selected_case):
    case = selected_case
    _, response, _ = full_response(invoke(case))

    assert not response.is_relative_to(
        case['tools'] / 'selected-data')
    assert not case['selected_db'].exists()


@pytest.mark.parametrize('arguments', [
    ('--project', ''),
    ('--project', 'selected-project', '--project',
     'selected-project'),
    ('--project', 'selected-project', '--project', 'other-project'),
    ('--project',),
])
def test_invalid_or_ambiguous_selector_refuses_before_project_work(
        selected_case, arguments):
    case = selected_case
    before = rows(case['source_db'])

    result = invoke(case, *arguments)

    assert result.returncode == 2
    assert result.stdout or result.stderr
    assert rows(case['source_db']) == before
    assert not case['selected_db'].exists()


def test_unknown_selected_project_refuses_without_running_bound_project(
        selected_case):
    case = selected_case
    before = rows(case['source_db'])
    result = invoke(case, '--project', 'selected')

    assert result.returncode == 2
    assert 'unknown configured project' in result.stdout.decode().lower()
    assert rows(case['source_db']) == before
    assert not case['selected_db'].exists()


@pytest.mark.parametrize('selected', ['bound', 'selected-project'])
@pytest.mark.parametrize('caller', ['foreign-caller', 'fork-child'])
def test_foreign_native_caller_cannot_select_through_someone_elses_entry(
        selected_case, selected, caller):
    case = selected_case
    before = rows(case['source_db'])
    project_id = case['project']['cfg'][
        'project'] if selected == 'bound' else selected
    result = invoke(case, '--project', project_id, session=caller)

    assert result.returncode == 2
    assert 'does not own this launcher' in result.stdout.decode()
    assert rows(case['source_db']) == before
    assert not case['selected_db'].exists()


@pytest.mark.parametrize('selected', ['bound', 'selected-project'])
@pytest.mark.parametrize('missing,conflicting',
                         [(True, False), (False, True)])
def test_missing_or_conflicting_native_identity_has_no_target_effects(
        selected_case, selected, missing, conflicting):
    case = selected_case
    before = rows(case['source_db'])
    project_id = case['project']['cfg'][
        'project'] if selected == 'bound' else selected

    result = invoke(case, '--project', project_id,
                    missing_identity=missing,
                    conflicting_identity=conflicting)

    assert result.returncode == 2
    assert 'native session identity' in result.stdout.decode()
    assert rows(case['source_db']) == before
    assert not case['selected_db'].exists()


def test_unrelated_invalid_registry_entry_does_not_replace_the_valid_selection(
        selected_case):
    case = selected_case
    registry = json.loads(case['registry'].read_text())
    registry['projects']['broken-neighbor'] = {
        'config_path': 'missing-neighbor/project.json'}
    write_json(case['registry'], registry)

    _, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))

    assert response.is_relative_to(case['tools'] / 'selected-data')


def test_selected_data_outside_configured_root_refuses_before_project_work(
        selected_case):
    case = selected_case
    before = rows(case['source_db'])
    raw = json.loads(case['launch_path'].read_text())
    raw['data_root'] = str(case['project']['app'])
    write_json(case['launch_path'], raw)

    result = invoke(case, '--project', 'selected-project')

    assert result.returncode == 2
    assert 'data_root' in result.stdout.decode()
    assert rows(case['source_db']) == before
    assert not case['selected_db'].exists()


def test_project_registry_address_is_reread_by_the_same_entry(
        selected_case):
    case = selected_case
    full_response(invoke(case, '--project', 'selected-project'))
    moved = case['tools'] / 'moved-config' / 'project.json'
    shutil.copytree(case['other_path'].parent, moved.parent)
    new_cfg = deepcopy(case['other'])
    new_cfg['paths']['state'] = str(
        case['tools'] / 'new-selected-data')
    write_json(moved, new_cfg)
    registry = json.loads(case['registry'].read_text())
    registry['projects']['selected-project']['config_path'] = str(
        moved.relative_to(case['tools']))
    write_json(case['registry'], registry)

    _, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))

    assert response.is_relative_to(
        case['tools'] / 'new-selected-data')


@pytest.mark.parametrize('selected_case', [False], indirect=True)
def test_selection_without_launcher_configuration_refuses_explicitly(
        selected_case):
    case = selected_case
    result = invoke(case, '--project', 'selected-project')

    assert result.returncode == 2
    assert 'project_launcher' in result.stdout.decode()
    assert not case['selected_db'].exists()


def arrange_selected_hooks(case, agent='primary'):
    raw = json.loads(case['hook_settings'].read_text())
    target_path = case['tools'] / 'selected-hook-settings.json'
    raw.pop('project_launcher', None)
    raw.update(root=str(case['tools']),
               project_config=str(
                   case['other_path'].relative_to(case['tools'])),
               state='selected-data/hooks',
               lock='selected-data/hooks.lock',
               definitions='selected-hook-definitions',
               hooks_file='selected-config/.codex/hooks.json')
    write_json(target_path, raw)
    cfg = deepcopy(case['other'])
    cfg['batch']['message_source'] = {'id': 'codex-hook-main',
                                      'mode': 'runtime_event'}
    cfg['runtime_resources']['hooks'] = [
        {'installation_id': 'primary-hooks',
         'settings_path': str(target_path)},
    ]
    write_json(case['other_path'], cfg)
    service = HookService(target_path)
    selected_definition = definition()
    selected_definition['agent_id'] = agent
    return service, install(service, selected_definition)


def test_selected_hooked_project_requires_its_own_native_binding(
        selected_case):
    case = selected_case
    arrange_selected_hooks(case)
    before = rows(case['source_db'])

    result = invoke(case, '--project', 'selected-project')

    assert result.returncode == 2
    assert 'own native' in result.stdout.decode().lower()
    assert rows(case['source_db']) == before
    assert not case['selected_db'].exists()


def test_selected_hooked_project_uses_its_own_existing_binding(
        selected_case):
    case = selected_case
    target, installed = arrange_selected_hooks(case)
    native = event(session='entry-owner')
    native['cwd'] = str(case['project']['app'])
    target.event(installed['definition_path'], native)
    binding = target.latest_binding('entry-owner', 'primary')
    before = rows(case['source_db'])

    envelope, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))

    assert envelope['session'] == 'entry-owner'
    assert response.is_relative_to(case['tools'] / 'selected-data')
    assert rows(case['source_db']) == before
    assert target.latest_binding('entry-owner', 'primary') == binding


def test_public_reconfigure_upgrades_the_exact_owned_non_forwarding_entry(
        selected_case):
    case = selected_case
    raw = json.loads(case['hook_settings'].read_text())
    values = [raw['source_root'], raw['python'],
              str(case['hook_settings']),
              case['binding']['binding_path']]
    assert all(' ' not in value for value in
               values), 'This historical input uses plain shell words'
    template = PACKET.with_name(
        'selected-project-old-launcher.sh').read_text()
    old_script = Template(template).substitute(
        SOURCE_ROOT=values[0], PYTHON=values[1],
        SETTINGS_PATH=values[2], BINDING_PATH=values[3])
    Path(case['binding']['launcher']).write_text(old_script)
    proposed = deepcopy(raw)
    proposed.update(
        hooks_file=str(case['tools'] / 'native-user' / 'hooks.json'),
        hooks_lock=str(case['tools'] / 'native-user' / 'hooks.lock'),
        repository_scope={'repository': str(case['project']['app']),
                          'timeout_seconds': 3.0},
    )
    before = rows(case['source_db'])

    result = case['service'].reconfigure({
        'request_id': 'upgrade-selected-project-entry',
        'expected_settings_digest': digest(raw),
        'expected_revision': case['service'].revision(),
        'target_revision': None,
        'settings': proposed,
        'definition': case['service'].definition(
            case['installed']['definition_path']).data,
    })

    assert result['binding_count'] == 1
    assert Path(case['binding']['launcher']).read_text().endswith(
        ' "$@"\n')
    assert rows(case['source_db']) == before
    _, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))
    assert response.is_relative_to(case['tools'] / 'selected-data')


def message_rows(database):
    with sqlite3.connect('file:' + str(database) + '?mode=ro',
                         uri=True) as db:
        return db.execute(
            "SELECT project,json_extract(data,'$.source.id'),json_extract(data,'$.source.mode'),"
            "json_extract(data,'$.message_id') FROM interaction_events ORDER BY id"
        ).fetchall()


def record_source_prompt(case):
    prompt = event('UserPromptSubmit', session='entry-owner',
                   turn='source-only-turn')
    prompt['cwd'] = str(case['project']['app'])
    case['service'].event(case['installed']['definition_path'],
                          prompt)
    before = message_rows(case['source_db'])
    assert len(before) == 1
    assert before[0][1:] == ('codex-hook-main', 'runtime_event',
                             'source-only-turn')
    return before


def test_source_prompt_is_not_injected_into_hookless_selected_project(
        selected_case):
    case = selected_case
    before = record_source_prompt(case)

    _, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))

    assert response.is_relative_to(case['tools'] / 'selected-data')
    assert case['selected_db'].is_file()
    assert message_rows(case['selected_db']) == []
    assert message_rows(case['source_db']) == before


def test_selected_project_retains_its_own_reported_message(
        selected_case):
    case = selected_case
    before = record_source_prompt(case)

    _, response, _ = full_response(invoke(
        case, '--project', 'selected-project',
        packet_path=PACKET.with_name(
            'selected-project-reported-message.json')))

    assert response.is_relative_to(case['tools'] / 'selected-data')
    assert case['selected_db'].is_file()
    assert message_rows(case['selected_db']) == [
        ('selected-project', 'selected-visible', 'agent_reported',
         'selected-visible-turn'),
    ]
    assert message_rows(case['source_db']) == before


@pytest.mark.parametrize('selected_case', ['blocked-capability'],
                         indirect=True)
def test_explicit_current_project_retains_unavailable_capability_gate(
        selected_case):
    case = selected_case
    packet = PACKET.parent / 'selected-project-taskless-verify.json'
    before = rows(case['source_db'])

    for arguments in [
        ('--project', case['project']['cfg']['project']), ()]:
        result = invoke(case, *arguments, packet_path=packet)
        assert result.returncode == 1, (result.stdout, result.stderr)
        envelope = json.loads(result.stdout)
        full = json.loads(Path(envelope['response_path']).read_text())
        assert full['status'] == 'capabilities_unavailable'
        assert full['capability_checks']['ready'] is False
        assert full['capability_checks']['missing_required'] == [
            'python']
        observed, = full['capability_checks']['observations']
        assert (observed['id'], observed['status'],
                observed['observation']) == (
                   'python', 'unavailable', 'probe_failed')
        receipt = json.loads(Path(observed['receipt']).read_text())
        assert receipt['status'] == 'unavailable'
        assert receipt['actual_exit_code'] == 0
        assert receipt['timed_out'] is False
        assert receipt[
                   'error'] == 'Probe output did not match explicit predicates'

    assert rows(case['source_db']) == before
    assert not case['selected_db'].exists()


def test_valid_selection_is_independent_of_broken_source_subject_workflow(
        selected_case):
    case = selected_case
    relative = next(
        iter(case['project']['cfg']['workflows'].values()))
    (case['project']['config_path'].parent / relative).unlink()

    _, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))

    assert response.is_relative_to(case['tools'] / 'selected-data')


def test_other_hooked_profile_uses_its_genuine_target_binding(
        selected_case):
    case = selected_case
    target, installed = arrange_selected_hooks(case,
                                               agent='selected-profile')
    native = event(session='entry-owner')
    native['cwd'] = str(case['project']['app'])
    target.event(installed['definition_path'], native)
    binding = target.latest_binding('entry-owner', 'selected-profile')
    before = rows(case['source_db'])

    _, response, _ = full_response(
        invoke(case, '--project', 'selected-project'))

    assert response.is_relative_to(case['tools'] / 'selected-data')
    assert rows(case['source_db']) == before
    assert target.latest_binding('entry-owner',
                                 'selected-profile') == binding


@pytest.mark.parametrize('source',
                         ['startup', 'resume', 'fork', 'clear',
                          'compact'])
def test_native_context_explains_explicit_project_selection(
        selected_case, source):
    case = selected_case
    native = event(session='context-' + source)
    native.update(cwd=str(case['project']['app']), source=source)

    result = case['service'].event(
        case['installed']['definition_path'], native)

    text = result['hookSpecificOutput']['additionalContext']
    binding = case['service'].latest_binding('context-' + source,
                                             'primary')
    assert binding['launcher'] in text
    assert '--project' in text
