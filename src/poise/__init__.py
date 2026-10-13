"""Public Python API for AI poise; runtime loads only on explicit access."""

from .modules.foundation.errors import PoiseError

__all__ = ["Poise", "PoiseError"]


def __getattr__(name):
    if name == "Poise":
        from .runtime import Poise
        globals()["Poise"] = Poise
        return Poise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
