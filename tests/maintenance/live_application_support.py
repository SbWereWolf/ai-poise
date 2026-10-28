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


def capture(argv, *, cwd, env, root, label, timeout=360):
    result = run_command([str(part) for part in argv], Path(cwd), env, timeout,
        root / (label + '.stdout'), root / (label + '.stderr'))
    write(root / (label + '.json'), result)
    assert result['capture_complete'] and not result['timed_out'], result
    return result, (root / (label + '.stdout')).read_bytes()


def sdk_operation(runtime, operation, config, parameters, base, root, label):
    env = {'PATH': runtime['path'], 'HOME': '/home/sbwerewolf', 'LANG': 'C.UTF-8',
        'PYTHONPATH': runtime['module_path']}
    result, stdout = capture([runtime['sdk_entry'], operation, '--config', config,
        '--parameters', parameters, '--base-dir', base], cwd=base, env=env,
        root=root, label=label)
    assert result['actual_exit_code'] == 0, result
    packet = json.loads(stdout)
    assert packet['status'] == 'succeeded', packet
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


def verify_launch(runtime, application, app, parameters, base, evidence, selected):
    """Test-owned foreground window; preserve the normal user's console profile.

    The existing SDK runs the real application-owned foreground command. Standard
    timeout bounds this *verification* window, and the common runner owns/reaps
    its process group. Readiness requires an independent HTTP response before
    the declared stop; timeout alone never proves application readiness.
    """
    runtime = application_runtime(runtime, application, base)
    if application == 'poise-op':
        packet = sdk_operation(runtime, 'run', app, parameters, base, evidence, 'application-run')
        command = packet['requirements'][0]['result']['commands'][0]
        import base64
        inventory = json.loads(base64.b64decode(command['stdout']['data']))
        assert inventory['projects'] and inventory['errors'] == []
        return
    document = json.loads(Path(app).read_text())
    document['operations'] = {key: str(Path(app).parent / value)
        for key, value in document['operations'].items()}
    document['operations']['run'] = 'foreground-run.json'
    document['interpreters']['exit'] = {
        'module': 'poise_environment.result_interpreters', 'attribute': 'exit_result'}
    run = Path(__file__).parent / 'fixtures/live_deployment/erp-window-run.json'
    write(evidence / 'foreground-run.json', json.loads(run.read_text()))
    window_app = write(evidence / 'foreground-app.json', document)
    values = json.loads(Path(parameters).read_text())
    values.update(verification_timeout=runtime['timeout_executable'],
                  verification_seconds=str(selected['window_seconds']))
    window_parameters = write(evidence / 'foreground-parameters.json', values)
    argv = [runtime['sdk_entry'], 'run', '--config', str(window_app),
        '--parameters', str(window_parameters), '--base-dir', str(base)]
    env = {'PATH': runtime['path'], 'HOME': '/home/sbwerewolf', 'LANG': 'C.UTF-8',
        'PYTHONPATH': runtime['module_path']}
    runner, outcome, errors = RegisteredCheckRunner(), {}, []

    def observe():
        try:
            outcome.update(runner.run('owned-application-window', argv, base, env,
                selected['window_seconds'] + 15, evidence / 'application-run.stdout',
                evidence / 'application-run.stderr'))
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=observe, name='registered-application-observer')
    worker.start()
    try:
        probe = selected['readiness']
        observed, stdout = capture(probe['argv'], cwd=base, env=probe['env'],
            root=evidence, label='application-readiness', timeout=probe['timeout_seconds'])
        assert observed['actual_exit_code'] == 0, stdout
        with urllib.request.urlopen(selected['health_url'], timeout=10) as response:
            body = response.read()
            assert response.status == 200 and body
            (evidence / 'http-body.bin').write_bytes(body)
        write(evidence / 'http-observation.json', {'status': 200,
            'url': selected['health_url'], 'body_sha256': hashlib.sha256(body).hexdigest()})
    finally:
        worker.join(selected['window_seconds'] + 20)
        if worker.is_alive():
            runner.cancel('owned-application-window')
            worker.join(5)
        assert not worker.is_alive(), 'Owned application observer must finish'
        write(evidence / 'application-run.json', outcome)
        if errors:
            write(evidence / 'application-observer-errors.json',
                  {'errors': [repr(error) for error in errors]})
    assert not errors, errors
    assert outcome['actual_exit_code'] == 0 and outcome['capture_complete']
    assert not outcome['timed_out'] and outcome['termination']['child_reaped']
    assert outcome['termination']['process_group_stopped']
    packet = json.loads((evidence / 'application-run.stdout').read_bytes())
    assert packet['status'] == 'succeeded'
    command = packet['requirements'][0]['result']['commands'][0]
    assert command['exit_code'] == 124
    assert 'run-process' in command['invocation']['argv']
    assert command['invocation']['cwd'] == str(base)
