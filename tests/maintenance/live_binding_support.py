"""Observe existing registry, configuration and native preparation owners."""
import hashlib
import json
import os
from pathlib import Path
import tarfile
from dataclasses import replace
from string import Template

from poise.common import configured_root, configured_storage_path, descendant, load_config
from poise.composition import project_tools
from poise.infrastructure.sqlite.worktree_preparation import SqlitePreparationRepository
from poise.infrastructure.task_paths import task_root
from poise.storage import Store

from live_application_support import capture, profile_inputs, write


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
    return {'config': config, 'manifest': manifest, 'state': state,
        'listing': listing, 'record': record,
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
    configuration = context['manifest'].parent / context['config']['worktree_preparation']['configuration']
    assert report['configuration']['sha256'] == sha(configuration)
    assert report['configuration']['path'] == str(configuration)
    preparation_dependencies_are_permanent(context, configuration)
    assert Path(declared['previous_receipt_path']).read_bytes() == Path(
        declared['previous_receipt_snapshot']).read_bytes()
    return report


def assert_permanent(paths, context):
    for value in paths:
        path = Path(os.path.abspath(value))
        assert not path.is_relative_to(context['task_root']), (
            'Persistent dependency belongs to Task: ' + str(path))
        assert not path.is_relative_to(context['worktree']), (
            'Persistent dependency belongs to worktree: ' + str(path))


def preparation_dependencies_are_permanent(context, configuration):
    from poise.infrastructure.worktree_preparation import PreparationConfiguration

    assert_permanent([configuration], context)
    parsed = PreparationConfiguration.from_bytes(Path(configuration).read_bytes(),
        Path(configuration), context['attempt'].coordinates)
    raw = parsed.command.raw
    # Use the standard renderer and only the operator-owned dependencies that
    # the existing preparation contract actually references. Task coordinates,
    # copied command destinations and ignored options remain legitimate inputs.
    commands = [part for argv in raw['commands'].values() for part in argv]
    persistent = [Template(value).safe_substitute(raw['parameters']) for value in commands]
    assert_permanent([value for value in persistent if Path(value).is_absolute()], context)
    selected = parsed.render()
    task_coordinates = {'task_root', 'worktree', 'task_id', 'branch'}
    for declared, rendered in zip(raw['inputs'], selected['inputs'], strict=True):
        if task_coordinates.intersection(Template(declared['source']).get_identifiers()):
            continue  # A new Task supplies its own runtime input, not this Task's persistent file.
        source = Path(rendered['source'])
        if not source.is_absolute():
            source = Path(configuration).parent / source
        assert_permanent([source], context)


def store_paths(manifest_path, config):
    state = configured_root(Path(manifest_path).parent, config['paths']['state'])
    return {
        'task_database': descendant(state, config['paths']['database']),
        'task_lock': descendant(state, config['paths']['lock']),
        'requirements_database': configured_storage_path(state, config['paths']['requirements_database']),
        'requirements_lock': configured_storage_path(state, config['paths']['requirements_lock']),
        'accounting_database': descendant(state, config['accounting']['storage']['database']),
        'accounting_lock': descendant(state, config['accounting']['storage']['lock']),
    }


def existing_runtime_stores(app, values, manifest_path, config):
    expected_state = configured_root(Path(manifest_path).parent, config['paths']['state'])
    assert Path(values['state_root']) == expected_state, 'Selected profile uses a different state root'
    expected = store_paths(manifest_path, config)
    documents = json.loads(Path(app).read_text())
    infra = json.loads((Path(app).parent / documents['operations']['infra']).read_text())
    observed = {}
    for identifier in ('poise-task-database', 'poise-requirements-database', 'poise-telemetry-database'):
        resource = next(row for row in infra['requirements'] if row['id'] == identifier)
        commands = [*resource['commands'], *resource['checks']['presence']['commands']]
        selections = []
        for command in commands:
            argv = [Template(part).substitute(values) for part in command['argv']]
            selected = Path(argv[argv.index('--config') + 1])
            assert Path(argv[argv.index('--state-root') + 1]) == expected_state
            _, candidate, _ = load_config(selected)
            assert candidate['project'] == config['project']
            assert store_paths(selected, candidate) == expected, 'Selected profile uses different working stores'
            selections.append(str(selected))
        observed[identifier] = selections
    missing = [str(expected[name]) for name in
               ('task_database', 'requirements_database', 'accounting_database')
               if not expected[name].is_file()]
    assert not missing, {'missing_actual_working_databases': missing}
    return {'state_root': str(expected_state),
        'stores': {name: str(path) for name, path in expected.items()},
        'configured_resource_manifests': observed}


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
    # ERP receives profile inputs explicitly on each invocation. Poise's saved
    # Bootstrap selection references its profile after this Task is retired.
    paths = [base] if application == 'erp' else [base, app, parameters]
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
    target = Path(selected['execution_base']) if application == 'erp' else base
    assert values['instance' if application == 'erp' else 'repository'] == str(target), (
        'Invocation instance differs from selected execution target')
    runtime_stores = (existing_runtime_stores(app, values, Path(registration['config_path']), manifest)
                      if application == 'poise-op' else None)
    operation_files = [app.parent / value for value in json.loads(app.read_text())['operations'].values()]
    if application == 'poise-op':
        assert_permanent(operation_files, context)
    source, stdout = capture([runtime['git'], '-C', str(base), 'rev-parse', 'HEAD'],
        cwd=base, env=environment, root=evidence, label='working-source-identity', timeout=30)
    assert source['actual_exit_code'] == 0
    target_source, target_stdout = capture(
        [runtime['git'], '-C', str(target), 'rev-parse', 'HEAD'], cwd=target,
        env=environment, root=evidence, label='execution-source-identity', timeout=30)
    assert target_source['actual_exit_code'] == 0
    report = {'application': application, 'repository': str(base), 'entry': entry,
        'source_commit': stdout.decode().strip(), 'sdk': sdk,
        'execution_base': str(target), 'execution_source_commit': target_stdout.decode().strip(),
        'runtime_stores': runtime_stores,
        'configuration_sha256': {str(path): sha(path)
            for path in [app, parameters, *operation_files, *map(Path, entry[1:2])]
            if path.is_file()}, 'accepted_archives': archives}
    write(evidence / 'working-binding.json', report)
    return report


def selected_instance_observation(runtime, selected, context, evidence, binding):
    """Link this Task's ERP scenario to its real public-owner V04 deployment.

    This attests the selected verification instance; it does not require every
    valid ERP invocation to have been created by Poise.
    """
    proof_path = Path(selected['instance_proof'])
    assert proof_path.is_relative_to(context['task_root'])
    proof = json.loads(proof_path.read_text())
    assert proof['application'] == 'erp'
    setup = Path(proof['isolated_config']['runtime_resources']['project_setup'])
    assert setup.is_relative_to(context['task_root'])
    listing = project_tools(setup).list()
    assert not listing['errors'], listing
    registration = next(row for row in listing['projects'] if row['project'] == 'erp')
    supplied = proof['second_preparation']
    supplied_report = json.loads(Path(supplied['receipt_path']).read_text())
    owner = owner_context({'task': supplied_report['task_id'], 'registered_projects': [
        {'id': 'erp', 'config': registration['config_path']}]}, 'erp')
    assert owner['task_root'].is_relative_to(context['task_root'])
    actual = owner['attempt'].projection()
    assert actual == supplied and actual['status'] == 'ready'
    assert sha(actual['receipt_path']) == actual['receipt_sha256']
    for key, value in owner['attempt'].coordinates.items():
        assert supplied_report[key] == value
    target = Path(selected['execution_base'])
    assert owner['worktree'] == target == Path(proof['worktree'])
    app, parameters = profile_inputs(supplied_report)
    assert app == Path(selected['config']) and parameters == Path(selected['parameters'])
    assert binding['execution_source_commit'] == proof['source_commit']
    primary = next(row for row in context['listing']['projects'] if row['project'] == 'erp')
    _, configuration, _ = load_config(Path(primary['config_path']))
    preparation = Path(primary['config_path']).parent / configuration['worktree_preparation']['configuration']
    assert sha(preparation) == supplied_report['configuration']['sha256']
    result, stdout = capture([runtime['git'], '-C', str(target), 'remote', 'get-url', 'origin'],
        cwd=target, env={'PATH': runtime['path'], 'LANG': 'C.UTF-8'},
        root=evidence, label='selected-instance-origin', timeout=30)
    assert result['actual_exit_code'] == 0 and stdout.decode().strip() == selected['base']
    report = {'caller': selected['base'], 'execution_base': str(target),
        'source_commit': proof['source_commit'], 'proof_path': str(proof_path),
        'proof_sha256': sha(proof_path), 'native_preparation': actual,
        'registration': registration['config_path'],
        'input_sha256': {str(path): sha(path) for path in (app, parameters)}}
    write(evidence / 'selected-instance.json', report)
    return report
