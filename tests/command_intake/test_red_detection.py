"""The intended absence is exact; arbitrary failures retain their identity."""
import pytest

from tests.command_intake.helpers import (
    ToolEventsUnavailable, fixture, install_capability,
)


def test_known_tool_event_rejection_is_feature_red():
    from poise.common import PoiseError
    def missing():
        raise PoiseError(fixture('gate-errors.json')['absent'])
    with pytest.raises(ToolEventsUnavailable) as result:
        install_capability(missing)
    assert str(result.value) == fixture('gate-errors.json')['absent']
    marker = object()
    assert install_capability(lambda: marker) is marker


def test_unrelated_failure_is_not_feature_red():
    from poise.common import PoiseError
    errors = [PoiseError(text) for text in fixture('gate-errors.json')['unrelated']]
    errors.extend([ImportError('subject absent'), PermissionError('fixture failed'),
                   ValueError(fixture('gate-errors.json')['absent'])])
    for error in errors:
        def broken():
            raise error
        with pytest.raises(type(error)) as actual:
            install_capability(broken)
        assert actual.value is error
