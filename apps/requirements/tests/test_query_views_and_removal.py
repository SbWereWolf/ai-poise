"""Real CLI checks arranged entirely through independent test-owned SQL data."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'apps/requirements/fixtures'
DATA = json.loads((FIXTURES / 'query-views-and-removal.json').read_text())


def literal(value):
    if isinstance(value, dict):
        if set(value) == {'$literal'}:
            return literal(DATA['literals'][value['$literal']])
        return {key: literal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [literal(item) for item in value]
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def quoted(value):
    # Structural names are independent fixture declarations, never CLI inputs.
    return '"' + value.replace('"', '""') + '"'


class Scenario:
    def __init__(self, root, case):
        self.root = root
        (root / 'config').mkdir()
        (root / 'state').mkdir()
        self.config = root / 'config/requirements.json'
        self.config.write_bytes((FIXTURES / 'requirements-config.json').read_bytes())
        self.database = root / 'state/requirements.sqlite'
        with closing(sqlite3.connect(self.database)) as connection:
            for source in DATA['schema_fixtures']:
                connection.executescript((FIXTURES / source).read_text())
            for table, columns in DATA['columns'].items():
                rows = literal(DATA['seeds'][case['seed']].get(table, []))
                rows += literal(case.get('extra_rows', {}).get(table, []))
                encoded = [
                    [canonical(value) if column in DATA['json_columns'].get(table, []) else value
                     for column, value in zip(columns, row, strict=True)]
                    for row in rows
                ]
                connection.executemany(
                    'INSERT INTO ' + quoted(table) + '(' + ','.join(map(quoted, columns))
                    + ') VALUES(' + ','.join('?' for column in columns) + ')', encoded,
                )
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            connection.commit()

    def state(self):
        with closing(sqlite3.connect(self.database)) as connection:
            tables = {}
            for table, columns in DATA['columns'].items():
                names = ','.join(map(quoted, columns))
                tables[table] = connection.execute(
                    'SELECT ' + names + ' FROM ' + quoted(table) + ' ORDER BY ' + names,
                ).fetchall()
            return {
                'tables': tables,
                'schema': connection.execute(
                    'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name',
                ).fetchall(),
                'version': connection.execute('PRAGMA user_version').fetchall(),
            }

    def execute(self, step):
        before = self.state()
        environment = dict(os.environ)
        environment['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT)))
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        process = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'apps/requirements/bin/requirements'),
             '--config', str(self.config)],
            cwd=self.root, env=environment, text=True, capture_output=True,
            input=json.dumps(step['packet'], ensure_ascii=False),
        )
        assert process.stderr == '', (step['id'], process.stderr)
        body = json.loads(process.stdout)
        if step.get('rejected'):
            assert process.returncode == 2, (step['id'], process.stdout)
            assert set(body) == {'status', 'reason'}, step['id']
            assert body['status'] == 'rejected', step['id']
            assert isinstance(body['reason'], str) and body['reason'].strip(), step['id']
        else:
            assert process.returncode == 0, (step['id'], step['packet'], process.stdout)
            expected = literal(DATA['responses'][step['expect']])
            assert canonical(body) == canonical(expected), step['id']
        after = self.state()
        assert after['schema'] == before['schema'], step['id']
        assert after['version'] == before['version'], step['id']
        if step.get('unchanged'):
            assert after == before, step['id']
        for table in step.get('preserved_tables', []):
            assert after['tables'][table] == before['tables'][table], (step['id'], table)
        with closing(sqlite3.connect(self.database)) as connection:
            for check in literal(step.get('sql', [])):
                rows = connection.execute(check['query'], check['parameters']).fetchall()
                actual = [
                    [json.loads(value) if index in check.get('json_indexes', []) else value
                     for index, value in enumerate(row)] for row in rows
                ]
                assert canonical(actual) == canonical(check['expected']), (step['id'], check['query'])


@pytest.mark.parametrize('case', DATA['scenarios'], ids=lambda case: case['id'])
def test_public_views_and_removal(tmp_path, case):
    scenario = Scenario(tmp_path, case)
    for step in case['steps']:
        scenario.execute(step)
