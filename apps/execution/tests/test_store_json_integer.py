"""One real-store scalar encoding boundary; no production changes."""
from copy import deepcopy

from store_checks import assert_same, fixture, installation, prepare, snapshot, stored_records, write


def test_json_integer_uses_public_refusal_or_exact_supported_value(tmp_path):
    module, store, database, _ = installation(tmp_path, source=True)
    prepare(store)
    record = deepcopy(fixture()['records'][0])
    record['data']['large_json_integer'] = 10**4300
    before = snapshot(database)
    try:
        write(store, [record], request='json-integer-bound', revision=7)
    except module.ExecutionError as error:
        assert str(error)
        assert snapshot(database) == before
    else:
        assert_same(stored_records(database)[-1], record)
