import pytest

from .helpers import decoded, PlacementUnavailable


def test_only_actual_unsupported_placement_is_classified():
    actual = "usage: poise [-h] command\npoise: error: argument command: invalid choice: 'placement'\n"
    with pytest.raises(PlacementUnavailable) as failure:
        decoded(2, '', actual)
    assert failure.value.code == 'PLACEMENT_UNSUPPORTED'
    for code, stdout, stderr in [(1, '', actual), (2, '{}', actual),
                                 (2, '', 'ImportError: placement'),
                                 (2, '', "usage: command\ninvalid choice: 'other'")]:
        try:
            decoded(code, stdout, stderr)
        except PlacementUnavailable:
            pytest.fail('An unrelated failure was misclassified as placement RED')
        except ValueError:
            pass
    assert decoded(2, '{"status":"rejected"}', 'unrelated') == (2, {'status': 'rejected'})
