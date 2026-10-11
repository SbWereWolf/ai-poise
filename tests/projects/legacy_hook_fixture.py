"""Restore a captured pre-association installation; never install through new code."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
import sqlite3
import sys


SNAPSHOT = Path(__file__).parent / 'fixtures/material_relocation/legacy-hooks-snapshot.json'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def render(value, replacements, *, commands=False):
    if isinstance(value, dict):
        return {k: (shlex.join([render(arg, replacements) for arg in shlex.split(v)])
                    if commands and k == 'command' else render(v, replacements, commands=commands))
                for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, replacements, commands=commands) for v in value]
    if isinstance(value, str):
        for token, actual in replacements.items():
            value = value.replace('${' + token + '}', actual)
    return value


def restore_legacy_hook(case):
    snapshot = json.loads(SNAPSHOT.read_text())
    assert snapshot['schema'] == 'historical-hook-installation-fixture-1'
    assert snapshot['provenance']['producer_commit'] == '7162b84d6bbff375fd7789ac72fdce3171fdf426'
    # Decode captured SQLite JSON columns before substituting portable paths.
    columns = {'installations': [1], 'operations': [1], 'hook_events': [4], 'bindings': [1, 2]}
    for table, rows in snapshot['tables'].items():
        for row in rows:
            for index in columns[table]:
                if row[index] is not None:
                    row[index] = json.loads(row[index])
    substitutions = {'ROOT': str(case['root']), 'CWD_ROOT': str(case['root']), 'PYTHON': sys.executable,
                     'SOURCE_ROOT': str(Path(__file__).resolve().parents[2] / 'src')}
    snapshot = render(snapshot, substitutions)
    snapshot = render(snapshot, {'DEFINITION_DIGEST': sha(snapshot['definition'])}, commands=True)
    native_bytes = (json.dumps(snapshot['native_hooks'], ensure_ascii=False,
                               indent=2, sort_keys=True) + '\n').encode()
    snapshot = render(snapshot, {'NATIVE_REVISION': hashlib.sha256(native_bytes).hexdigest()})
    operation_id, operation = snapshot['tables']['operations'][0]
    snapshot = render(snapshot, {'OPERATION_DIGEST': sha([
        operation_id, None, snapshot['definition'], operation['receipt']['hooks_path'],
    ])})
    settings_path = case['root'] / 'hook-settings.json'
    settings_path.write_text(json.dumps(snapshot['settings'], ensure_ascii=False, indent=2) + '\n')
    settings = snapshot['settings']
    state = case['root'] / settings['state']
    state.mkdir(parents=True, exist_ok=True)
    database = state / settings['database']
    assert not database.exists()
    # This is restoration of recorded test-owned historical input, not a call
    # to a current mutable Hook/Task owner or an invented installation record.
    with sqlite3.connect(database) as db:
        for statement in snapshot['schema_sql']:
            db.execute(statement)
        db.execute('PRAGMA user_version=' + str(snapshot['user_version']))
        for table, rows in snapshot['tables'].items():
            for source in rows:
                row = deepcopy(source)
                for index in columns[table]:
                    if row[index] is not None:
                        row[index] = canonical(row[index])
                db.execute('INSERT INTO "' + table + '" VALUES(' + ','.join('?' for _ in row) + ')', row)
    receipt = snapshot['tables']['installations'][0][1]['receipt']
    definition = Path(receipt['definition_path'])
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(json.dumps(snapshot['definition'], ensure_ascii=False, indent=2) + '\n')
    hooks = Path(receipt['hooks_path'])
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_bytes(native_bytes)
    installation_id = snapshot['tables']['installations'][0][0]
    case['resources']['hooks'] = [{'installation_id': installation_id, 'settings_path': str(settings_path)}]
    # Independent integrity checks prove this is a coherent nonempty baseline.
    assert sha(json.loads(definition.read_text())) == definition.stem
    assert hashlib.sha256(hooks.read_bytes()).hexdigest() == receipt['revision']
    groups = snapshot['tables']['installations'][0][1]['groups']
    assert len(groups) == 4
    assert all(snapshot['native_hooks']['hooks'][kind].count(group) == 1 for kind, group in groups)
    assert snapshot['tables']['bindings'] == []
    assert [row[3] for row in snapshot['tables']['hook_events']] == ['install.pending', 'install.completed']
    from poise.infrastructure.hook_transport import HookSettings
    parsed = HookSettings.proposed(settings_path, settings)
    assert parsed.project_config == case['config_path']
    assert any(case['root'].is_relative_to(Path(root)) for root in parsed.raw['cwd_roots'])
    return {'settings': settings_path, 'database': database, 'definition': definition,
            'native_hooks': hooks, 'installation': snapshot['tables']['installations'][0][1]}
