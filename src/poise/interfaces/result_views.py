"""Saved-output navigation only; does not establish or replay a Work session."""
from ..common import PoiseError
from ..infrastructure.result_views import saved_result_view


def execute(path, mode, budget, output, error):
    try:
        text = saved_result_view(path, mode, budget)
    except (KeyError, TypeError, RecursionError):
        error.write('Invalid saved result presentation data\n')
        return 2
    except (PoiseError, OSError, ValueError, UnicodeError) as exc:
        error.write(str(exc) + '\n')
        return 2
    output.write(text)
    return 0
