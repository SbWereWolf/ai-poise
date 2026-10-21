"""Regression checks for canonical Task scope and public scalar refusals."""
from copy import deepcopy

import pytest

from store_checks import (
    assert_same,
    fixture,
    installation,
    prepare,
    snapshot,
    stored_records,
    write,
)


def test_contract_snapshot_rejects_foreign_canonical_task(tmp_path):
    module, store, path, _ = installation(tmp_path, source=True)
    prepare(store)
    write(store)
    record = deepcopy(next(record for record in fixture()['records']
                           if record['kind'] == 'contract_snapshot'))
    record['id'] += '-scope-probe'
    record['data']['contract']['task_id'] = '0043'
    before = snapshot(path)

    with pytest.raises(module.ExecutionError):
        write(store, [record], request='foreign-contract', revision=8)

    assert snapshot(path) == before


@pytest.mark.parametrize(
    'field,value',
    [('generation', 2**63), ('revision', 2**63), ('text', '\ud800')],
    ids=['sqlite-generation-bound', 'sqlite-revision-bound', 'utf8-string-bound'],
)
def test_scalar_limits_use_public_refusal_or_exact_supported_value(tmp_path, field, value):
    module, store, path, _ = installation(tmp_path, source=True)
    prepare(store)
    record = deepcopy(fixture()['records'][0])
    if field == 'text':
        record['data']['text'] = value
    else:
        record[field] = value
    before = snapshot(path)

    try:
        write(store, [record], request='scalar-limit', revision=7)
    except module.ExecutionError as error:
        assert str(error)
        assert snapshot(path) == before
    else:
        assert_same(stored_records(path)[-1], record)
