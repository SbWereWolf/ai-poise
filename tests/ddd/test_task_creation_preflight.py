"""Compatibility selector for the task-local immutable verification method."""

import importlib.util
from pathlib import Path


_source = Path(__file__).with_name("test_creation_method_preflight.py")
_spec = importlib.util.spec_from_file_location("creation_method_preflight_contract", _source)
assert _spec is not None and _spec.loader is not None
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)

globals().update({
    name: value
    for name, value in vars(_contract).items()
    if name.startswith("test_") and callable(value)
})
