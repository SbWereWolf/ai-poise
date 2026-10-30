"""One shipped project selection, unchanged goal graphs and a generic CLI default."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from poise.common import PoiseError, load_config
from poise.composition import project_tools
from poise.infrastructure.projects import FileProjectSetup, ProjectSettings
from poise_environment.profile import main as profile_main
from .helpers import shipped_setup, shipped_edits


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/common-blueprint'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_shipped_registry_has_one_common_blueprint(tmp_path):
    expected = read(FIXTURES / 'contract.json')
    settings = ProjectSettings(shipped_setup(tmp_path / 'supply'))
    assert sorted(settings.raw['templates']) == expected['template_ids']
    assert sorted(p.name for p in (ROOT / 'config/project-templates').glob('*.json')) == (
        expected['template_file_names']
    )
    blueprint = FileProjectSetup(settings).template({'id': 'common'}).data
    assert blueprint['schema'] == expected['blueprint_schema']
    assert blueprint['version'] == expected['blueprint_version']
    assert sorted(blueprint['workflow_sources']) == expected['goal_types']
    assert sorted(blueprint['config']['workflows']) == expected['goal_types']
    # The checkout bytes are the subject; shipped_setup normalizes its owned copy.
    assert hashlib.sha256((ROOT / 'config/project-templates/common.json').read_bytes()).hexdigest() == (
        expected['blueprint_sha256']
    )


def test_common_creation_copies_all_thirteen_graphs(project, tmp_path):
    expected = read(FIXTURES / 'contract.json')
    tools = tmp_path / 'supply'
    settings_path = shipped_setup(tools)
    settings = ProjectSettings(settings_path)
    # Existing valid input supplies question answers; it never supplies the oracle.
    available = next(iter(settings.raw['templates'].values()))
    blueprint = read(settings.root / available['path'])
    sources = {
        goal: (tools / entry['path']).read_bytes()
        for goal, entry in blueprint['workflow_sources'].items()
    }
    data = tmp_path / 'common-data'
    request = read(FIXTURES / 'project-request.json')
    request['edits'] = shipped_edits(
        blueprint, tools=tools, project='common-case', repository=project['app'],
        data=data, trees=project['app'] / 'task-worktrees',
    )
    owner = project_tools(settings_path)
    result = owner.apply(request)

    assert result['status'] == 'created'
    manifest = Path(result['config_path'])
    _, current, graphs = load_config(manifest)
    assert current['project'] == 'common-case'
    assert current['git']['repository'] == str(project['app'])
    assert current['paths']['state'] == str(data)
    assert sorted(graphs) == expected['goal_types']
    for goal in expected['goal_types']:
        selected = Path(current['workflows'][goal])
        if not selected.is_absolute():
            selected = manifest.parent / selected
        assert read(selected) == json.loads(sources[goal])
        assert (tools / blueprint['workflow_sources'][goal]['path']).read_bytes() == sources[goal]
    before = manifest.read_bytes()
    assert owner.apply(request)['replayed'] is True
    assert manifest.read_bytes() == before
    assert not (data / current['paths']['database']).exists()


def test_profile_default_selects_common(project, tmp_path, capsys):
    source = tmp_path / 'profile-inputs'
    shutil.copytree(ROOT / 'config', source / 'config')
    settings_file = source / 'config/project-setup.example.json'
    settings = read(settings_file)
    available = next(iter(settings['templates'].values()))
    body = (source / available['path'].removeprefix('app/')).read_bytes()
    (source / 'config/project-templates/common.json').write_bytes(body)
    settings['templates'] = {'common': {'path': 'app/config/project-templates/common.json'}}
    settings_file.write_text(json.dumps(settings), encoding='utf-8')
    specification_file = source / 'config/maintenance/profile.example.json'
    specification = read(specification_file)
    specification['templates'] = {'common': 'config/project-templates/common.json'}
    specification_file.write_text(json.dumps(specification), encoding='utf-8')
    output, state = tmp_path / 'profile', tmp_path / 'state'

    status = profile_main([
        '--source', str(source), '--output', str(output), '--state', str(state),
        '--repository', str(project['app']), '--project', 'common-profile',
        '--base-ref', 'main', '--author-name', 'Fixture',
        '--author-email', 'fixture@example.invalid', '--module-path', str(ROOT / 'src'),
    ])
    captured = capsys.readouterr()
    assert status == 0, (captured.out, captured.err)
    assert json.loads(captured.out)['status'] == 'created'
    generated = ProjectSettings(output / 'setup.json')
    assert set(generated.raw['templates']) == {'common'}
    assert generated.raw['templates']['common']['path'] == 'config/project-templates/common.json'
    assert read(output / 'catalog.json')['parameters']['template_id']['default'] == 'common'
    blueprint = FileProjectSetup(generated).template({'id': 'common'}).data
    assert sorted(blueprint['workflow_sources']) == read(FIXTURES / 'contract.json')['goal_types']
    assert all((output / entry['path']).is_file() for entry in blueprint['workflow_sources'].values())
    assert not state.exists()


@pytest.mark.parametrize('identifier', ['linux-reference', 'wsl-poise'])
def test_retired_blueprint_is_not_selectable(tmp_path, identifier):
    owner = FileProjectSetup(ProjectSettings(shipped_setup(tmp_path / 'supply')))
    with pytest.raises(PoiseError, match='Unknown selected project template'):
        owner.template({'id': identifier})
