"""Two actual configured projects and public native-to-Task-store assignments."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess

from poise.application.session_establishment import SessionEstablisher
from poise.composition import project_config_tools, project_tools
from poise.common import digest, load_config
from poise.modules.session_establishment.domain import CallerIdentity
from tests.conftest import seed_fixture_requirements
from tests.runtime_services.test_session_establishment import registry
from .initial_proxy_support import invoke
from .post_tool_proxy_support import PostToolScene
from .pre_tool_proxy_support import captured


def second_project(project, parent):
    repository = parent / 'independent-erp-repository'
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repository)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty',
                    '-qm', 'Initial private ERP fixture'], check=True, capture_output=True)
    setup = project['cfg']['runtime_resources']['project_setup']
    # Use the existing selected test blueprint through its public project owner.
    result = project_tools(setup).apply({
        'schema': 'project-setup-1', 'request_id': 'post-tool-second-project',
        'destination': 'configured/post-tool-erp', 'template': {'id': 'selected'},
        'edits': [{'path': ['project'], 'value': 'post-tool-erp'},
                  {'path': ['git', 'repository'], 'value': str(repository)},
                  {'path': ['git', 'push_required'], 'value': False}],
        'probe_repository': True})
    path = Path(result['config_path'])
    _, cfg, workflows = load_config(path)
    prepared = project_config_tools(setup).apply({
        'schema': 'project-runtime-resources-bootstrap-1',
        'request_id': 'post-tool-erp-resources', 'config_path': str(path),
        'expected_revision': digest({'config': cfg, 'workflows': workflows}),
        'runtime_resources': {'project_setup': str(setup), 'hooks': []},
        'receipt_path': 'operations/post-tool-erp-resources.json'})
    assert prepared['status'] == 'completed'
    _, cfg, _ = load_config(path)
    # Ordinary fixture Requirements preparation, not a Task/assignment SQL writer.
    requirements = seed_fixture_requirements(path.parent, cfg)
    return {'root': path.parent, 'config_path': path, 'cfg': cfg,
            'app': repository, 'workflow': deepcopy(project['workflow']),
            'requirements_registry': requirements}


def assignment_case(project, parent, *, proxy):
    other = second_project(project, parent)
    folder = parent / 'independent-erp-fixture'
    folder.mkdir()
    scenes = [PostToolScene(project, parent), PostToolScene(other, folder)]
    projects = [project, other]
    assert [p['cfg']['project'] for p in projects] == ['demo', 'post-tool-erp']
    assert projects[0]['config_path'] != projects[1]['config_path']
    native = 'same-native-two-projects'
    expected_sessions = ['poise-observed-assignment', 'erp-observed-assignment']
    databases, roots = [], []
    try:
        for index, (selected, scene) in enumerate(zip(projects, scenes)):
            profile = json.loads(scene.services[1].settings.project_config.read_text())
            assert Path(profile['task_adapter']['config_path']) == selected['config_path']
            assert all(service.settings.project_config == scene.services[1].settings.project_config
                       for service in scene.services)
            owner = registry(selected)
            database = owner.database.path
            databases.append(database)
            actual = SessionEstablisher(owner).establish(
                CallerIdentity.native(selected['cfg']['project'], native), [],
                preferred_session=expected_sessions[index])
            assert actual.session_id == expected_sessions[index]
            # A wrong-store lookup must produce a distinct wrong value, not
            # accidentally return the same session or an indistinguishable fallback.
            foreign_project = projects[1-index]['cfg']['project']
            decoy = 'wrong-store-' + str(index)
            wrong = SessionEstablisher(owner).establish(
                CallerIdentity.native(foreign_project, native), [], preferred_session=decoy)
            assert wrong.session_id == decoy
            with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
                rows = connection.execute(
                    'SELECT identity_key,session_id FROM runtime_bindings ORDER BY identity_key').fetchall()
            assert {value for _, value in rows} >= {expected_sessions[index], decoy}
            root = Path(profile['paths']['state']) / profile['paths']['runtime'] / expected_sessions[index]
            roots.append(root)
            scene.bind(session=native)
            # Expected location is derived from explicit fixture configuration
            # and literal assignment, not observed_project_session_id under test.
            scene.capture_roots[native] = root
        assert databases[0] != databases[1] and roots[0] != roots[1]
        for index, scene in enumerate(scenes):
            event = 'PostToolUse' if proxy else 'SessionStart'
            handler = scene.publish(event=event)
            diagnostic = ('project=' + projects[index]['cfg']['project'] +
                          '; assigned=' + expected_sessions[index] + '\n').encode()
            scene.outcome(0, stdout=b'', stderr=diagnostic, code=1)
            raw = scene.tool_raw(session=native) if proxy else scene.raw_event(session=native)
            result = invoke(handler['command'], raw, scene.root, native)
            assert (result.returncode, result.stdout, result.stderr) == (1, b'{}\n', diagnostic)
            matches = captured(scene, b'', diagnostic, 1, native)
            assert matches
            for out, err in matches:
                assert Path(out).is_relative_to(roots[index])
                assert Path(err).is_relative_to(roots[index])
                assert not Path(out).is_relative_to(roots[1-index])
            # The actual record must name the admitted native and assigned IDs.
            records = [json.loads(path.read_text()) for path in roots[index].rglob('result.json')]
            assert any(value.get('session_id') == native and
                       value.get('assigned_session_id') == expected_sessions[index]
                       for value in records)
        for index, scene in enumerate(scenes):
            profile = json.loads(scene.services[1].settings.project_config.read_text())
            base = Path(profile['paths']['state']) / profile['paths']['runtime']
            assert not (base / ('wrong-store-' + str(1-index))).exists()
    finally:
        for scene in scenes:
            scene.stop_owned_metric()
