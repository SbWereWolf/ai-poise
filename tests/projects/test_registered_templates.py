"""Shipped registry selections must be real, exact, owner-validated templates."""
import re
from pathlib import Path

import pytest

from poise.common import PoiseError
from poise.infrastructure.projects import FileProjectSetup, ProjectSettings

ROOT = Path(__file__).resolve().parents[2]


def test_every_registered_template_can_be_loaded_by_its_owner():
    settings = ProjectSettings(ROOT / 'config/project-setup.example.json')
    owner = FileProjectSetup(settings)
    before = settings.path.read_bytes()
    for name, entry in settings.raw['templates'].items():
        owner.template({'id': name, 'version': entry['version'], 'digest': entry['digest']})
    assert settings.path.read_bytes() == before


def test_missing_retired_template_is_not_an_advertised_selection():
    settings = ProjectSettings(ROOT / 'config/project-setup.example.json')
    assert set(settings.raw['templates']) == {'linux-reference', 'wsl-poise'}
    with pytest.raises(PoiseError, match='Unknown selected project template'):
        FileProjectSetup(settings).template({'id': 'wsl-system', 'version': '2', 'digest': '0' * 64})


@pytest.mark.parametrize('name', ['linux-reference', 'wsl-poise'])
def test_registered_template_accepts_multiline_commit_message(name):
    settings = ProjectSettings(ROOT / 'config/project-setup.example.json')
    entry = settings.raw['templates'][name]
    blueprint = FileProjectSetup(settings).template({
        'id': name, 'version': entry['version'], 'digest': entry['digest'],
    })
    pattern = blueprint.data['config']['git']['commit_pattern']
    assert re.fullmatch(pattern, 'Clear result\n\nOperators can read why it matters.')
    assert re.fullmatch(pattern, 'WIP: Preserve current work')
    assert re.fullmatch(pattern, '') is None
