"""Versioned Requirements Registry selection policy: literal positive/negative paths."""
from pathlib import Path
import json

from poise.infrastructure.development_routing import load_development_routing

ROOT = Path(__file__).resolve().parents[2]


def route(path):
    router = load_development_routing(
        catalog=ROOT / '.agents/skill-catalog.json',
        selection=ROOT / 'config/development/skill-selection.json',
        policy=ROOT / 'config/development/routing.json',
        packages=ROOT / 'config/testing/test-packages.json')
    request = json.loads((Path(__file__).parent / 'fixtures/general_instructions/policy-route.json').read_text())
    request.update(checkout=str(ROOT), scope_paths=[path])
    return router.route(request)


def test_requirements_registry_paths_select_requirements_db():
    for path in ['src/poise/modules/requirements_registry/domain.py',
                 'src/poise/application/requirements_registry.py',
                 'src/poise/infrastructure/requirements_registry.py',
                 'src/poise/interfaces/requirements_registry.py',
                 'docs/workflows/requirements-registry.md']:
        result = route(path)
        assert 'requirements-db' in result['skills']
        assert result['matched_selection_rules']


def test_unrelated_paths_do_not_select_requirements_db():
    assert 'requirements-db' not in route('src/poise/application/development_routing.py')['skills']
