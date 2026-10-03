"""Reference walkthroughs use public tools, real Git/commands and SQLite."""
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from multiprocessing import get_context
from pathlib import Path
import json
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"examples"))

GOALS=('development','test_development','verification','review','design','analysis',
       'profiling','environment_diagnostics','environment_remediation','documentation',
       'task_planning','sprint_planning','integration')


def _run_route(root,case):
    goal,scenario=case
    from catalogue_walkthrough import run
    return case,run(root/f'{scenario}-{goal}',goal,scenario)


def test_full_routes_and_feedback_without_direct_database_setup(tmp_path):
    matrix=[(goal,scenario) for scenario in ('short','feedback') for goal in GOALS]
    # Runtime telemetry may fork; never fork a multithreaded test worker.
    with ProcessPoolExecutor(max_workers=8, mp_context=get_context('spawn')) as pool:
        results=pool.map(_run_route,repeat(tmp_path),matrix)
        for (goal,scenario),result in results:
            case=f'{scenario}-{goal}'
            assert result['status']=='PASS',case
            assert result['task_status']=='completed',case
            assert result['goal_type']==goal,case
            assert result['remote_unchanged'],case
            assert result['read_only_checks']>0,case
            assert result['reports'] and len(result['reports'])==len(result['accepted_stages']),case
            if scenario=='feedback':
                assert result['feedback_cycles']>=1,case
                assert max(r['iteration'] for r in result['reports'])>=2,case
            assert result['ready_artifacts']>=1,case
            if goal in ('task_planning','sprint_planning'):
                assert result['published_tasks'],case
                assert result['child_bootstrap_valid'],case
            if goal=='integration':
                assert result['target_published'],case
            else:
                assert result['target_unchanged'],case


def test_every_declared_feedback_edge_has_a_walkthrough(tmp_path):
    from catalogue_walkthrough import coverage
    report=coverage()
    reference = json.loads(
        (Path(__file__).resolve().parents[2] / 'config/catalogue/reference.json').read_text()
    )
    assert report['nodes'] == sum(len(goal['stages']) for goal in reference['goal_types'])
    assert report['feedback_edges']==34
    assert report['unmapped']==[]


@pytest.mark.parametrize("goal", [
    "design", "development", "documentation", "environment_remediation",
    "integration", "sprint_planning", "task_planning", "test_development",
])
def test_paired_reference_route_through_public_handoffs(tmp_path, goal):
    from catalogue_walkthrough import run
    result = run(tmp_path / goal, goal, "short")
    assert result["status"] == "PASS"
    assert result["task_status"] == "completed"
    assert len(result["reports"]) == len(result["accepted_stages"])
    assert result["remote_unchanged"]
