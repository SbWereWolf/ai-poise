"""Permanent regressions for the reviewer's two reproduced boundary defects."""
from copy import deepcopy
import json
import sqlite3

import pytest

from test_graph import Environment, FIXTURES, PoiseError, QUERIES


CASES = json.loads((FIXTURES / 'graph-boundary-errors.json').read_text())


def assert_cli_rejection(process):
    assert process.returncode == 2, (process.stdout, process.stderr)
    assert process.stderr == ''
    body = json.loads(process.stdout)
    assert set(body) == {'status', 'reason'}
    assert body['status'] == 'rejected'
    assert isinstance(body['reason'], str) and body['reason']


@pytest.mark.parametrize('mixed', [False, True], ids=['ordinary', 'typed-mixed'])
@pytest.mark.parametrize('kind', [[], {}], ids=['array', 'object'])
@pytest.mark.parametrize('cli', [False, True], ids=['public', 'cli'])
def test_non_string_query_kind_rejects_without_effects(tmp_path, kind, mixed, cli):
    environment = Environment(tmp_path, seed=False)
    before = environment.state()
    queries = deepcopy(CASES['mixed_prefix']) if mixed else []
    queries.append({'id': 'q', 'kind': kind})
    if cli:
        assert_cli_rejection(environment.shell(queries))
    else:
        with pytest.raises(PoiseError):
            environment.commands.query(queries)
    assert environment.state() == before
    environment.reopen()
    assert environment.state() == before


@pytest.mark.parametrize('case', CASES['selected_json'], ids=lambda case: case['id'])
@pytest.mark.parametrize('cli', [False, True], ids=['public', 'cli'])
def test_selected_unreadable_utf8_is_an_operation_error(tmp_path, case, cli):
    environment = Environment(tmp_path)
    with sqlite3.connect(environment.database) as database:
        database.execute(case['sql'], (bytes.fromhex(case['bytes_hex']), *case['parameters']))
    before = environment.state()
    control = QUERIES['control']
    assert environment.commands.query(control['queries']) == control['response']
    if cli:
        assert_cli_rejection(environment.shell([case['query']]))
    else:
        with pytest.raises(PoiseError):
            environment.commands.query([case['query']])
    assert environment.state() == before
    environment.reopen()
    assert environment.commands.query(control['queries']) == control['response']
    assert environment.state() == before
