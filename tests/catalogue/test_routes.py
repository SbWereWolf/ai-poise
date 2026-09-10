"""Reference walkthroughs use public tools, real Git/commands and SQLite."""
from pathlib import Path
import json
import pytest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"examples"))

GOALS=('development','test_development','verification','review','design','analysis',
       'profiling','environment_diagnostics','environment_remediation','documentation',
       'task_planning','sprint_planning','integration')


@pytest.mark.parametrize('goal',GOALS)
@pytest.mark.parametrize('scenario',['short','feedback'])
def test_full_route_and_feedback_without_direct_database_setup(tmp_path,goal,scenario):
    from catalogue_walkthrough import run
    result=run(tmp_path/'walk',goal,scenario)
    assert result['status']=='PASS'
    assert result['task_status']=='completed'
    assert result['goal_type']==goal
    assert result['read_only_checks']>0
    assert result['reports'] and len(result['reports'])==len(result['accepted_stages'])
    if scenario=='feedback':
        assert result['feedback_cycles']>=1
        assert max(r['iteration'] for r in result['reports'])>=2
    assert result['ready_artifacts']>=1
    if goal in ('task_planning','sprint_planning'):
        assert result['published_tasks']
        assert result['child_bootstrap_valid']
    if goal=='integration':
        assert result['target_published']
    else:
        assert result['target_unchanged']


def test_every_declared_feedback_edge_has_a_walkthrough(tmp_path):
    from catalogue_walkthrough import coverage
    report=coverage()
    assert report['nodes']==98
    assert report['feedback_edges']==34
    assert report['unmapped']==[]
