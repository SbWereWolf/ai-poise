import importlib
import json
from pathlib import Path

import pytest

from poise.modules.goal_config.domain import validate_workflow


@pytest.mark.parametrize('producer', ['demo', 'actions_demo', 'sprint_demo'])
def test_example_workflow_producer_declares_worktree_requirement(tmp_path, monkeypatch, producer):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'examples'))
    module = importlib.import_module(producer)
    if producer == 'demo':
        root = module.create(tmp_path / 'example')
        workflows = [json.loads((root / 'config/workflows/development.json').read_text())]
    else:
        kinds = (['integration', 'environment_remediation'] if producer == 'actions_demo'
                 else ['development', 'documentation'])
        workflows = [module.workflow(kind) for kind in kinds]
    for workflow in workflows:
        assert workflow.get('worktree_required') is True
        validate_workflow(workflow)
