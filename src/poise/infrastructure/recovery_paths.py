"""Lexical path checks shared only by compact recovery filesystem adapters."""
import os
from pathlib import Path

from ..common import PoiseError, descendant


def recovery_path(root, relative):
    root = Path(os.path.abspath(root))
    lexical = root / relative
    for part in [lexical, *lexical.parents]:
        if part.is_symlink():
            # Preserve the existing containment diagnostic for an escaping link.
            try:
                descendant(root, relative)
            except PoiseError as error:
                raise PoiseError(str(error) + '; symlink: ' + str(part)) from error
            raise PoiseError('Недопустимая символьная ссылка (symlink): ' + str(part))
    return descendant(root, relative)


def recovery_absolute(path):
    path = Path(os.path.abspath(path))
    return recovery_path(Path(path.anchor), path.relative_to(path.anchor).as_posix())
