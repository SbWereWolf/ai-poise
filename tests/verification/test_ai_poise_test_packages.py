import json
from pathlib import Path
import shutil

import pytest

from poise.common import PoiseError
from poise.infrastructure.test_packages import AiPoiseTestPackages


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / 'config/testing/test-packages.json'


def test_catalog_enumerates_deterministic_ai_poise_membership():
    packages = AiPoiseTestPackages.load(CATALOG)
    first = packages.enumerate(ROOT)
    second = packages.enumerate(ROOT)
    assert first == second
    assert packages.package_ids == tuple(item['id'] for item in first)
    runner = next(item for item in first if item['id'] == 'verification-runner')
    assert runner['owner'] == 'verification'
    assert 'evidence' in runner['integration_boundaries']
    assert 'src/poise/execution.py' in runner['members']['source']
    assert 'tests/runner/test_registered_check_runner.py' in runner['members']['tests']
    assert 'tests/conftest.py' in runner['members']['fixtures']


def test_membership_is_relative_to_supplied_ai_poise_checkout(tmp_path):
    checkout = tmp_path / 'moved-anywhere'
    shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
    packages = AiPoiseTestPackages.load(CATALOG)
    original = packages.membership(ROOT, 'verification-runner')
    moved = packages.membership(checkout, 'verification-runner')
    assert moved == original
    flattened = [p for paths in moved['members'].values() for p in paths]
    assert all(not Path(p).is_absolute() for p in flattened)
    assert not any(str(checkout) in p for p in flattened)


def test_catalog_refuses_non_ai_poise_checkout(tmp_path):
    checkout = tmp_path / 'other'
    checkout.mkdir()
    (checkout / 'pyproject.toml').write_text('[project]\nname="other"\nversion="1"\n')
    with pytest.raises(PoiseError, match='ai-poise'):
        AiPoiseTestPackages.load(CATALOG).membership(checkout, 'verification-runner')


def test_catalog_rejects_escape_and_stale_patterns(tmp_path):
    raw = json.loads(CATALOG.read_text())
    raw['packages'][0]['members']['source'] = ['../outside.py']
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps(raw))
    with pytest.raises(PoiseError, match='inside'):
        AiPoiseTestPackages.load(bad)

    raw = json.loads(CATALOG.read_text())
    raw['packages'][0]['members']['source'] = ['src/poise/does-not-exist.py']
    bad.write_text(json.dumps(raw))
    packages = AiPoiseTestPackages.load(bad)
    with pytest.raises(PoiseError, match='matched no files'):
        packages.membership(ROOT, 'verification-runner')


def test_runtime_services_check_attempts_tracks_local_bytes(tmp_path):
    from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache

    source = 'src/poise/application/check_attempts.py'
    packages = AiPoiseTestPackages.load(CATALOG)
    assert source in packages.membership(ROOT, 'runtime-services')['members']['source']
    impact = packages.impact(ROOT, [source])
    assert impact['direct_packages'] == ['runtime-services']
    assert impact['unmapped_paths'] == []

    checkout = tmp_path / 'owned-checkout'
    shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(
        '.git', '__pycache__', '.pytest_cache', '.poise-test-cache'))
    local = AiPoiseTestPackageCache(checkout, checkout / 'config/testing/test-packages.json')
    other = AiPoiseTestPackageCache(ROOT, CATALOG)
    before = local.fingerprint('runtime-services')['input_fingerprint']
    other_before = other.fingerprint('runtime-services')['input_fingerprint']
    assert before == other_before
    assert local.cache == checkout / '.poise-test-cache'
    assert other.cache == ROOT / '.poise-test-cache'

    changed = checkout / source
    changed.write_bytes(changed.read_bytes() + b'\n# Cache input regression.\n')
    assert local.fingerprint('runtime-services')['input_fingerprint'] != before
    assert other.fingerprint('runtime-services')['input_fingerprint'] == other_before


def test_duplicate_family_inputs_are_explicitly_owned_by_packages():
    packages = AiPoiseTestPackages.load(CATALOG)
    family_sources = {
        'src/poise/application/duplicate_tasks.py',
        'src/poise/modules/tasks/duplicates.py',
        'src/poise/infrastructure/sqlite/duplicate_tasks.py',
    }
    task_members = packages.membership(ROOT, 'tasks')['members']
    assert family_sources <= set(task_members['source'])
    assert 'tests/tasks/fixtures/duplicate_route.json' in task_members['fixtures']
    runtime_members = packages.membership(ROOT, 'runtime-services')['members']
    assert family_sources | {'src/poise/infrastructure/duplicate_admission.py'} <= set(runtime_members['source'])
    for path in family_sources | {'src/poise/infrastructure/duplicate_admission.py',
                                 'tests/tasks/fixtures/duplicate_route.json'}:
        assert packages.impact(ROOT, [path])['unmapped_paths'] == []


def test_duplicate_reuse_sources_and_test_helpers_are_cache_inputs():
    packages = AiPoiseTestPackages.load(CATALOG)
    sources = {
        'src/poise/application/duplicate_reuse.py',
        'src/poise/infrastructure/duplicate_reuse.py',
        'src/poise/modules/tasks/domain.py',
        'tests/tasks/test_duplicate_creation.py',
    }
    membership = packages.membership(ROOT, 'runtime-services')['members']
    assert sources <= set(membership['source'])
    assert 'tests/runtime_services/test_duplicate_reuse.py' in membership['tests']
    for path in sources:
        assert 'runtime-services' in packages.impact(ROOT, [path])['direct_packages']


def test_restart_repair_sources_are_runtime_cache_inputs():
    packages = AiPoiseTestPackages.load(CATALOG)
    expected = {
        'src/poise/modules/tasks/newborn.py',
        'src/poise/modules/tasks/ports.py',
        'src/poise/infrastructure/repository_tree.py',
    }
    membership = packages.membership(ROOT, 'runtime-services')['members']
    assert expected <= set(membership['source'])
    assert 'tests/runtime_services/test_restart_local_repair.py' in membership['tests']
    for path in expected:
        assert 'runtime-services' in packages.impact(ROOT, [path])['direct_packages']
