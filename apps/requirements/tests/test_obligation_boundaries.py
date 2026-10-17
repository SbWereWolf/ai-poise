"""External Requirements CLI with literal responses and independent SQL state."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'apps/requirements/fixtures'
STATE = json.loads((FIXTURES / 'graph-state.json').read_text(encoding='utf-8'))
CASES = json.loads((FIXTURES / 'obligation-boundaries.json').read_text(encoding='utf-8'))
TASK_TABLES = (
    'task_identities',
    'task_statements',
    'task_requirement_keys',
    'task_requirement_versions',
    'application_task_links',
    'task_snapshots',
    'task_creation_requests',
)


class Scenario:
    def __init__(self, root):
        self.root = root
        self.database = root / 'state/requirements.sqlite'
        self.database.parent.mkdir()
        config_dir = root / 'config'
        config_dir.mkdir()
        self.config = config_dir / 'requirements.json'
        self.config.write_text((FIXTURES / 'requirements-config.json').read_text(), encoding='utf-8')
        with sqlite3.connect(self.database) as db:
            db.executescript((FIXTURES / 'storage-schema.sql').read_text())
            for table, data in STATE['tables'].items():
                # Identifiers come only from the independent, test-owned schema fixture.
                columns = ','.join('"' + column + '"' for column in data['columns'])
                parameters = ','.join('?' for _ in data['columns'])
                db.executemany(f'INSERT INTO "{table}"({columns}) VALUES({parameters})', data['rows'])
        self.baseline = self.task_rows('000007')
        self.other = self.task_rows('000008')

    def task_rows(self, task_id):
        result = {}
        with sqlite3.connect(self.database) as db:
            for table in TASK_TABLES:
                columns = ','.join('"' + column + '"' for column in STATE['tables'][table]['columns'])
                rows = db.execute(
                    f'SELECT {columns} FROM "{table}" WHERE task_id=? ORDER BY {columns}',
                    (task_id,),
                ).fetchall()
                result[table] = rows
        return result

    def cli(self, packet):
        process = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'apps/requirements/bin/requirements'),
             '--config', str(self.config)],
            input=json.dumps(packet, ensure_ascii=False), text=True, capture_output=True,
            cwd=self.root, timeout=30, check=False,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert process.stderr == ''
        return json.loads(process.stdout)

    def check(self, steps):
        for step in steps:
            assert self.cli(step['packet']) == step['response']
            if step.get('preserve_task'):
                assert self.task_rows('000007') == self.baseline
            if step.get('preserve_other'):
                assert self.task_rows('000008') == self.other
            for observation in step.get('sql', []):
                with sqlite3.connect(self.database) as db:
                    rows = db.execute(observation['query'], observation['parameters']).fetchall()
                assert [list(row) for row in rows] == observation['rows']


def test_explicit_current_addition_preserves_old_key_text_and_snapshot(tmp_path):
    Scenario(tmp_path).check(CASES['current_addition'])


def test_future_record_changes_task_only_after_explicit_inclusion(tmp_path):
    Scenario(tmp_path).check(CASES['future_then_include'])


def test_empty_future_basis_is_a_typed_gap(tmp_path):
    Scenario(tmp_path).check(CASES['projected_gap'])


def test_historical_task_links_use_live_upper_values_and_saved_snapshot(tmp_path):
    Scenario(tmp_path).check(CASES['historical_versus_live'])
