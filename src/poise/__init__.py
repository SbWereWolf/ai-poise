"""Public Python API for AI poise."""

from .modules.foundation.errors import PoiseError

__all__ = ["Poise", "PoiseError"]


def __getattr__(name):
    """The Task API is composed only when explicitly requested."""
    if name == 'Poise':
        from .runtime import Poise
        return Poise
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
