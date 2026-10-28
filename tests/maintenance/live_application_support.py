"""Transparent operational test arrangement through existing execution owners."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from poise.execution import run_command, RegisteredCheckRunner
from threading import Thread
import urllib.request


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return path


def requirements_paths(manifest):
    state = Path(manifest['paths']['state'])
    return tuple(state / manifest['paths'][name]
                 for name in ('requirements_database', 'requirements_lock'))


def capture(argv, *, cwd, env, root, label, timeout=360):
    result = run_command([str(part) for part in argv], Path(cwd), env, timeout,
        root / (label + '.stdout'), root / (label + '.stderr'))
    write(root / (label + '.json'), result)
    assert result['capture_complete'] and not result['timed_out'], result
    return result, (root / (label + '.stdout')).read_bytes()


def sdk_operation(runtime, operation, config, parameters, base, root, label):
    env = {'PATH': runtime['path'], 'HOME': '/home/sbwerewolf', 'LANG': 'C.UTF-8',
        'PYTHONPATH': runtime['module_path']}
    argv = [runtime['sdk_entry'], operation, '--config', config,
            '--parameters', parameters, '--base-dir', base]
    if operation != 'check':
        argv += ['--requirements', 'all']
    result, stdout = capture(argv, cwd=base, env=env,
        root=root, label=label)
    assert result['actual_exit_code'] == 0, result
    packet = json.loads(stdout)
    assert packet['status'] == 'succeeded', packet
    assert packet['operation'] == operation, packet
    return packet


def clone_source(runtime, source, destination, root):
    """Even the source-clone setup is an explicit external-tool configuration."""
    env = {'PATH': runtime['path'], 'LANG': 'C.UTF-8'}
    observed, stdout = capture([runtime['git'], '-C', source, 'rev-parse', 'HEAD'],
        cwd=root, env=env, root=root, label='source-head', timeout=30)
    assert observed['actual_exit_code'] == 0
    commit = stdout.decode().strip()
    commands = [
        [runtime['git'], 'clone', '--local', '--no-hardlinks', '--no-checkout', source, destination],
        [runtime['git'], '-C', destination, 'checkout', '-b', 'codex/deployment-source', commit],
    ]
    requirement = {'id': 'source-clone', 'commands': [dict(argv=[str(p) for p in argv],
        cwd=str(root), env=env, interpreter={'name': 'exit', 'options': {'accept': [0]}})
        for argv in commands]}
    config_root = root / 'source-clone-config'
    write(config_root / 'infra.json', {'requirements': [requirement]})
    for name in ('deps', 'check'):
        write(config_root / (name + '.json'), {'requirements': []})
    app = write(config_root / 'app.json', {'operations': {'infra': 'infra.json',
        'deps': 'deps.json', 'check': 'check.json'}, 'interpreters': {'exit': {
            'module': 'poise_environment.result_interpreters', 'attribute': 'exit_result'}}})
    parameters = write(config_root / 'parameters.json', {})
    sdk_operation(runtime, 'infra', app, parameters, root, root, 'source-clone')
    return commit


def configured_private_project(project, name, repository, preparation):
    """Public project creation binds real application IDs in isolated fixture stores."""
    from conftest import WorkPoise, bind_task_requirements, seed_fixture_requirements
    from poise.composition import project_tools, project_config_tools
    from poise.application.work import WorkTools
    from poise.common import digest, load_config

    settings = Path(project['cfg']['runtime_resources']['project_setup'])
    # This is a private test template, not a deployed managed configuration.
    # Project setup edits select only fields explicitly present in its template.
    setup = json.loads(settings.read_text())
    template = setup['templates']['selected']
    blueprint_path = settings.parent / template['path']
    blueprint = json.loads(blueprint_path.read_text())
    blueprint['config']['worktree_preparation'] = {'configuration': str(preparation)}
    write(blueprint_path, blueprint)
    template['digest'] = digest(blueprint)
    write(settings, setup)
    created = project_tools(settings).apply({
        'schema': 'project-setup-1', 'request_id': 'create-real-application-fixture',
        'destination': 'configured/actual-application', 'template': {'id': 'selected'},
        'edits': [
            {'path': ['project'], 'value': name},
            {'path': ['git', 'repository'], 'value': str(repository)},
            {'path': ['git', 'base_ref'], 'value': 'codex/deployment-source'},
            {'path': ['git', 'push_required'], 'value': False},
            {'path': ['automatic_checks'], 'value': []},
            {'path': ['batch', 'max_artifact_bytes'], 'value': 16777216},
            {'path': ['paths', 'worktrees'], 'value': str(repository / '.runtime/worktrees')},
            {'path': ['worktree_preparation'], 'value': {'configuration': str(preparation)}},
        ], 'probe_repository': True,
    })
    path = Path(created['config_path'])
    _, config, workflows = load_config(path)
    publication = project_config_tools(settings).apply({
        'schema': 'project-runtime-resources-bootstrap-1',
        'request_id': 'actual-application-runtime-resources',
        'config_path': str(path),
        'expected_revision': digest({'config': config, 'workflows': workflows}),
        'runtime_resources': {'project_setup': str(settings), 'hooks': []},
        'receipt_path': 'operations/actual-application-runtime-resources.json',
    })
    assert publication['status'] == 'completed'
    _, config, _ = load_config(path)
    registry = seed_fixture_requirements(path.parent, config)
    task = deepcopy(project['task'])
    task.update(goal='Проверить подготовку отдельного окружения.',
        requirements=['Рабочее дерево подготовлено для проверки окружения.'],
        definition_of_done=['Наблюдения о подготовке окружения сохранены.'], methods=[], method_inputs=[])
    task['checks'] = {stage['id']: [] for stage in project['workflow']['stages']}
    task['evidence_plan'] = {stage['id']: {'subject_methods': {}, 'arguments': [],
        'review_arguments': []} for stage in project['workflow']['stages']}
    bind_task_requirements(task, registry)
    runtime = WorkPoise(path, 'ISOLATED-APPLICATION-FIXTURE')
    return WorkTools(runtime), task, config


def receipt(reply):
    path = Path(reply['preparation']['receipt_path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reply['preparation']['receipt_sha256']
    data = json.loads(path.read_text())
    assert data['status'] == reply['preparation']['status'] == 'ready', data
    return data


def profile_inputs(report):
    commands = [command for operation in report['operations'] for command in operation['commands']]
    modern = [row['argv'] for row in commands if '--config' in row['argv']]
    assert modern, 'The real public preparation must invoke operation configuration'
    assert not any('--catalog-dir' in row['argv'] for row in commands)
    argv = modern[-1]
    return Path(argv[argv.index('--config') + 1]), Path(argv[argv.index('--parameters') + 1])


def application_runtime(runtime, application, root):
    selected = dict(runtime)
    if application == 'erp':
        selected['module_path'] = os.pathsep.join([
            str(root / 'platform-infrastructure/tools'), runtime['module_path']])
    return selected


def consumer_command(runtime, application, operation, app, parameters, base, entry=None):
    if entry is not None:
        suffix = (['--config', str(app), '--parameters', str(parameters),
                   '--base-dir', str(base)] if application == 'erp' else [])
        return [*entry, operation, *suffix]
    if application == 'erp':
        return ['/bin/bash', str(base / 'start-app'), operation, '--config', str(app),
                '--parameters', str(parameters), '--base-dir', str(base)]
    return [runtime['service_python'], '-I', '-B', str(base / 'bootstrap'), operation,
            '--config', str(Path(app).parent / 'bootstrap.json'), '--base-dir', str(base)]


def consumer_operation(runtime, application, operation, app, parameters, base, evidence, label, entry=None):
    runtime = application_runtime(runtime, application, base)
    result, stdout = capture(consumer_command(runtime, application, operation,
        app, parameters, base, entry), cwd=base,
        env={'PATH': runtime['path'], 'HOME': '/home/sbwerewolf', 'LANG': 'C.UTF-8',
             'PYTHONPATH': runtime['module_path']}, root=evidence, label=label)
    assert result['actual_exit_code'] == 0, result
    packet = json.loads(stdout)
    if application == 'poise-op':
        assert packet['status'] == 'ready' and packet['exit_code'] == 0, packet
        # Existing Bootstrap plugin preserves the actual SDK JSON in its
        # initial observation; its owner exposes the packet without clipping.
        observation = packet['observations'][0]
        packet = json.loads(observation['stdout'])
    assert packet['status'] == 'succeeded', packet
    assert packet['operation'] == operation, packet
    return packet


def verify_launch(runtime, application, app, parameters, base, evidence, selected, entry=None):
    """Exercise the unchanged run action through the actual consumer entry."""
    runtime = application_runtime(runtime, application, base)
    before = {Path(app): Path(app).read_bytes(), Path(parameters): Path(parameters).read_bytes()}
    document = json.loads(before[Path(app)])
    run_path = Path(app).parent / document['operations']['run']
    before[run_path] = run_path.read_bytes()
    if application == 'poise-op':
        packet = consumer_operation(runtime, application, 'run', app, parameters,
                                    base, evidence, 'application-run', entry)
        command = packet['requirements'][0]['result']['commands'][0]
        import base64
        inventory = json.loads(base64.b64decode(command['stdout']['data']))
        assert inventory['projects'] and inventory['errors'] == []
        assert {path: path.read_bytes() for path in before} == before
        return
    seconds = selected['window_seconds']
    assert 0 < seconds <= 30
    private = evidence / 'private-console'
    private.mkdir()
    for name in ('tmp', 'run'):
        (private / name).mkdir(mode=0o700)
    environment = {'PATH': runtime['path'], 'HOME': '/home/sbwerewolf', 'LANG': 'C.UTF-8',
        'PYTHONPATH': runtime['module_path'], 'XDG_RUNTIME_DIR': str(private / 'run'),
        'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(private / 'run/no-host-bus')}
    inputs = write(evidence / 'launch-window-inputs.json', {
        'argv': consumer_command(runtime, application, 'run', app, parameters, base, entry),
        'parameters': str(parameters), 'base': str(base), 'evidence': str(evidence),
        'environment': environment, 'private_display': True,
        'command_timeout': selected['command_timeout'], 'window_seconds': seconds})
    # Standard private PID namespace owns every detached console. Xvfb and its
    # socket/auth files live in that namespace's private /tmp, never WSLg.
    argv = [selected['bwrap'], '--unshare-pid', '--die-with-parent', '--ro-bind', '/', '/',
        '--bind', str(private / 'tmp'), '/tmp',
        '--bind', str(base), str(base), '--bind', str(evidence), str(evidence),
        '--proc', '/proc', '--dev-bind', '/dev', '/dev',
        selected['xvfb_run'], '-a', '-s', '-screen 0 1700x700x24 -nolisten tcp',
        runtime['service_python'], '-B', str(Path(__file__).with_name('live_launch_window.py')),
        str(inputs)]
    runner, outcome, errors = RegisteredCheckRunner(), {}, []

    def observe():
        try:
            outcome.update(runner.run('owned-application-window', argv, base, environment,
                seconds + selected['command_timeout'] + 15,
                evidence / 'application-window.stdout', evidence / 'application-window.stderr'))
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=observe, name='registered-application-observer')
    worker.start()
    try:
        probe = selected['readiness']
        observed, stdout = capture(probe['argv'], cwd=base, env=probe['env'],
            root=evidence, label='application-readiness', timeout=probe['timeout_seconds'])
        assert observed['actual_exit_code'] == 0, stdout
        for endpoint in selected['endpoints']:
            with urllib.request.urlopen(endpoint, timeout=10) as response:
                body = response.read()
                assert response.status == 200 and body
                name = 'http-' + hashlib.sha256(endpoint.encode()).hexdigest()[:12]
                (evidence / (name + '.bin')).write_bytes(body)
                write(evidence / (name + '.json'), {'status': 200, 'url': endpoint,
                    'body_sha256': hashlib.sha256(body).hexdigest()})
    finally:
        worker.join(50)
        if worker.is_alive():
            runner.cancel('owned-application-window')
            worker.join(5)
        assert not worker.is_alive(), 'Owned application observer must finish'
        write(evidence / 'application-window.json', outcome)
        if errors:
            write(evidence / 'application-observer-errors.json',
                  {'errors': [repr(error) for error in errors]})
    assert not errors, errors
    assert outcome['actual_exit_code'] == 0 and outcome['capture_complete']
    assert not outcome['timed_out'] and outcome['termination']['child_reaped']
    assert outcome['termination']['process_group_stopped']
    actual = json.loads((evidence / 'declared-run.json').read_text())
    assert actual['actual_exit_code'] == 0 and actual['capture_complete'] and not actual['timed_out']
    packet = json.loads((evidence / 'declared-run.stdout').read_bytes())
    assert packet['status'] == 'succeeded'
    assert [row['id'] for row in packet['requirements']] == selected['process_ids']
    assert all(row['result']['status'] == 'succeeded' for row in packet['requirements'])
    assert {path: path.read_bytes() for path in before} == before
