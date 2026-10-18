"""Caller-relative work reads: literal public oracles and complete effect checks."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from apps.sequencing.tests.test_registration import Client, current_state
from apps.sequencing.tests.test_store_migration import quoted, rendered


ROOT = Path(__file__).resolve().parents[3]
DATA = json.loads((ROOT / 'apps/sequencing/fixtures/eligibility-contract.json').read_text())


def arrange(client, case):
    if case['setup'] is None:
        # Existing supported route, checked independently of either new query kind.
        client.check(DATA['empty_prepare'], exit_code=2)
    else:
        client.check(case['setup'])
    with closing(sqlite3.connect(client.database)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for item in case['states']:
            terminal = item['terminal']
            saved = None if terminal is None else json.dumps(terminal, ensure_ascii=False, separators=(',', ':'))
            cursor = db.execute('UPDATE tasks SET state=?,terminal_json=? WHERE task_id=?',
                                (item['state'], saved, item['task_id']))
            assert cursor.rowcount == 1
        for table, rows in case['rows'].items():
            fields = DATA['columns'][table]
            names = ','.join(map(quoted, fields))
            slots = ','.join('?' for _ in fields)
            db.executemany('INSERT INTO ' + quoted(table) + '(' + names + ') VALUES(' + slots + ')',
                           rendered(rows, client.root))
        db.commit()
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    for row in case['rows'].get('workspace_roots', []):
        directory = Path(rendered(row[3], client.root))
        directory.mkdir(parents=True)
        (directory / 'uncommitted.txt').write_bytes(b'Preserved test-owned workspace material')


def marker_state(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('uncommitted.txt')}


@pytest.mark.parametrize('case', DATA['cases'], ids=lambda case: case['name'])
def test_current_eligibility_and_candidate_contract(tmp_path, case):
    client = Client(tmp_path)
    arrange(client, case)
    before = current_state(client.database)
    markers = marker_state(tmp_path)
    for scenario in case['reads']:
        actual = client.invoke(scenario['packet'], exit_code=scenario['exit'])
        if scenario.get('reason_only_shape', False):
            assert set(actual) == {'schema', 'status', 'reason'}
            assert {key: actual[key] for key in ('schema', 'status')} == scenario['expected']
            assert isinstance(actual['reason'], str) and actual['reason'].strip()
        else:
            assert actual == scenario['expected']
        assert current_state(client.database) == before
        assert marker_state(tmp_path) == markers
