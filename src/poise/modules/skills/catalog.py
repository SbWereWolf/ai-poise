"""Immutable metadata shared by routing consumers and focused decomposition."""
from __future__ import annotations

from dataclasses import dataclass
import re

from ..foundation.validation import validate_exact_keys
from ..foundation.errors import DomainError


SCHEMA = "poise-skill-catalog-1"
_FIELDS = {"id", "path", "purpose", "class", "level", "responsibility"}
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _exact(value: object, fields: set[str], where: str) -> None:
    validate_exact_keys(value, fields, where, DomainError)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _instruction_path(value: object) -> bool:
    return (
        _text(value)
        and value == value.strip()
        and not any(char in value for char in "\\:*?[]")
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and value.split("/")[-1] == "SKILL.md"
    )


@dataclass(frozen=True)
class SkillMetadata:
    id: str
    path: str
    purpose: str
    skill_class: str
    level: int | None
    responsibility: str | None

    def as_dict(self) -> dict:
        return {"id": self.id, "path": self.path, "purpose": self.purpose,
                "class": self.skill_class, "level": self.level,
                "responsibility": self.responsibility}


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[SkillMetadata, ...]

    @classmethod
    def parse(cls, document: object) -> SkillCatalog:
        _exact(document, {"schema", "skills"}, "skill catalog")
        if document["schema"] != SCHEMA:
            raise DomainError("Unsupported skill catalog schema")
        items = document["skills"]
        if not isinstance(items, list) or not items:
            raise DomainError("skill catalog.skills must be a non-empty list")
        result, ids, paths = [], set(), set()
        for item in items:
            _exact(item, _FIELDS, "skill metadata")
            skill_id = item["id"]
            if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id) or skill_id in ids:
                raise DomainError("skill catalog requires unique kebab-case skill IDs")
            if not _instruction_path(item["path"]) or item["path"] in paths:
                raise DomainError(f"skill {skill_id}: requires a unique canonical relative SKILL.md path")
            if not _text(item["purpose"]):
                raise DomainError(f"skill {skill_id}: purpose must be non-empty text")
            if item["class"] not in ("meta", "common", "working"):
                raise DomainError(f"skill {skill_id}: class must be meta, common or working")
            if item["class"] == "working":
                if type(item["level"]) is not int or item["level"] <= 0:
                    raise DomainError(f"skill {skill_id}: working level must be a positive integer")
                if not _text(item["responsibility"]):
                    raise DomainError(f"skill {skill_id}: working responsibility must be explicit")
            elif item["level"] is not None or item["responsibility"] is not None:
                raise DomainError(f"skill {skill_id}: meta/common level and responsibility must be null")
            ids.add(skill_id)
            paths.add(item["path"])
            result.append(SkillMetadata(skill_id, item["path"], item["purpose"],
                                        item["class"], item["level"], item["responsibility"]))
        return cls(tuple(sorted(result, key=lambda item: item.id)))

    def as_dict(self) -> dict:
        return {"schema": SCHEMA, "skills": [skill.as_dict() for skill in self.skills]}

    def select(self, skill_ids: object) -> list[dict]:
        if (not isinstance(skill_ids, list)
                or any(not isinstance(item, str) or not item for item in skill_ids)
                or len(skill_ids) != len(set(skill_ids))):
            raise DomainError("skill_ids must be an explicit list of unique strings")
        unknown = sorted(set(skill_ids) - {skill.id for skill in self.skills})
        if unknown:
            raise DomainError(f"unknown catalog skills: {unknown}")
        selected = set(skill_ids)
        return [skill.as_dict() for skill in self.skills if skill.id in selected]

    def decomposition_skills(self, skill_ids: list[str]) -> list[dict]:
        """Translate the catalog vocabulary to the existing project-policy contract.

        This is a representation mapping, not another classifier. Responsibility
        comes only from the metadata; no path, framework or name inference occurs.
        """
        classes = {"meta": "meta", "common": "general", "working": "narrow"}
        return [{"id": skill["id"], "class": classes[skill["class"]],
                 "responsibility": skill["responsibility"]}
                for skill in self.select(skill_ids)]
