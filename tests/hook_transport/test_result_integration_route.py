from poise.modules.hook_transport.domain import BoundSourceRoute


def test_result_integration_always_runs_from_current_installation_source():
    accepted_task = {"id": "T1", "status": "completed"}
    active_task = {"id": "T2", "status": "active"}

    route = BoundSourceRoute.decide("integrate", None, active_task, accepted_task)

    assert route.source == "installation"
    assert route.task_id is None
