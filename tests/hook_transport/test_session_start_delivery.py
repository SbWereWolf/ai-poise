"""Session entry remains available across native starts and subject failures."""
import json
from pathlib import Path
import re
import sqlite3

import pytest

from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookService
from hook_transport.helpers import event, install, settings
from batch.helpers import request


@pytest.mark.parametrize('source', ['startup', 'resume', 'clear', 'compact', 'fork'])
def test_every_native_start_receives_its_own_launcher(project, tmp_path, source):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    native = event(session='native-' + source)
    native.update(source=source, cwd=str(project['app']))

    result = service.event(installed['definition_path'], native)

    binding = service.latest_binding(native['session_id'], 'primary')
    assert binding['external_session'] == native['session_id']
    assert binding['session_id'] == native['session_id']
    assert Path(binding['launcher']).is_file()
    assert binding['launcher'] in result['hookSpecificOutput']['additionalContext']


def test_shipped_session_start_matcher_covers_every_native_start():
    root = Path(__file__).resolve().parents[2]
    definition = json.loads((root / 'config/hook-templates/codex-main.json').read_text())
    start = next(item for item in definition['events'] if item['event'] == 'SessionStart')
    for source in ['startup', 'resume', 'clear', 'compact', 'fork']:
        assert re.search(start['matcher'], source), source


def _tasks(database):
    with sqlite3.connect(database) as connection:
        return connection.execute(
            'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata '
            'FROM tasks ORDER BY id').fetchall()


def test_start_delivers_launcher_when_task_workflow_cannot_load(project, tmp_path):
    path = settings(project, tmp_path)
    service = HookService(path)
    installed = install(service)
    baseline = event(session='healthy-baseline')
    baseline['cwd'] = str(project['app'])
    service.event(installed['definition_path'], baseline)
    before = _tasks(project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database'])
    workflow = next(iter(project['cfg']['workflows'].values()))
    (project['config_path'].parent / workflow).unlink()
    native = event(session='broken-workflow')
    native['cwd'] = str(project['app'])

    result = HookService(path).event(installed['definition_path'], native)

    binding = service.latest_binding(native['session_id'], 'primary')
    assert binding['launcher'] in result['hookSpecificOutput']['additionalContext']
    assert str(workflow) in result['systemMessage']
    assert _tasks(project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']) == before


def test_ordinary_work_remains_strict_for_broken_workflow(project, tmp_path, monkeypatch):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    native = event(session='strict-work')
    native['cwd'] = str(project['app'])
    service.event(installed['definition_path'], native)
    binding = service.latest_binding(native['session_id'], 'primary')
    monkeypatch.setenv('CODEX_THREAD_ID', native['session_id'])
    monkeypatch.delenv('CODEX_SESSION_ID', raising=False)
    packet = request('show', {'queries': [{'id': 'messages', 'kind': 'messages'}]})
    assert service.work(binding['binding_path'], packet)['status'] == 'read_only'
    database = project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']
    before = _tasks(database)
    workflow = next(iter(project['cfg']['workflows'].values()))
    (project['config_path'].parent / workflow).unlink()

    with pytest.raises(PoiseError, match=re.escape(str(workflow))):
        service.work(binding['binding_path'], packet)

    assert _tasks(database) == before


def test_start_keeps_launcher_and_reports_runtime_failure(project, tmp_path, monkeypatch):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    native = event(session='broken-runtime')
    native['cwd'] = str(project['app'])

    def broken_runtime(*args, **kwargs):
        raise PoiseError('Selected task runtime is unavailable')

    monkeypatch.setattr('poise.infrastructure.hook_transport.establish_poise', broken_runtime)
    result = service.event(installed['definition_path'], native)

    binding = service.latest_binding(native['session_id'], 'primary')
    assert binding['launcher'] in result['hookSpecificOutput']['additionalContext']
    assert 'Selected task runtime is unavailable' in result['systemMessage']
    with sqlite3.connect(service.settings.database) as connection:
        rows = connection.execute('SELECT event,data FROM hook_events WHERE binding=?',
                                  (binding['session_id'],)).fetchall()
    assert [(kind, json.loads(raw)) for kind, raw in rows] == [
        ('SessionStart', {'turn_id': None, 'source': 'startup'})]
