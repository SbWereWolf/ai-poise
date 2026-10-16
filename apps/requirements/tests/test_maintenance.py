"""Existing maintenance and new-meaning scenarios through the standalone CLI."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
SYSTEM = {
    'id': 'SYS-EXPORT', 'level': 'system', 'status': 'current',
    'text': 'Пользователь получает отчёт с сохранёнными значениями.',
}
MAINTENANCE = {
    'id': 'APP-EXPORT-MAINTENANCE', 'level': 'application', 'status': 'current',
    'text': 'Основание обслуживания: восстановить сохранённые значения отчёта.',
}
NEW_SYSTEM = {
    'id': 'SYS-EXPORT-FILTER', 'level': 'system', 'status': 'current',
    'text': 'Пользователь выбирает период отчёта.',
}
NEW_APPLICATION = {
    'id': 'APP-EXPORT-FILTER', 'level': 'application', 'status': 'current',
    'text': 'Приложение формирует отчёт за выбранный период.',
}
MAINTENANCE_ENTRY = {
    'key': 'repair', 'status': 'current',
    'text': 'Восстановить значения отчёта.',
    'applications': ['APP-EXPORT-MAINTENANCE'],
}


class Scenario:
    def __init__(self, root):
        config_dir = root / 'config'
        config_dir.mkdir()
        self.config = config_dir / 'requirements.json'
        self.database = root / 'state/requirements.sqlite'
        settings = json.loads(
            (ROOT / 'apps/requirements/fixtures/requirements-config.json').read_text()
        )
        self.config.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
        self.query([{'id': 'empty', 'kind': 'coverage'}], 0, [{
            'id': 'empty', 'value': {
                'application_without_system': [], 'system_without_application': [],
                'application_without_task': [],
            },
        }])
        # Known Task is a declared input; this test does not implement allocation.
        with sqlite3.connect(self.database) as db:
            db.execute(
                'INSERT INTO task_identities VALUES(?,?,?)',
                ('000041', '{"type":"test-fixture"}', 1),
            )
            db.execute(
                'INSERT INTO task_statements VALUES(?,?,?,?,?)',
                ('000041', 1, 'Восстановить отчёт.', '[]', '{"type":"test-fixture"}'),
            )
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
            assert db.execute(
                'SELECT task_id,current_statement_revision FROM task_identities'
            ).fetchall() == [('000041', 1)]
        self.apply('existing-behavior', 0, [{'kind': 'put_requirement', 'requirement': SYSTEM}])

    def cli(self, operation, body):
        completed = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'apps/requirements/bin/requirements'),
             '--config', str(self.config)],
            input=json.dumps({'operation': operation, 'input': body}, ensure_ascii=False),
            text=True, capture_output=True, cwd=self.config.parent.parent, check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert completed.stderr == ''
        return json.loads(completed.stdout)

    def apply(self, request_id, revision, operations):
        assert self.cli('apply', {
            'request_id': request_id, 'expected_revision': revision, 'operations': operations,
        }) == {'status': 'applied', 'revision': revision + 1, 'replayed': False}

    def query(self, queries, revision, results):
        assert self.cli('query', {'queries': queries}) == {
            'status': 'read_only', 'revision': revision, 'results': results,
        }

    def maintenance(self):
        self.apply('maintenance', 1, [
            {'kind': 'put_requirement', 'requirement': MAINTENANCE},
            {'kind': 'link', 'system': 'SYS-EXPORT', 'application': 'APP-EXPORT-MAINTENANCE'},
            {'kind': 'revise_task', 'task_id': '000041', 'expected_statement_revision': 1,
             'statement': {'goal': 'Восстановить отчёт.', 'definition_of_done': [],
                           'task_requirements': [deepcopy(MAINTENANCE_ENTRY)]}},
        ])

    def old_basis(self):
        with sqlite3.connect(self.database) as db:
            assert db.execute(
                'SELECT r.id,r.level,r.current_revision,v.text FROM requirements r '
                'JOIN requirement_revisions v ON v.id=r.id AND v.revision=r.current_revision '
                'WHERE r.id=?', ('APP-EXPORT-MAINTENANCE',),
            ).fetchall() == [(
                'APP-EXPORT-MAINTENANCE', 'application', 1,
                'Основание обслуживания: восстановить сохранённые значения отчёта.',
            )]
            assert db.execute(
                'SELECT system_id,application_id,valid_from,valid_to '
                'FROM system_application_versions WHERE application_id=?',
                ('APP-EXPORT-MAINTENANCE',),
            ).fetchall() == [('SYS-EXPORT', 'APP-EXPORT-MAINTENANCE', 2, None)]
            assert db.execute(
                'SELECT revision FROM requirement_revisions WHERE id=? ORDER BY revision',
                ('APP-EXPORT-MAINTENANCE',),
            ).fetchall() == [(1,)]


def test_maintenance_uses_existing_system_and_known_task(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.maintenance()
    scenario.query([
        {'id': 'basis', 'kind': 'chain', 'requirement_id': 'APP-EXPORT-MAINTENANCE'},
        {'id': 'task', 'kind': 'task_chain', 'task_id': '000041',
         'key': 'repair', 'statement_revision': 2},
        {'id': 'systems', 'kind': 'search_requirements',
         'filter': {'level': 'system'}, 'limit': 10, 'after_id': None},
    ], 2, [
        {'id': 'basis', 'value': {'application': MAINTENANCE, 'systems': [SYSTEM]}},
        {'id': 'task', 'value': {
            'task': {'task_id': '000041', 'statement_revision': 2, **MAINTENANCE_ENTRY},
            'applications': [MAINTENANCE],
        }},
        {'id': 'systems', 'value': {'rows': [SYSTEM], 'next': None}},
    ])
    scenario.old_basis()


def test_new_meaning_preserves_maintenance_identity_text_and_links(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.maintenance()
    scenario.old_basis()
    new_entry = {
        'key': 'period', 'status': 'current',
        'text': 'Добавить выбор периода отчёта.', 'applications': ['APP-EXPORT-FILTER'],
    }
    scenario.apply('new-meaning', 2, [
        {'kind': 'put_requirement', 'requirement': NEW_SYSTEM},
        {'kind': 'put_requirement', 'requirement': NEW_APPLICATION},
        {'kind': 'link', 'system': 'SYS-EXPORT-FILTER', 'application': 'APP-EXPORT-FILTER'},
        {'kind': 'revise_task', 'task_id': '000041', 'expected_statement_revision': 2,
         'statement': {'goal': 'Восстановить отчёт.', 'definition_of_done': [],
                       'task_requirements': [deepcopy(MAINTENANCE_ENTRY), new_entry]}},
    ])
    scenario.query([
        {'id': 'old', 'kind': 'chain', 'requirement_id': 'APP-EXPORT-MAINTENANCE'},
        {'id': 'new', 'kind': 'chain', 'requirement_id': 'APP-EXPORT-FILTER'},
        {'id': 'old-task', 'kind': 'task_chain', 'task_id': '000041',
         'key': 'repair', 'statement_revision': 3},
        {'id': 'new-task', 'kind': 'task_chain', 'task_id': '000041',
         'key': 'period', 'statement_revision': 3},
    ], 3, [
        {'id': 'old', 'value': {'application': MAINTENANCE, 'systems': [SYSTEM]}},
        {'id': 'new', 'value': {'application': NEW_APPLICATION, 'systems': [NEW_SYSTEM]}},
        {'id': 'old-task', 'value': {
            'task': {'task_id': '000041', 'statement_revision': 3, **MAINTENANCE_ENTRY},
            'applications': [MAINTENANCE],
        }},
        {'id': 'new-task', 'value': {
            'task': {'task_id': '000041', 'statement_revision': 3, **new_entry},
            'applications': [NEW_APPLICATION],
        }},
    ])
    scenario.old_basis()
