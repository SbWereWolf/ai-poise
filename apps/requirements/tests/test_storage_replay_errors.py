"""Operation-local replay errors through the actual Requirements provider."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest
import test_storage_consumer as helpers

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/storage-replay-errors.json').read_text())


@pytest.fixture
def prepared():
    case = helpers.ConsumerStorageTests('test_valid_public_query_control')
    case.setUp()
    try:
        case.seed_model()
        with closing(sqlite3.connect(case.database)) as connection:
            connection.execute(
                'INSERT INTO requirement_requests(protocol_namespace,request_id,intent,response) VALUES(?,?,?,?)',
                ('registry.apply', CASES['request']['request_id'],
                 json.dumps(CASES['request']), json.dumps(CASES['saved_response'])),
            )
            connection.commit()
        yield case
    finally:
        case.doCleanups()


@pytest.mark.parametrize('invalid', CASES['invalid_cases'])
def test_unusable_saved_replay_returns_descriptive_error(prepared, invalid):
    # The column is selected by the literal test fixture, never external input.
    assert invalid['column'] in ('intent', 'response')
    with closing(sqlite3.connect(prepared.database)) as connection:
        connection.execute(
            f"UPDATE requirement_requests SET {invalid['column']}=? WHERE protocol_namespace=? AND request_id=?",
            (invalid['value'], 'registry.apply', CASES['request']['request_id']),
        )
        connection.commit()
    before = prepared.model_state()
    process = prepared.invoke({'operation': 'apply', 'input': CASES['request']})
    reason = prepared.rejected(process)
    assert reason.strip()
    assert prepared.model_state() == before


def test_valid_saved_replay_preserves_data(prepared):
    before = prepared.model_state()
    process = prepared.invoke({'operation': 'apply', 'input': CASES['request']})
    prepared.successful(process, CASES['replayed_response'])
    assert prepared.model_state() == before
