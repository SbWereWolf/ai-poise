"""Real CLI scenarios with independently authored complete response oracles."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'apps/requirements/fixtures'
CASES = json.loads((FIXTURES / 'outcome-cascade.json').read_text())


def literal(value):
    if isinstance(value, dict):
        if set(value) == {'$literal'}:
            return literal(CASES['literals'][value['$literal']])
        return {key: literal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [literal(item) for item in value]
    return value


class Scenario:
    def __init__(self, root):
        self.root = root
        (root / 'config').mkdir()
        self.config = root / 'config/requirements.json'
        self.config.write_bytes((FIXTURES / 'requirements-config.json').read_bytes())
        self.database = root / 'state/requirements.sqlite'

    def state(self):
        with sqlite3.connect(self.database) as connection:
            return tuple(connection.iterdump())

    def execute(self, step):
        before = self.state() if step.get('unchanged') else None
        environment = dict(os.environ)
        environment['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT)))
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        process = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'apps/requirements/bin/requirements'),
             '--config', str(self.config)],
            cwd=self.root, env=environment, text=True, capture_output=True,
            input=json.dumps(step['packet'], ensure_ascii=False), timeout=30,
        )
        assert process.stderr == '', (step['id'], process.stderr)
        body = json.loads(process.stdout)
        if step.get('rejected'):
            assert process.returncode == 2, (step['id'], process.stdout)
            assert set(body) == {'status', 'reason'}
            assert body['status'] == 'rejected'
            assert isinstance(body['reason'], str) and body['reason'].strip()
        else:
            assert process.returncode == 0, (step['id'], step['packet'], process.stdout)
            expected = literal(CASES['responses'][step['expect']])
            assert json.dumps(body, sort_keys=True) == json.dumps(expected, sort_keys=True), step['id']
        if before is not None:
            assert self.state() == before, step['id']
        if 'sql_facts' in step:
            with sqlite3.connect(self.database) as connection:
                rows = connection.execute(
                    'SELECT outcome_id,task_id,snapshot_revision,kind,keys '
                    'FROM requirement_outcomes ORDER BY outcome_id',
                ).fetchall()
                actual = [list(row[:4]) + [json.loads(row[4])] for row in rows]
                assert actual == CASES['sql_facts'][step['sql_facts']], step['id']
                assert connection.execute(
                    'SELECT revision FROM requirements_meta',
                ).fetchall() == [(step['owner_revision'],)], step['id']
        if 'sql_snapshots' in step:
            with sqlite3.connect(self.database) as connection:
                rows = connection.execute(
                    'SELECT task_id,snapshot_revision,statement_revision,registry_revision,payload '
                    'FROM task_snapshots ORDER BY task_id,snapshot_revision',
                ).fetchall()
                actual = [list(row[:4]) + [json.loads(row[4])] for row in rows]
                assert actual == literal(CASES['sql_snapshots'][step['sql_snapshots']]), step['id']


@pytest.mark.parametrize('case', CASES['scenarios'], ids=lambda case: case['id'])
def test_addressed_outcome_and_complete_cascade(tmp_path, case):
    scenario = Scenario(tmp_path)
    for step in case['steps']:
        scenario.execute(step)
