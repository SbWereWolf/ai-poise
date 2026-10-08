"""Transparent test arrangement, never an archiver or a production oracle."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from batch.helpers import bootstrap, request, result, verify
from conftest import WorkPoise, add_test, git, write_json
from poise.application.work import WorkTools

from .helpers import destination, export, handoff_args, settings


FIXTURES = Path(__file__).parent / 'fixtures'


def tiny_repository(path):
    path.mkdir()
    git(path, 'init', '-b', 'main')
    git(path, 'config', 'user.name', 'Recovery fixture')
    git(path, 'config', 'user.email', 'recovery@example.invalid')
    (path / 'version').write_text('installed current system\n')
    git(path, 'add', '.')
    git(path, 'commit', '-m', 'Installed system fixture')


def policy(project, recovery_tool):
    """Bind test-owned schema inputs; expectations never use this function."""
    root = project['root']
    system = root / 'installed-poise'
    tiny_repository(system)
    configs = json.loads((FIXTURES / 'compact-system-configs.json').read_text())
    paths = {name: write_json(root / (name + '.json'), value)
             for name, value in configs.items()}
    replacements = {
        '@NODE@': recovery_tool[0], '@WORKSPACE_RECOVER@': recovery_tool[1],
        '@POISE_REPOSITORY@': str(system),
        '@POISE_CONFIG@': str(project['config_path']),
        '@POISE_OVERLAY@': str(paths['poise_overlay']),
        '@PROJECT_REPOSITORY@': str(project['app']),
        '@PROJECT_CONFIG@': str(paths['project_config']),
        '@PROJECT_OVERLAY@': str(paths['project_overlay']),
    }
    def bind(value):
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        return replacements.get(value, value) if isinstance(value, str) else value
    raw = json.loads((FIXTURES / 'compact-recovery-policy.json').read_text())
    return bind(raw)


def configure(project, recovery_tool):
    transfer = settings()
    del transfer['bundles_directory']
    transfer['archive'] = 'work.tar.gz'
    transfer['recovery'] = policy(project, recovery_tool)
    project['cfg']['runtime_services']['transfer'] = transfer
    write_json(project['config_path'], project['cfg'])
    return transfer['recovery']


def legacy_prepared(project):
    project['cfg']['runtime_services']['transfer'] = settings()
    write_json(project['config_path'], project['cfg'])
    runtime = WorkPoise(project['config_path'], 'source')
    tools = WorkTools(runtime)
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    return runtime, tools, context, result(context, 'Preserved legacy denial input')


def prepared(project, recovery_tool, *, recovery_changes=None):
    recovery = configure(project, recovery_tool)
    if recovery_changes is not None:
        recovery.update(deepcopy(recovery_changes))
        write_json(project['config_path'], project['cfg'])
    runtime = WorkPoise(project['config_path'], 'source')
    tools = WorkTools(runtime)
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    return runtime, tools, context, result(context, 'Preserved task state')


def target(project, path, *, mapping=None, components=None):
    dst = destination(project, path)
    recovery = dst['cfg']['runtime_services']['transfer']['recovery']
    recovery['systems']['project']['repository'] = str(dst['app'])
    for owner, spec in recovery['systems'].items():
        copied = []
        for index, original in enumerate(spec['configs']):
            if owner == 'poise' and index == 0:
                copied.append(str(dst['config_path']))
            else:
                current = dst['root'] / Path(original).name
                current.write_bytes(Path(original).read_bytes())
                copied.append(str(current))
        spec['configs'] = copied
    if mapping is not None:
        recovery['mapping'] = deepcopy(mapping)
    if components is not None:
        recovery['component_steps'] = deepcopy(components)
    write_json(dst['config_path'], dst['cfg'])
    return dst, WorkTools(WorkPoise(dst['config_path'], 'receiver'))


def save(tools, payload=None, *, request_id='compact-export'):
    return export(tools, request_id=request_id, handoff=handoff_args(payload))


def registered_note(tools):
    made = tools.invoke(request('artifacts', {'items': [{
        'scope': 'task', 'path': 'cache',
        'source': {'kind': 'text', 'text': 'Necessary task material\n'},
    }]}))
    return Path(made['artifact_paths'][0])


def flow(tool, manifest, session):
    process = subprocess.run(
        [*tool, 'flow', 'run', '--manifest', str(manifest), '--session', str(session)],
        capture_output=True, text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads((session / 'state.json').read_text())
