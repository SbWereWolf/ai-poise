"""GREEN arrangement checks independent of the missing product profile."""
import hashlib
import json
import sys

import pytest
pytest.register_assert_rewrite('erp_support')
from erp_support import FIXTURES, cjm_consumer_inputs, invoke, read_json, snapshot, write_json

pytest_plugins = ['erp_support']


@pytest.mark.parametrize('previous', [False, True], ids=['initial', 'repeat'])
def test_refusal_arrangement_observes_prepared_bytes_and_preserves_prior_state(tmp_path, previous):
    target = tmp_path / 'target.json'
    source = tmp_path / 'prepared.json'
    desired = (FIXTURES / 'erp-json-new.json').read_bytes()
    old = (FIXTURES / 'erp-json-old.json').read_bytes()
    source.write_bytes(desired)
    if previous:
        target.write_bytes(old)
    events = tmp_path / 'events.jsonl'
    result = invoke([sys.executable, '-B', FIXTURES / 'erp_publication_refusal.py',
                     '--target', target, '--events', events, '--', sys.executable, '-B',
                     FIXTURES / 'erp_fixture_publication.py', source, target])
    assert result.returncode != 0 and 'PermissionError' in result.stderr
    assert source.read_bytes() == desired
    observed = [json.loads(line) for line in events.read_text().splitlines()]
    assert len(observed) == 1
    assert set(observed[0]) == {'operation', 'errno', 'prepared_sha256'}
    assert observed[0]['operation'] == 'replace' and observed[0]['errno'] == 13
    assert observed[0]['prepared_sha256'] == hashlib.sha256(desired).hexdigest()
    if previous:
        assert target.read_bytes() == old
    else:
        assert not target.exists()


def test_refusal_arrangement_exposes_remove_old_before_failed_replace(tmp_path):
    target = tmp_path / 'target.json'
    source = tmp_path / 'prepared.json'
    target.write_bytes((FIXTURES / 'erp-json-old.json').read_bytes())
    source.write_bytes((FIXTURES / 'erp-json-new.json').read_bytes())
    events = tmp_path / 'events.jsonl'
    result = invoke([sys.executable, '-B', FIXTURES / 'erp_publication_refusal.py',
                     '--target', target, '--events', events, '--', sys.executable, '-B',
                     FIXTURES / 'erp_fixture_publication.py', source, target, '--remove-old'])
    assert result.returncode != 0 and 'PermissionError' in result.stderr
    assert len(events.read_text().splitlines()) == 1
    assert not target.exists(), 'This is the exact realistic fault the product test must reject'


def test_pinned_cjm_fixture_is_valid_and_semantic_refusal_is_not_setup_failure(erp_case):
    root, relative, selected, command = cjm_consumer_inputs(erp_case)
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(selected.read_bytes())
    before = snapshot(root)
    loaded = invoke(command)
    assert loaded.returncode == 0, (loaded.stdout, loaded.stderr)
    assert json.loads(loaded.stdout) == {'wirePrice': 15, 'setupPrice': 0, 'draftSlabCountLimit': 7}
    assert snapshot(root) == before
    invalid = read_json(selected)
    invalid['businessFixture']['setupPrice'] = -1
    write_json(target, invalid)
    before = snapshot(root)
    refused = invoke(command)
    assert refused.returncode != 0 and 'CJM-CONFIG:' in refused.stderr
    assert 'setupPrice' in refused.stderr and refused.stdout == ''
    assert snapshot(root) == before
