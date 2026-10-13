"""Narrow regressions for the two T025 inspection findings, not a release suite."""
from importlib import util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = util.spec_from_file_location(name, path)
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


@pytest.mark.parametrize('method', ['rename', 'replace'])
def test_unknown_receiver_cannot_hide_foreign_destination(method):
    fixtures = load('boundary_test_fixtures', ROOT / 'tests/delivery/test_application_boundaries.py')
    with fixtures.checkout() as checkout:
        source = fixtures.text('storage-preamble.py') + f'\ndef move(source):\n    source.{method}(foreign)\n'
        path = fixtures.probe(checkout, source=source)
        before = fixtures.snapshot(checkout)
        proc, report = fixtures.run(checkout)
        fixtures.rejected(proc, report, rule='foreign_storage', path=path.relative_to(checkout).as_posix())
        assert any(row['target_owner'] == 'sequencing' and row['access'] == 'write'
                   for row in report['accesses'])
        assert any(row['file'] == path.relative_to(checkout).as_posix()
                   for row in report['unresolved_accesses'])
        assert fixtures.snapshot(checkout) == before


def test_string_replace_is_not_a_filesystem_rename():
    fixtures = load('boundary_string_fixtures', ROOT / 'tests/delivery/test_application_boundaries.py')
    with fixtures.checkout() as checkout:
        fixtures.probe(checkout, source=fixtures.text('storage-preamble.py') +
                       '\ntext = str(foreign).replace("target", "other")\n')
        proc, report = fixtures.run(checkout)
        fixtures.passed(proc, report)
        assert not any(row['file'].endswith('boundary_probe.py') and row['sink'] == 'Path.replace'
                       for row in report['accesses'])


def test_stability_phase_never_uses_unbounded_path_read(tmp_path):
    tool = load('boundary_stability_under_test', ROOT / 'tools/check_application_boundaries.py')

    class GuardPath(type(tmp_path)):
        def read_bytes(self):
            raise AssertionError('stability check attempted an unbounded read_bytes')

    source = GuardPath(tmp_path / 'sample.py')
    policy = GuardPath(tmp_path / 'policy.json')
    source.write_bytes(b'original')
    policy.write_bytes(b'{}')
    result = tool.report()
    analyzer = tool.Analyzer(tmp_path, policy, result)
    analyzer.raw = {source: b'original'}
    analyzer.policy_raw = b'{}'
    # Simulate growth between initial observation and the stability phase.
    source.write_bytes(b'original' + b'x' * 4096)
    assert analyzer.finish() == 1
    assert result['stable_inputs'] is False
    assert result['coverage']['complete'] is False
    assert any(d['rule'] == 'changed_input' for d in result['diagnostics'])


@pytest.mark.parametrize('definition', [
    'value: package.Poise',
    'def use(value: package.Poise):\n    pass',
    'class Consumer(package.Poise):\n    pass',
])
def test_explicit_runtime_export_in_definition_operand(definition):
    fixtures = load('boundary_definition_fixtures', ROOT / 'tests/delivery/test_application_boundaries.py')
    with fixtures.checkout() as checkout:
        path = fixtures.probe(checkout, source='import poise as package\n' + definition + '\n')
        proc, report = fixtures.run(checkout)
        fixtures.rejected(proc, report, rule='foreign_import', kind='violation',
                          path=path.relative_to(checkout).as_posix())
