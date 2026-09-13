import importlib
import json
from pathlib import Path

import pytest

from poise.modules.goal_config.domain import validate_process


@pytest.mark.parametrize('producer', ['demo', 'actions_demo', 'sprint_demo'])
def test_example_process_producer_declares_worktree_requirement(tmp_path, monkeypatch, producer):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'examples'))
    module = importlib.import_module(producer)
    if producer == 'demo':
        root = module.create(tmp_path / 'example')
        processes = [json.loads((root / 'config/processes/development.json').read_text())]
    else:
        kinds = (['integration', 'environment_remediation'] if producer == 'actions_demo'
                 else ['development', 'documentation'])
        processes = [module.process(kind) for kind in kinds]
    for process in processes:
        assert process.get('worktree_required') is True
        validate_process(process)
