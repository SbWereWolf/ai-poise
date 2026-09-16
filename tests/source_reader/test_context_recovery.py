"""C023: recover current owner metadata, never copy Task lifecycle or remembered text."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from conftest import WorkPoise, write_json, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.source_reader import FileSourceReader

ROOT = Path(__file__).resolve().parents[2]


def packet(reason='resume', event_id='resume-1', cwd='.', reads=None):
    return {'operation': 'restore_context', 'messages': [], 'input': {
        'reason': reason, 'event_id': event_id, 'facts': [], 'cwd': str(cwd),
        'reads': [] if reads is None else reads}}


def reader(runtime):
    runtime.context_reader = FileSourceReader(runtime.runtime / 'reader.json',
        json.loads((ROOT / 'config/development/source-reader.json').read_text()))
    return runtime.context_reader


def test_taskless_resume_and_replay_do_not_create_task(project):
    h = WorkPoise(project['config_path'], 'R'); r = reader(h)
    result = WorkTools(h).invoke(packet(cwd=project['app']))
    assert result['status'] == 'context_restored'
    assert result['task'] is None
    assert result['development_route'] == {'status': 'not_applicable', 'reason': 'taskless'}
    assert result['reader']['generation'] == 2
    assert result['read_result'] is None
    assert h.current_task() is None
    assert WorkTools(h).invoke(packet(cwd=project['app']))['reader'] == result['reader']
    assert r.context()['generation'] == 2


def test_compaction_rereads_unchanged_bytes_despite_old_ack(project, tmp_path):
    h = WorkPoise(project['config_path'], 'R'); r = reader(h)
    file = tmp_path / 'outside-poise.txt'; file.write_text('unchanged text\n')
    selected = {'path': str(file), 'start': 1, 'end': 1, 'reread': False, 'reason': ''}
    old = r.read({'cwd': str(tmp_path), 'generation': 1, 'acknowledged': [], 'reads': [selected]})
    ack = old['items'][0]['receipt_id']
    assert r.read({'cwd': str(tmp_path), 'generation': 1, 'acknowledged': [ack], 'reads': [selected]})['items'][0]['status'] == 'unchanged'
    result = WorkTools(h).invoke(packet('compact', 'compact-1', tmp_path, [selected]))
    assert result['reader']['generation'] == 2
    assert result['read_result']['items'][0]['text'] == 'unchanged text\n'
    assert result['read_result']['items'][0]['status'] == 'read'
    assert result['remembered_text'] is False
    with pytest.raises(PoiseError, match='generation changed'):
        r.read({'cwd': str(tmp_path), 'generation': 1, 'acknowledged': [ack], 'reads': [selected]})


def test_active_task_metadata_preserves_exact_owner_state_without_worktree(project):
    project['process']['worktree_required'] = False
    write_json(project['root'] / 'config/processes/development.json', project['process'])
    h = WorkPoise(project['config_path'], 'R'); reader(h)
    h.bootstrap(project['task']); before = deepcopy(h.current_task())
    out = WorkTools(h).invoke(packet(cwd=project['app']))
    assert out['task']['id'] == 'T1'
    assert out['task']['stage'] == 'tests'
    assert out['task']['iteration'] == 1
    assert out['task']['worktree'] is None
    assert out['development_route'] == {'status': 'not_configured'}
    assert out['task']['requirements'] == ['double(n) возвращает n*2']
    assert h.current_task() == before
    raw = (h.runtime / 'reader.json').read_text()
    assert 'T1' not in raw and 'requirements' not in raw


def test_missing_reader_config_is_explicit_and_does_not_claim(project):
    h = WorkPoise(project['config_path'], 'R')
    with pytest.raises(PoiseError, match='source_reader is not configured'):
        WorkTools(h).invoke(packet())
    assert h.current_task() is None


@pytest.mark.parametrize('patch', [{'reason': 'invented'}, {'event_id': ''}, {'reads': 'all'}, {'facts': ['x', 'x']}])
def test_invalid_recovery_input_does_not_reset_reader(project, patch):
    h = WorkPoise(project['config_path'], 'R'); r = reader(h)
    before = r.context(); req = packet(); req['input'].update(patch)
    with pytest.raises(PoiseError): WorkTools(h).invoke(req)
    assert r.context() == before


def test_native_compact_resets_reader_and_preserves_source_provenance(project, tmp_path):
    from poise.infrastructure.hook_transport import HookService
    from hook_transport.helpers import settings, install, event
    project['cfg']['source_reader'] = {'policy': str(ROOT / 'config/development/source-reader.json'),
                                     'receipt_file': 'reader.json'}
    s = HookService(settings(project, tmp_path)); installed = install(s)
    e = event(); e['cwd'] = str(project['app'])
    s.event(installed['definition_path'], e)
    binding = s.latest_binding('conversation', 'primary')
    h = s.bound_runtime(binding['binding_path'])
    before = h.context_reader.context()
    e['source'] = 'compact'
    reply = s.event(installed['definition_path'], e)
    after = h.context_reader.context()
    assert after['generation'] == before['generation'] + 1
    assert after['reason'] == 'compact'
    assert after['event_id'].startswith('hook-event:')
    assert 'restore_context' in reply['hookSpecificOutput']['additionalContext']
    assert h.current_task() is None
    with s.registry.transaction() as db:
        saved = db.execute("SELECT data FROM hook_events WHERE event='SessionStart' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert json.loads(saved)['source'] == 'compact'


def test_optional_config_does_not_allow_receipts_outside_session(project):
    project['cfg']['source_reader'] = {'policy': str(ROOT / 'config/development/source-reader.json'),
                                     'receipt_file': '../foreign.json'}
    write_json(project['config_path'], project['cfg'])
    with pytest.raises(PoiseError): WorkPoise(project['config_path'], 'R')


def test_resume_recomputes_route_instead_of_trusting_saved_snapshot(project, tmp_path):
    from skills.test_development_routing import configured as configured_fixture
    from skills.test_development_routing import write
    import shutil
    paths, _, copy_path = configured_fixture.__wrapped__(tmp_path)
    for name in ('pyproject.toml', '.agents'):
        source = copy_path / name; target = project['app'] / name
        shutil.copytree(source, target) if source.is_dir() else shutil.copyfile(source, target)
    git(project['app'], 'add', '.'); git(project['app'], 'commit', '-m', 'fixture')
    policy = json.loads(Path(paths['policy']).read_text()); policy['known_paths'].append('tests/**')
    write(Path(paths['policy']), policy)
    project['cfg']['development_routing'] = paths
    write_json(project['config_path'], project['cfg'])
    h = WorkPoise(project['config_path'], 'R'); reader(h)
    ctx = h.bootstrap(project['task']); before = deepcopy(h.current_task())
    (h.runtime / 'development-routing.json').write_text('{"skills":["forged"]}')
    copy = Path(ctx['worktree']); write(copy / 'src/new.py', 'x = 1\n')
    result = WorkTools(h).invoke(packet(cwd=copy))
    assert result['development_route']['skills'] == ['poise', 'tdd']
    assert result['development_route']['checks']['required_methods'] == ['RED']
    assert result['development_route']['input']['changed_paths'] == ['src/new.py']
    assert h.current_task() == before


def test_concurrent_task_change_invalidates_recovery_not_task(project):
    from poise.application.context_recovery import ContextRecovery
    h = WorkPoise(project['config_path'], 'R'); r = reader(h)
    class MovingOwner:
        def context_metadata(self, facts): return {'task': None}
        def current_task(self): return {'id': 'new-task', 'version': 9}
    with pytest.raises(PoiseError, match='Task context changed'):
        ContextRecovery(MovingOwner(), r).restore(packet()['input'])
    assert r.context()['generation'] == 2
