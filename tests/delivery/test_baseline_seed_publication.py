"""Task0137: shipped baseline schedule must publish through the current owners."""
import json
import os
import subprocess
import sys

import pytest

from poise.modules.tasks.definition import validate_creation
from tests.delivery.test_wsl_seed import ROOT, task_definitions, project_from_blueprint


@pytest.mark.parametrize('task_id', ['0001','0002'])
def test_shipped_development_baseline_contract_is_consistent(task_id):
    task = task_definitions()[task_id]
    blueprint = json.loads((ROOT/'config/project-templates/wsl-poise.json').read_text())
    process = json.loads((ROOT/blueprint['process_sources']['development']['path']).read_text())
    validated = validate_creation(task, process, [], blueprint['config']['task_decomposition'])
    assert validated['contract']['checks']['baseline'] == ['BASELINE']
    assert validated['contract']['evidence_plan']['baseline']['subject_methods']['BASELINE']['exit_codes']==[0]


def test_unmodified_seed_publishes_without_sql_or_contract_repair(tmp_path):
    cfg = project_from_blueprint(tmp_path)
    completed = subprocess.run([sys.executable, str(ROOT/'tools/seed_wsl_tasks.py'),
        '--poise-config', str(cfg)], env={**os.environ,'PYTHONPATH':str(ROOT/'src')},
        capture_output=True,text=True,timeout=20)
    assert completed.returncode == 0, completed.stdout+completed.stderr
    assert json.loads(completed.stdout)['status'] == 'seeded'


@pytest.mark.parametrize('task_id', ['0001', '0002'])
def test_seed_trace_can_be_planned_but_is_required_before_code(task_id):
    from poise.modules.content_requirements.domain import ContentPolicy, ContentSnapshot

    task = task_definitions()[task_id]
    process = json.loads((ROOT/'config/catalogue/processes/development.json').read_text())
    policy = ContentPolicy.from_layers(
        {'sections': [], 'routes': [], 'requirements': []}, task['content_contract'],
        tuple(stage['id'] for stage in process['stages']), tuple(task['requirements']),
        ('PLANNED_RED', 'PLANNED_GREEN'), (),
    )
    empty = ContentSnapshot((), ())
    assert policy.evaluate('verification_planning', 'pre', empty, ()).passed
    for stage in ('test_implementation', 'implementation'):
        assert not policy.evaluate(stage, 'pre', empty, ()).passed
    planned = policy.apply(empty, 'verification_planning', {}, {
        'task-verification': {'red_method': 'PLANNED_RED', 'green_method': 'PLANNED_GREEN'},
    })
    for stage in ('test_implementation', 'implementation'):
        assert policy.evaluate(stage, 'pre', planned, ()).passed
