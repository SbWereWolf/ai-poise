"""Run each explicitly documented feedback edge, not combinatorial fault mixtures."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'examples'))
EDGES=json.loads((ROOT/'config/catalogue/reference.json').read_text())['feedback_edges']


def _run_edge(root,edge):
    from catalogue_walkthrough import run
    return edge,run(root/edge['id'],edge['goal_type'],'feedback',edge['id'])


def test_feedback_edges_return_to_work_and_finish_same_task(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        cases=pool.map(lambda edge:_run_edge(tmp_path,edge),EDGES)
        for edge,result in cases:
            case=edge['id']
            assert result['task_status']=='completed',case
            assert case in result['traversed_feedback_edges'],case
            assert result['ready_artifacts']>=1,case
            assert result['open_findings_at_completion']==0,case
