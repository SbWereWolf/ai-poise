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
