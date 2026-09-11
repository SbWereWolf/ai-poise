"""Task-0029 selector retained for its immutable planned check registration."""

from runtime_services.test_failed_check_rework import (
    test_failed_check_can_rework_to_declared_stage_without_recreating_task as canonical_recovery,
)


def test_supplemental_failed_check_rework_selector(project):
    canonical_recovery(project)
