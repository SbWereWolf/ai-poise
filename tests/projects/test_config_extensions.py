"""Unknown configuration options are inert, not a reason to reject a project."""
from copy import deepcopy
import json

import pytest

from poise.common import load_config
from poise.modules.foundation.errors import PoiseError
from tests.conftest import write_json
from .test_update import installed_project, update_request, update_tools, work_tools


@pytest.mark.parametrize('path', [
    (), ('paths',), ('limits',), ('git',), ('runtime_services',),
    ('runtime_services', 'transfer'), ('runtime_services', 'handoff'),
    ('runtime_services', 'check_runner'), ('runtime_services', 'output'),
    ('runtime_services', 'output', 'unknown'),
    ('runtime_services', 'output', 'unknown', 'files'),
    ('accounting',), ('accounting', 'storage'), ('accounting', 'tokenizer'),
    ('sprint',), ('batch',), ('batch', 'artifact_directories'),
    ('batch', 'message_source'), ('task_decomposition',),
])
def test_unknown_manifest_options_are_preserved_but_do_not_block_loading(project, path):
    _, config_path, _ = installed_project(project)
    original = json.loads(config_path.read_text())
    candidate = deepcopy(original)
    if path in (('runtime_services', 'transfer'), ('runtime_services', 'handoff')):
        candidate['runtime_services'][path[1]] = {}
    target = candidate
    for key in path:
        target = target[key]
    target['future_option'] = {'arbitrary': [None, False, '../not-a-path']}
    write_json(config_path, candidate)
    before = config_path.read_bytes()

    _, loaded, _ = load_config(config_path)

    assert loaded == candidate
    assert config_path.read_bytes() == before


def test_public_edit_preserves_unknown_options_and_replays_exact_revision(project):
    project['cfg']['future_option'] = {'nested': [None, 'arbitrary']}
    project['cfg']['runtime_services']['transfer'] = {'bundles_directory': 'unused operator value'}
    settings, path, created = installed_project(project)
    before = load_config(path)[1]
    packet = update_request(path, created['revision'], manifest_edits=[
        {'path': ['git', 'remote'], 'value': 'edited'}])
    result = update_tools(settings).apply(packet)
    _, observed, workflows = load_config(path)
    expected = deepcopy(before)
    expected['git']['remote'] = 'edited'
    assert observed == expected
    from poise.common import digest
    assert result['revision'] == digest({'config': expected, 'workflows': workflows})
    assert update_tools(settings).apply(packet) == {**result, 'replayed': True}


def test_public_workflow_edit_preserves_raw_extensions_and_revision(project):
    from poise.common import digest
    from .test_update import workflow_change
    project['workflow']['future_option'] = {'value': None}
    project['workflow']['stages'][0]['future_stage_option'] = [False]
    write_json(project['root'] / project['cfg']['workflows']['development'], project['workflow'])
    settings, path, created = installed_project(project)
    _, config, workflows = load_config(path)
    before = deepcopy(workflows['development'])
    packet = update_request(path, created['revision'], workflow_updates=[workflow_change(before)])
    result = update_tools(settings).apply(packet)
    expected = deepcopy(before)
    expected['stages'][0]['instruction'] = packet['workflow_updates'][0]['changes'][0]['set']['instruction']
    _, after_config, after_workflows = load_config(path)
    assert after_config == config
    assert after_workflows['development'] == expected
    assert result['revision'] == digest({'config': config, 'workflows': after_workflows})
    assert update_tools(settings).apply(packet)['revision'] == result['revision']


@pytest.mark.parametrize('path', [(), ('route',), ('benefit',), ('stages', 0),
                                  ('content_contract',)])
def test_unknown_workflow_configuration_options_do_not_relax_task_contract(project, path):
    _, config_path, _ = installed_project(project)
    document = load_config(config_path)[1]
    workflow_path = config_path.parent / document['workflows']['development']
    raw = json.loads(workflow_path.read_text())
    node = raw
    for key in path:
        node = node[key]
    node['future_option'] = None
    write_json(workflow_path, raw)
    before = workflow_path.read_bytes()
    _, _, workflows = load_config(config_path)
    from poise.modules.goal_config.domain import GoalTypeDefinition
    assert workflows['development'] == raw
    assert GoalTypeDefinition.from_configuration(workflows['development']).data == project['workflow']
    with pytest.raises(PoiseError):
        GoalTypeDefinition.parse(raw)
    assert workflow_path.read_bytes() == before


def test_setup_settings_ignore_unknown_options_at_every_fixed_object(project):
    settings, path, created = installed_project(project)
    cfg = json.loads(settings.read_text())
    cfg['future_option'] = None
    cfg['exit_codes']['future_option'] = []
    for entry in cfg['templates'].values():
        entry['future_option'] = False
    write_json(settings, cfg)
    assert update_tools(settings).apply(update_request(path, created['revision']))['status'] == 'updated'


def test_editor_settings_ignore_unknown_options_without_changing_packet_schema(tmp_path):
    from tests.goal_config.helpers import settings, request
    from poise.composition import goal_config_tools
    path, selection = settings(tmp_path)
    raw = json.loads(path.read_text())
    raw['future_option'] = None
    raw['exit_codes']['future_option'] = []
    for item in raw['templates'].values():
        item['future_option'] = False
    write_json(path, raw)
    api = goal_config_tools(path)
    packet = request('create', 'writing', None, [], selection=selection)
    assert api.apply_batch(packet)['status'] == 'applied'
    with pytest.raises(PoiseError):
        api.apply_batch({**packet, 'future_option': True})


def test_hook_settings_ignore_unknown_options(project, tmp_path):
    from tests.hook_transport.helpers import settings
    from poise.infrastructure.hook_transport import HookSettings
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text())
    raw['future_option'] = None
    raw['probe_files']['future_option'] = []
    raw['exit_codes']['future_option'] = []
    write_json(path, raw)
    assert HookSettings(path).raw == raw


def test_runtime_adapter_settings_ignore_unknown_options(project, tmp_path):
    from tests.runtime_services.test_adapter import settings
    from poise.infrastructure.runtime_adapter import validate_settings
    raw = settings(project, tmp_path)
    raw['future_option'] = None
    assert validate_settings(raw) == raw


def test_catalogue_settings_ignore_unknown_options(tmp_path):
    from tests.catalogue.test_catalogue_tools import prepare
    from poise.infrastructure.catalogue import FileCatalogue
    _, catalogue, _, _ = prepare(tmp_path)
    raw = json.loads(catalogue.path.read_text())
    raw['future_option'] = None
    for entry in raw['task_templates'].values():
        entry['future_option'] = []
    write_json(catalogue.path, raw)
    assert FileCatalogue(catalogue.path).raw == raw


def test_state_relocation_holds_all_store_locks_during_copy(project, monkeypatch):
    import fcntl
    from poise.common import configured_root, configured_storage_path, descendant
    import poise.infrastructure.project_config as owner
    from .test_update import relocation
    settings, path, created = installed_project(project)
    root, cfg, _ = load_config(path)
    state = configured_root(root, cfg['paths']['state'])
    state.mkdir(parents=True, exist_ok=True)
    expected = [descendant(state, cfg['batch']['artifact_lock']),
                configured_storage_path(state, cfg['paths']['requirements_lock']),
                configured_storage_path(state, cfg['accounting']['storage']['lock'])]
    original = owner.copy_state_tree
    observed = []
    def copy_under_locks(source, target):
        for lock in expected:
            with lock.open('a+b') as stream:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed.append(lock)
        original(source, target)
    monkeypatch.setattr(owner, 'copy_state_tree', copy_under_locks)
    packet = update_request(path, created['revision'], state_relocation=relocation(
        path, project['root'].parent / 'locked-relocation'))
    assert update_tools(settings).apply(packet)['status'] == 'updated'
    assert observed == expected


@pytest.mark.parametrize('defect', ['missing', 'invalid'])
def test_unknown_option_does_not_hide_invalid_required_option(project, defect):
    _, config_path, _ = installed_project(project)
    config = json.loads(config_path.read_text())
    config['limits']['future_option'] = None
    if defect == 'missing':
        del config['limits']['output_chars']
    else:
        config['limits']['output_chars'] = False
    write_json(config_path, config)
    with pytest.raises(PoiseError, match='output_chars'):
        load_config(config_path)


def test_edit_manifest_with_active_task_preserves_database_and_contract(project):
    settings, config_path, created = installed_project(project)
    work = work_tools(config_path, 'active-owner')
    work.invoke({'operation': 'bootstrap', 'input': {
        'task': project['task'], 'decision': None, 'feedback': None,
        'rework_stage': None}, 'messages': []})
    before = work.runtime.task_queries.record('T1')
    database = work.runtime.store.database.path
    before_bytes = database.read_bytes()
    packet = update_request(config_path, created['revision'], manifest_edits=[
        {'path': ['git', 'remote'], 'value': 'changed'}])

    result = update_tools(settings).apply(packet)

    assert result['status'] == 'updated'
    assert load_config(config_path)[1]['git']['remote'] == 'changed'
    assert database.read_bytes() == before_bytes
    assert work.runtime.task_queries.record('T1') == before
