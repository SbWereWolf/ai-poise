"""Deferred consumer: execute only after the documentation example producer."""
import json
from pathlib import Path

from .helpers import Scenario


def test_shipped_policy_example_is_executable(project, tmp_path):
    s = Scenario(project, tmp_path)
    shipped = Path(__file__).resolve().parents[2] / 'config/examples/result-placement.example.json'
    # The example's protocol is the accepted literal Alpha policy with explicit tokens.
    s.policy_data = s.fill(json.loads(shipped.read_text(encoding='utf-8')))
    s.save_policy()
    before = s.initial()
    value = s.assert_result(s.invoke(s.request(operation='check')))
    s.assert_effect(value, before, None, 'none', 'not_applicable')
    assert s.state.read_bytes() == before
