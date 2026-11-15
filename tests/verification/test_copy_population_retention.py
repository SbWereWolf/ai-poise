"""Regression subject: the two existing portability scenarios, on tiny data."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import pytest

from verification.disk_retention_support import REPOSITORY, tiny_checkout


def load_subject(monkeypatch, root, catalog):
    path = REPOSITORY / 'tests/verification/test_ai_poise_test_packages.py'
    spec = importlib.util.spec_from_file_location('retention_portability_subject', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(module, 'CATALOG', catalog)
    return module


def assert_admitted_population(destination, package):
    # Literal paths, independent of membership and the subject's materializer.
    source = ('src/pkg/value.py' if package == 'verification-runner'
              else 'src/poise/application/check_attempts.py')
    expected = {'pyproject.toml', 'config/testing/test-packages.json', source,
                'tests/test_value.py', 'fixtures/data.json'}
    actual = {p.relative_to(destination).as_posix()
              for p in destination.rglob('*') if p.is_file()}
    assert actual == expected, 'copy population includes generated or unrelated data'
    assert (destination / 'fixtures/data.json').read_bytes() == b'{"value":1}\n'


@pytest.mark.parametrize(('scenario', 'destination', 'package'), [
    ('test_membership_is_relative_to_supplied_ai_poise_checkout',
     'moved-anywhere', 'verification-runner'),
    ('test_runtime_services_check_attempts_tracks_local_bytes',
     'owned-checkout', 'runtime-services'),
], ids=['membership', 'fingerprint'])
def test_existing_copy_scenarios_admit_only_required_files(
        tmp_path, monkeypatch, scenario, destination, package):
    root, catalog = tiny_checkout(tmp_path)
    subject = load_subject(monkeypatch, root, catalog)
    output = tmp_path / 'output'
    output.mkdir()
    getattr(subject, scenario)(output)
    assert_admitted_population(output / destination, package)
    assert (root / 'src/poise/application/check_attempts.py').read_bytes() == b'ATTEMPTS = 1\n'


def test_descendant_destination_does_not_enter_its_source_population(tmp_path, monkeypatch):
    root, catalog = tiny_checkout(tmp_path)
    subject = load_subject(monkeypatch, root, catalog)
    output = root / '.poise-test-cache/evidence/fixture/pytest/case'
    output.mkdir(parents=True)
    original = shutil.copytree

    def bounded_copy(source, destination, *args, **kwargs):
        # Abort the known unsafe operation before any recursion or disk growth.
        assert not (Path(source) == root and Path(destination).is_relative_to(root)), (
            'whole-root copy would include its descendant destination')
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(shutil, 'copytree', bounded_copy)
    subject.test_membership_is_relative_to_supplied_ai_poise_checkout(output)
    assert_admitted_population(output / 'moved-anywhere', 'verification-runner')


def test_copy_oracle_rejects_generated_state_and_control_is_restored(tmp_path):
    root, _ = tiny_checkout(tmp_path)
    destination = tmp_path / 'control'
    destination.mkdir()
    for relative in ('pyproject.toml', 'config/testing/test-packages.json',
                     'src/pkg/value.py', 'tests/test_value.py', 'fixtures/data.json'):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
    assert_admitted_population(destination, 'verification-runner')
    wrong = destination / '.poise-test-cache/canary'
    wrong.parent.mkdir()
    wrong.write_bytes(b'wrong population')
    with pytest.raises(AssertionError, match='copy population'):
        assert_admitted_population(destination, 'verification-runner')
    wrong.unlink()
    assert_admitted_population(destination, 'verification-runner')
