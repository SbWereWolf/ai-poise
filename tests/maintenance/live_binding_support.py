"""Observe existing registry, configuration and native preparation owners."""
import hashlib
import json
from pathlib import Path
import tarfile
from dataclasses import replace

from poise.common import configured_root, descendant, load_config
from poise.composition import project_tools
from poise.infrastructure.sqlite.worktree_preparation import SqlitePreparationRepository
from poise.infrastructure.task_paths import task_root
from poise.storage import Store

from live_application_support import capture, write


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owner_context(baseline, parent_project):
    registered = next(row for row in baseline['registered_projects']
                      if row['id'] == parent_project)
    manifest = Path(registered['config'])
    root, config, workflows = load_config(manifest)
    listing = project_tools(Path(config['runtime_resources']['project_setup'])).list()
    assert not listing['errors'], listing
    state = configured_root(root, config['paths']['state'])
    store = Store(descendant(state, config['paths']['database']),
        descendant(state, config['paths']['lock']), config['limits']['lock_seconds'],
        config['limits']['lock_poll_seconds'], workflows, read_only=True,
        read_wait=config['limits']['sqlite_read_seconds'])
    record = store.queries.record(baseline['task'])
    assert record is not None
    with store.database.read_transaction() as connection:
        attempt = SqlitePreparationRepository(connection).latest(baseline['task'])
    assert attempt is not None and attempt.phase == 'completed'
    return {'config': config, 'listing': listing, 'record': record,
        'attempt': attempt, 'task_root': task_root(state, config['paths'],
            baseline['task'], record['sprint_id']), 'worktree': Path(record['worktree'])}


def native_refresh_observation(context, declared):
    actual = context['attempt'].projection()
    assert declared['receipt_path'] == actual['receipt_path']
    assert declared['receipt_sha256'] == actual['receipt_sha256']
    path = Path(actual['receipt_path'])
    assert path.is_relative_to(context['task_root']) and sha(path) == actual['receipt_sha256']
    report = json.loads(path.read_text())
    assert report['schema'] == 'poise/worktree-preparation/v1'
    for key, value in context['attempt'].coordinates.items():
        assert report[key] == value
    assert report['task_id'] == context['record']['id']
    assert report['worktree'] == str(context['worktree'])
    assert report['attempt_id'] == actual['attempt_id'] != declared['previous_attempt_id']
    assert report['status'] == actual['status'] == 'ready'
    configuration = Path(context['config']['worktree_preparation']['configuration'])
    assert report['configuration']['sha256'] == sha(configuration)
    assert Path(declared['previous_receipt_path']).read_bytes() == Path(
        declared['previous_receipt_snapshot']).read_bytes()
    return report


def assert_permanent(paths, context):
    for value in paths:
        path = Path(value).absolute()
        assert not path.is_relative_to(context['task_root']), path
        assert not path.is_relative_to(context['worktree']), path


def working_binding(runtime, application, selected, baseline, context, evidence):
    from apps.bootstrap.infrastructure import CommandConfiguration
    from poise.infrastructure.project_launch import FileProjectLaunch

    registration = next(row for row in context['listing']['projects']
                        if row['project'] == application)
    _, manifest, _ = load_config(Path(registration['config_path']))
    base = Path(manifest['git']['repository'])
    accepted = next(row for row in baseline['registered_projects'] if row['id'] == application)
    assert base == Path(accepted['repository']) == Path(selected['base'])
    app, parameters = Path(selected['config']), Path(selected['parameters'])
    environment = {'PATH': runtime['path'], 'HOME': '/home/sbwerewolf', 'LANG': 'C.UTF-8'}
    paths = [base, app, parameters]
    if application == 'erp':
        entry = ['/bin/bash', str(base / 'start-app')]
        selection = base / '.runtime/environment-consumer/runtime.env'
        result, stdout = capture(['/bin/bash', '-c',
            'source "$1"; printf "%s" "$ERP_ENVIRONMENT_PYTHON_BIN"',
            'working-sdk-selection', str(selection)], cwd=base, env=environment,
            root=evidence, label='working-sdk-selection', timeout=30)
        assert result['actual_exit_code'] == 0
        python = stdout.decode()
        paths += [selection, base / 'start-app']
    else:
        selection = base / 'config/stride.env'
        result, stdout = capture(['/bin/bash', '-c',
            'source "$1"; printf "%s" "$STRIDE_CONFIG"',
            'working-launcher-selection', str(selection)], cwd=base, env=environment,
            root=evidence, label='working-launcher-selection', timeout=30)
        assert result['actual_exit_code'] == 0
        settings_path = Path(stdout.decode())
        owner = FileProjectLaunch()
        launcher = replace(owner.read_settings(str(settings_path)), project_id=application)
        assert launcher.select_project(owner.list_projects(launcher)) == registration['config_path']
        bootstrap_files = set()
        for operation in ('infra', 'deps', 'check', 'run'):
            route = launcher.plan_command([operation])
            assert str(base / 'bootstrap') in route
            assert route[route.index(str(base / 'bootstrap')) + 1] == operation
            bootstrap_file = Path(route[route.index('--config') + 1])
            bootstrap_files.add(bootstrap_file)
            assert route[route.index('--base-dir') + 1] == str(base)
        assert len(bootstrap_files) == 1
        bootstrap_file = bootstrap_files.pop()
        commands = CommandConfiguration.load(bootstrap_file).rendered_commands(
            values={'base_dir': str(base)})
        assert all(row[row.index('--config') + 1] == str(app) and
                   row[row.index('--parameters') + 1] == str(parameters)
                   for row in commands.values())
        python = commands['check'][0]
        assert all(row[0] == python for row in commands.values())
        entry = ['/bin/bash', str(base / 'stride.sh'), '--poise-root', str(base),
                 '--project', application, str(selection)]
        paths += [selection, settings_path, bootstrap_file, base / 'bootstrap', base / 'stride.sh',
                  launcher.poise_root, launcher.data_root]
    assert Path(python).is_absolute()
    probe = ('import importlib.metadata,json,sys,environment_maintenance as sdk; '
             'print(json.dumps({"version":importlib.metadata.version("environment-maintenance"),'
             '"python":sys.executable,"module":sdk.__file__}))')
    result, stdout = capture([python, '-I', '-B', '-c', probe], cwd=base,
        env=environment, root=evidence, label='working-sdk-identity', timeout=30)
    assert result['actual_exit_code'] == 0
    sdk = json.loads(stdout)
    assert sdk['version'] == '2.0.0' and sdk['python'] == python
    assert Path(sdk['module']).is_relative_to(Path(python).parent.parent)
    paths += [python, sdk['module']]
    archives = {path: digest for path, digest in baseline['source_hashes'].items()
                if path.endswith('/environment-maintenance-2.0.0.tgz')}
    assert len(archives) == 1
    for path, digest in archives.items():
        assert sha(path) == digest
        paths.append(path)
        installed_package = Path(python).parents[2]
        installed_module = Path(sdk['module']).parent
        with tarfile.open(path, 'r:gz') as archive:
            assert (installed_package / 'package.json').read_bytes() == archive.extractfile(
                'package/package.json').read()
            for relative in ('cli.py', 'actions/process.py',
                             'checking/application.py', 'preparation/application.py'):
                expected = archive.extractfile(
                    'package/src/environment_maintenance/' + relative).read()
                assert (installed_module / relative).read_bytes() == expected
    assert_permanent(paths, context)
    values = json.loads(parameters.read_text())
    assert values['instance' if application == 'erp' else 'repository'] == str(base)
    operation_files = [app.parent / value for value in json.loads(app.read_text())['operations'].values()]
    assert_permanent(operation_files, context)
    source, stdout = capture([runtime['git'], '-C', str(base), 'rev-parse', 'HEAD'],
        cwd=base, env=environment, root=evidence, label='working-source-identity', timeout=30)
    assert source['actual_exit_code'] == 0
    report = {'application': application, 'repository': str(base), 'entry': entry,
        'source_commit': stdout.decode().strip(), 'sdk': sdk,
        'configuration_sha256': {str(path): sha(path)
            for path in [app, parameters, *operation_files, *map(Path, entry[1:2])]
            if path.is_file()}, 'accepted_archives': archives}
    write(evidence / 'working-binding.json', report)
    return report
