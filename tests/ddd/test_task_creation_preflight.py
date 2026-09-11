"""Compatibility selector for the task-local immutable verification method."""

import importlib.util
from pathlib import Path


_source = Path(__file__).with_name("test_creation_method_preflight.py")
_spec = importlib.util.spec_from_file_location("creation_method_preflight_contract", _source)
assert _spec is not None and _spec.loader is not None
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)

test_legacy_documentation_task_with_missing_pytest_path_is_rejected_before_allocation = (
    _contract.test_legacy_documentation_task_with_missing_pytest_path_is_rejected_before_allocation
)
