"""Explicit catalog file loading; never read instruction/reference bodies."""
from pathlib import Path

from ..common import descendant
from ..modules.foundation.errors import PoiseError
from ..modules.skills.catalog import SkillCatalog
from .goal_config import strict_json


def load_skill_catalog(path: Path, root: Path) -> SkillCatalog:
    try:
        document = strict_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PoiseError(f"Cannot read skill catalog {path}: {exc}") from exc
    catalog = SkillCatalog.parse(document)
    if not root.is_dir():
        raise PoiseError(f"Skill installation root is not a directory: {root}")
    for skill in catalog.skills:
        try:
            instruction = descendant(root, skill.path)
            if not instruction.is_file():
                raise PoiseError("instruction file is missing or not a regular file")
        except (PoiseError, OSError, RuntimeError) as exc:
            raise PoiseError(f"skill {skill.id}: {exc}") from exc
    return catalog
