"""Shipped registry selections must be real, exact, owner-validated templates."""
import re

import pytest

from poise.common import PoiseError
from poise.infrastructure.projects import FileProjectSetup, ProjectSettings

from .helpers import shipped_setup


def test_every_registered_template_can_be_loaded_by_its_owner(tmp_path):
    settings = ProjectSettings(shipped_setup(tmp_path / 'supply'))
    owner = FileProjectSetup(settings)
    before = settings.path.read_bytes()
    for name, entry in settings.raw['templates'].items():
        owner.template({'id': name})
    assert settings.path.read_bytes() == before


def test_missing_retired_template_is_not_an_advertised_selection(tmp_path):
    settings = ProjectSettings(shipped_setup(tmp_path / 'supply'))
    assert set(settings.raw['templates']) == {'common'}
    with pytest.raises(PoiseError, match='Unknown selected project template'):
        FileProjectSetup(settings).template({'id': 'wsl-system'})


@pytest.mark.parametrize('name', ['common'])
def test_registered_template_accepts_multiline_commit_message(tmp_path, name):
    settings = ProjectSettings(shipped_setup(tmp_path / 'supply'))
    entry = settings.raw['templates'][name]
    blueprint = FileProjectSetup(settings).template({
        'id': name,
    })
    pattern = blueprint.data['config']['git']['commit_pattern']
    assert re.fullmatch(pattern, 'Clear result\n\nOperators can read why it matters.')
    assert re.fullmatch(pattern, 'WIP: Preserve current work')
    assert re.fullmatch(pattern, '') is None
