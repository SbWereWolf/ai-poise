"""The test reporter preserves unexpected outcomes and original diagnostics."""
import json
from types import SimpleNamespace

from tests.skills.record_instruction_red import canonical, run
from tests.skills.instruction_delivery_helpers import fixture


def test_reporter_preserves_unexpected_events():
    reports = fixture('reporter-passed.json')
    first = json.loads(canonical(0, [], reports))
    assert first['exit_code'] == 0
    assert first['reports'] == reports
    for phase, outcome in [('setup', 'failed'), ('teardown', 'failed'), ('call', 'skipped')]:
        changed = [{**reports[0], 'phase': phase, 'outcome': outcome}]
        assert canonical(1, [], changed) != canonical(0, [], reports)
    assert canonical(2, ['sample'], []) != canonical(0, [], [])


def test_reporter_preserves_raw_diagnostics_and_exit(tmp_path, monkeypatch):
    expected_stdout = 'raw timing 1.234s /different/path\nUTF-8: Иван\r\n'
    expected_stderr = 'original stderr 9.876s\n'
    calls = []

    def fake_pytest(argv, plugins):
        calls.append(argv)
        print(expected_stdout, end='')
        import sys
        print(expected_stderr, end='', file=sys.stderr)
        report = SimpleNamespace(failed=True, nodeid='raw-node', outcome='failed',
                                 longreprtext='full original traceback /variable/path')
        plugins[0].pytest_collectreport(report)
        return 2

    monkeypatch.setattr('tests.skills.record_instruction_red.pytest.main', fake_pytest)
    actual, output = run(['raw-node'], tmp_path / 'reported', 'fixture')
    assert actual == 2
    assert len(calls) == 1
    assert calls[0][-3:] == ['--basetemp', str(tmp_path / 'reported/pytest-temp'), 'raw-node']
    assert (tmp_path / 'reported/fixture.pytest.stdout.txt').read_bytes() == expected_stdout.encode()
    assert (tmp_path / 'reported/fixture.pytest.stderr.txt').read_bytes() == expected_stderr.encode()
    event = json.loads((tmp_path / 'reported/fixture.pytest.events.jsonl').read_text())
    assert event['longrepr'] == 'full original traceback /variable/path'
    assert json.loads(output)['exit_code'] == 2
    assert json.loads(output)['collection_errors'] == ['raw-node']
