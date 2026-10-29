"""Supported Task transport for the existing literal CRUD consumer fixture."""
from copy import deepcopy

from poise.common import load_config
from poise.infrastructure.requirements_registry import RequirementsStore
from requirements_registry.crud_support import arrange
from requirements_registry.sql_migration_support import SOURCE, literal
from runtime_services.stage_criteria_helpers import boot


def arrange_owned(project, tmp_path):
    database, lock, config_path = arrange(project, tmp_path)
    _, config, _ = load_config(config_path)
    # Work envelopes include fixed objects with more than four fields. Queries
    # retain their literal page limit of four; use the existing project envelope.
    config['batch']['max_items'] = project['cfg']['batch']['max_items']
    settings = deepcopy(literal('contract.json')['settings'])
    settings['initial_directory'] = str(SOURCE / settings['initial_directory'])
    config['requirements_migrations'] = settings
    registry = RequirementsStore(
        database, lock, config['limits']['lock_seconds'],
        config['limits']['lock_poll_seconds'],
        read_wait=config['limits']['sqlite_read_seconds'],
    ).registry()
    task = deepcopy(project['task'])
    plan = registry.plan_task([
        {'text': text, 'applications': ['A1']} for text in task['requirements']
    ])
    assert plan['status'] == 'ready'
    task['requirements_snapshot'] = plan['snapshot']
    task['requirements_agreement'] = {'accepted': True, 'chains': plan['chains']}
    tools, _ = boot({**project, 'cfg': config, 'task': task}, initial_execution=True)
    return database, lock, config_path, tools


def invoke_owned(tools, packet):
    response = tools.invoke({
        'operation': 'requirements', 'input': {'request': deepcopy(packet)}, 'messages': [],
    })
    return {key: value for key, value in response.items() if key != 'interaction'}
