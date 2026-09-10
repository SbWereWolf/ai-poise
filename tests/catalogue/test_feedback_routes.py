"""Run each explicitly documented feedback edge, not combinatorial fault mixtures."""
from pathlib import Path
import json
import sys
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'examples'))
EDGES=json.loads((ROOT/'config/catalogue/reference.json').read_text())['feedback_edges']


@pytest.mark.parametrize('edge',EDGES,ids=[e['id'] for e in EDGES])
def test_feedback_edge_returns_to_work_and_finishes_same_task(tmp_path,edge):
    from catalogue_walkthrough import run
    result=run(tmp_path/'walk',edge['goal_type'],'feedback',edge['id'])
    assert result['task_status']=='completed'
    assert edge['id'] in result['traversed_feedback_edges']
    assert result['ready_artifacts']>=1
    assert result['open_findings_at_completion']==0
