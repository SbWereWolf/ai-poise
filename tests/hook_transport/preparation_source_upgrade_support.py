"""Transparent test inputs and direct observations, not launcher producers."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

from conftest import write_json
from poise.common import digest, file_digest
from poise.infrastructure.hook_transport import HookService
from poise.interfaces.hook_transport import execute
from hook_transport.helpers import event, install, settings

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/source_upgrade'
FORMS = ('H1', 'H2', 'H3', 'H4', 'H5')
SESSION = 'source-upgrade-session'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def fixture_script(form, coordinates):
    """Render literal test data using only standard formatting and shell quoting."""
    code = Path(coordinates['code_root'])
    source = str(code / 'src')
    values = dict(coordinates, source_root=source,
                  source_python_path='PYTHONPATH=' + source,
                  current_python_path='PYTHONPATH=' + os.pathsep.join((source, str(code))),
                  guard=(FIXTURES / 'guard.txt').read_text().removesuffix('\n'))
    values['recovery_command'] = shlex.join([
        'bash', str(code / 'src/poise/recover_runtime.sh'), '--settings', coordinates['settings'],
        '--python', '/absolute/path/to/compatible/python'])
    return (FIXTURES / (form + '.sh.in')).read_text().format_map(
        {key: shlex.quote(value) for key, value in values.items()}).encode('utf-8')


def copy_candidate(destination):
    destination.mkdir(parents=True)
    ignored = shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info')
    shutil.copytree(ROOT / 'src', destination / 'src', ignore=ignored)
    shutil.copytree(ROOT / 'apps/bootstrap', destination / 'apps/bootstrap', ignore=ignored)
    for name in ('stride.sh', 'pyproject.toml'):
        shutil.copy2(ROOT / name, destination / name)
    original = source_inventory(ROOT)
    assert source_inventory(destination) == original
    return destination


def source_inventory(root):
    paths = [root / 'stride.sh', root / 'pyproject.toml']
    for prefix in ('src', 'apps/bootstrap'):
        paths.extend(p for p in (root / prefix).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts
                     and not p.name.endswith('.pyc') and not any(part.endswith('.egg-info') for part in p.parts))
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def arrange_hooks(project, tmp_path, code_root=ROOT):
    path = settings(project, tmp_path)
    raw = read_json(path)
    raw['source_root'] = str(code_root / 'src')
    raw['python'] = sys.executable
    write_json(path, raw)
    foreign = read_json(FIXTURES / 'foreign-hooks.json')
    write_json(path.parent / raw['hooks_file'], foreign)
    service = HookService(path)
    installed = install(service, revision=service.revision())
    for kind in ('SessionStart', 'UserPromptSubmit'):
        native = event(kind, session=SESSION)
        native['cwd'] = str(project['app'])
        service.event(installed['definition_path'], native)
    # Read actual allocation identity independently, never a command serializer.
    database = path.parent / raw['state'] / raw['database']
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        binding_raw, = connection.execute('SELECT data FROM bindings WHERE id=?', (SESSION,)).fetchone()
    binding = json.loads(binding_raw)
    coordinates = {'code_root': str(code_root), 'python': sys.executable,
                   'settings': str(path), 'binding': binding['binding_path']}
    return {'project': project, 'service': service, 'settings_path': path, 'settings': raw,
            'database': database, 'installed': installed, 'binding': binding,
            'coordinates': coordinates, 'foreign': foreign}


def snapshot(case):
    with sqlite3.connect(case['database'].as_uri() + '?mode=ro', uri=True) as connection:
        rows = {
            'bindings': connection.execute('SELECT id,data,latest_message FROM bindings ORDER BY id').fetchall(),
            'installations': connection.execute('SELECT id,data FROM installations ORDER BY id').fetchall(),
            'operations': connection.execute('SELECT id,data FROM operations ORDER BY id').fetchall(),
            'events': connection.execute('SELECT id,at,binding,event,data FROM hook_events ORDER BY id').fetchall(),
        }
    paths = [case['settings_path'], case['settings_path'].parent / case['settings']['hooks_file'],
             Path(case['binding']['binding_path']), Path(case['binding']['launcher'])]
    return {'rows': rows, 'files': {str(p): p.read_bytes() for p in paths}}


def lifecycle(rows):
    return [row for row in rows if row[3] in ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd')]


def assert_session_preserved(before, after, *, reconfigured=False):
    assert len(before['rows']['bindings']) == len(after['rows']['bindings']) == 1
    old, new = before['rows']['bindings'][0], after['rows']['bindings'][0]
    assert old[0] == new[0] == SESSION and old[2] == new[2]
    previous, current = json.loads(old[1]), json.loads(new[1])
    fields = ('session_id', 'external_session', 'agent_id', 'project', 'settings',
              'binding_path', 'launcher')
    assert {key: current[key] for key in fields} == {key: previous[key] for key in fields}
    if not reconfigured:
        assert old == new
    assert after['rows']['events'][:len(before['rows']['events'])] == before['rows']['events']


def write_launcher(case, form):
    path = Path(case['binding']['launcher'])
    path.write_bytes(fixture_script(form, case['coordinates']))
    path.chmod(case['settings']['executable_mode'])


def resume(case, definition_path=None):
    native = event(session=SESSION)
    native.update(source='resume', cwd=str(case['project']['app']))
    return HookService(case['settings_path']).event(
        definition_path or case['installed']['definition_path'], native)


def reconfigure_packet(case):
    target = deepcopy(case['settings'])
    target['output_chars'] = 6000
    packet = read_json(FIXTURES / 'reconfigure-request.json')
    packet.update(expected_settings_digest=digest(case['settings']),
                  expected_revision=file_digest(case['settings_path'].parent / target['hooks_file']),
                  target_revision=file_digest(case['settings_path'].parent / target['hooks_file']),
                  settings=target, definition=read_json(case['installed']['definition_path']))
    return packet


def public_reconfigure(case, packet):
    output, error = io.StringIO(), io.StringIO()
    code = execute('runtime-config', SimpleNamespace(settings=str(case['settings_path'])),
                   io.BytesIO(json.dumps({'operation': 'reconfigure', 'input': packet}).encode()),
                   output, error)
    assert error.getvalue() == ''
    envelope = json.loads(output.getvalue())
    return code, read_json(envelope['response_path'])


def assert_current(case):
    path = Path(case['binding']['launcher'])
    assert path.read_bytes() == fixture_script('current', case['coordinates'])
    assert path.stat().st_mode & 0o777 == case['settings']['executable_mode']


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if key not in ('PYTHONPATH', 'PYTHONHOME', 'POISE_CONFIG')}


def assert_probe(observed, code_root, expected_config):
    assert observed['status'] == 'observed', observed
    assert Path(observed['poise_origin']).is_relative_to(code_root)
    assert Path(observed['handler_origin']).is_relative_to(code_root)
    raw = (FIXTURES / 'ready.stdout').read_text()
    diagnostic = (FIXTURES / 'diagnostic.stderr').read_text()
    assert observed['handler'] == ready_handler_result(raw, diagnostic)
    assert observed['project_config'] == expected_config


def ready_handler_result(stdout, stderr):
    expected = read_json(FIXTURES / 'handler-result.template.json')
    expected['observations'][0].update(stdout=stdout, stderr=stderr)
    return expected
