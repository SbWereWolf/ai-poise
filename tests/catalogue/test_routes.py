"""Reference walkthroughs use public tools, real Git/commands and SQLite."""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from itertools import repeat
from multiprocessing import get_context
from pathlib import Path
import json
import subprocess
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


def test_reference_coverage_retains_explicit_historical_gaps():
    from catalogue_walkthrough import coverage
    from tests.catalogue.test_paired_stages import accepted_workflow
    report = coverage()
    root = Path(__file__).resolve().parents[2]
    history = json.loads((root / 'docs/architecture-design/data/stage-library-map.json').read_text())
    historical = {(node['goal_type'], node['stage']) for node in history['nodes']}
    accepted = {
        (goal, stage['id'])
        for goal in GOALS for stage in accepted_workflow(goal)['stages']
    }
    assert report['nodes'] == len(accepted) == 143
    assert report['feedback_edges'] == 52
    assert report['unmapped'] == sorted(historical - accepted)
    assert len(report['unmapped']) == 19
    assert report['extra'] == sorted(accepted - historical)
    assert len(report['extra']) == 60


@pytest.mark.parametrize("goal", [
    "design", "development", "documentation", "environment_remediation",
    "integration", "sprint_planning", "task_planning", "test_development",
])
def test_paired_reference_route_through_public_handoffs(tmp_path, goal, monkeypatch):
    if goal == "integration":
        _assert_real_integration(tmp_path / goal, "short", monkeypatch)
        return
    from catalogue_walkthrough import run
    result = run(tmp_path / goal, goal, "short")
    assert result["status"] == "PASS"
    assert result["task_status"] == "completed"
    assert len(result["reports"]) == len(result["accepted_stages"])
    assert result["remote_unchanged"]


def test_integration_feedback_through_public_handoffs(tmp_path, monkeypatch):
    _assert_real_integration(tmp_path / "integration", "feedback", monkeypatch)


def _assert_real_integration(directory, scenario, monkeypatch):
    from catalogue_walkthrough import run
    from poise.application.work import WorkTools

    application = directory / "application"
    remote = directory / "remote.git"
    anchors = {}
    preparations = []
    integrations = []
    invoke = WorkTools.invoke

    def git(repository, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(repository), *arguments], text=True,
        ).strip()

    def observe(tool, packet):
        arguments = packet["input"]
        payload = arguments.get("result")
        if packet["operation"] == "verify" and isinstance(payload, dict):
            work = payload.get("stage_work", {})
            plan = work.get("plan")
            if work.get("phase") == "prepare" and isinstance(plan, dict) and plan.get("kind") == "git_merge":
                if not anchors:
                    anchors["target"] = git(application, "rev-parse", "refs/heads/main")
                    anchors["remote"] = git(remote, "rev-parse", "refs/heads/main")
                    for component in ("left", "right"):
                        anchors[component] = git(application, "rev-parse", "refs/heads/" + component)
                preparations.append((deepcopy(plan), git(application, "rev-parse", "refs/heads/tasks/MAIN")))
        if packet["operation"] == "integrate":
            intent = deepcopy(arguments)
            accepted_head = git(application, "rev-parse", "refs/heads/tasks/MAIN")
            response = invoke(tool, packet)
            integrations.append((intent, response, accepted_head))
            return response
        return invoke(tool, packet)

    monkeypatch.setattr(WorkTools, "invoke", observe)
    result = run(directory, "integration", scenario)
    assert result["status"] == "PASS"
    assert result["task_status"] == "completed"
    assert result["reports"] and len(result["reports"]) == len(result["accepted_stages"])
    assert result["remote_unchanged"]
    assert result["target_published"]
    assert preparations
    for plan, observed_head in preparations:
        assert plan["base_commit"] == observed_head
    assert len(integrations) == 1
    intent, response, accepted_head = integrations[0]
    assert isinstance(intent["authorization"], str) and intent["authorization"].strip()
    assert isinstance(intent["commit_message"], str) and intent["commit_message"].strip()
    assert intent["authorization"] != intent["commit_message"]
    assert intent["expected_source_commit"] == accepted_head
    assert intent["expected_target_commit"] == anchors["target"]
    assert response["status"] == "integrated"
    assert git(application, "rev-parse", "refs/heads/main") == response["target_after"]
    for component in ("left", "right"):
        assert git(application, "rev-parse", "refs/heads/" + component) == anchors[component]
        subprocess.run(
            ["git", "-C", str(application), "merge-base", "--is-ancestor",
             anchors[component], "refs/heads/main"], check=True,
        )
    assert git(remote, "rev-parse", "refs/heads/main") == anchors["remote"]
    if scenario == "feedback":
        assert result["feedback_cycles"] >= 1
        assert max(report["iteration"] for report in result["reports"]) >= 2
        assert len(preparations) >= 2
        assert any(head != anchors["target"] for _, head in preparations[1:])
