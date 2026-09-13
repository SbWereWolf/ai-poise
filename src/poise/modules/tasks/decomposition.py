"""Pure validation of project-routed Task decomposition declarations."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass

from ..foundation.errors import DomainError
from ..verification.domain import exact_keys


def _unique_strings(value, where):
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise DomainError(f"{where}: requires unique strings")
    return tuple(value)


def _nonempty_strings(value, where):
    result = _unique_strings(value, where)
    if not result:
        raise DomainError(f"{where}: requires non-empty strings")
    return result


def _contains(pattern, area):
    if pattern == area:
        return True
    if pattern.endswith("/**"):
        return area.startswith(pattern[:-3].rstrip("/") + "/")
    return False


@dataclass(frozen=True)
class FocusedDecomposition:
    kind: str
    phases: tuple[dict, ...]
    integration: dict | None

    @classmethod
    def parse(cls, declaration, stages):
        exact_keys(declaration, {"kind", "phases", "integration"}, "decomposition")
        if declaration["kind"] not in ("ordinary", "integration"):
            raise DomainError("decomposition kind must be ordinary or integration")
        expected = _unique_strings(list(stages), "process stages")
        phases = declaration["phases"]
        if not isinstance(phases, list):
            raise DomainError("decomposition phases must be a list")
        parsed = []
        names = []
        for index, phase in enumerate(phases):
            if not isinstance(phase, dict):
                raise DomainError(f"decomposition phase {index}: expected object")
            stage = phase.get("stage")
            where = f"decomposition phase {stage if isinstance(stage, str) else index}"
            phase_fields = {"stage", "skills", "areas"}
            missing_fields = sorted(phase_fields - set(phase))
            unknown_fields = sorted(set(phase) - phase_fields)
            if missing_fields or unknown_fields:
                raise DomainError(
                    f"{where}: отсутствуют {missing_fields}; неизвестные поля {unknown_fields}"
                )
            if not isinstance(stage, str) or not stage:
                raise DomainError(f"{where}: stage must be a non-empty string")
            names.append(stage)
            parsed.append({
                "stage": stage,
                "skills": _unique_strings(phase["skills"], f"{where} skills"),
                "areas": _unique_strings(phase["areas"], f"{where} areas"),
            })
        counts = Counter(names)
        missing = sorted(set(expected) - set(names))
        duplicate = sorted(name for name, count in counts.items() if count > 1)
        unknown = sorted(set(names) - set(expected))
        if missing or duplicate or unknown:
            raise DomainError(
                "decomposition phases mismatch: "
                f"missing={missing}; duplicate={duplicate}; unknown={unknown}"
            )
        integration = deepcopy(declaration["integration"])
        if declaration["kind"] == "ordinary":
            if integration is not None:
                raise DomainError("ordinary task integration must be null")
        else:
            exact_keys(
                integration,
                {"component_inputs", "combined_result", "integration_checks", "allowed_paths"},
                "integration",
            )
            missing_fields = []
            for field in ("component_inputs", "combined_result", "integration_checks", "allowed_paths"):
                value = integration[field]
                valid = (
                    isinstance(value, str) and bool(value.strip())
                    if field == "combined_result"
                    else isinstance(value, list)
                    and bool(value)
                    and all(isinstance(item, str) and item.strip() for item in value)
                    and len(value) == len(set(value))
                )
                if not valid:
                    missing_fields.append(field)
            if missing_fields:
                raise DomainError(
                    f"integration task requires non-empty fields: {sorted(missing_fields)}"
                )
        return cls(declaration["kind"], tuple(parsed), integration)

    @staticmethod
    def validate_policy(policy):
        exact_keys(policy, {"skills", "areas"}, "task_decomposition")
        if not isinstance(policy["skills"], list) or not policy["skills"]:
            raise DomainError("task_decomposition.skills must be a non-empty list")
        skills = {}
        for item in policy["skills"]:
            exact_keys(item, {"id", "class", "responsibility"}, "task_decomposition skill")
            skill_id = item["id"]
            skill_class = item["class"]
            responsibility = item["responsibility"]
            if not isinstance(skill_id, str) or not skill_id or skill_id in skills:
                raise DomainError("task_decomposition skill ids must be unique strings")
            if skill_class not in ("meta", "general", "narrow"):
                raise DomainError("task_decomposition skill class must be meta, general or narrow")
            if skill_class == "narrow":
                if not isinstance(responsibility, str) or not responsibility:
                    raise DomainError("narrow skill requires responsibility")
            elif responsibility is not None:
                raise DomainError("meta and general skill responsibility must be null")
            skills[skill_id] = (skill_class, responsibility)
        if not isinstance(policy["areas"], list) or not policy["areas"]:
            raise DomainError("task_decomposition.areas must be a non-empty list")
        areas = {}
        for item in policy["areas"]:
            exact_keys(item, {"path", "responsibility"}, "task_decomposition area")
            path, responsibility = item["path"], item["responsibility"]
            if (
                not isinstance(path, str)
                or not path
                or path in areas
                or not isinstance(responsibility, str)
                or not responsibility
            ):
                raise DomainError("task_decomposition areas require unique paths and responsibilities")
            areas[path] = responsibility
        return {"skills": skills, "areas": areas}

    def validate(self, policy):
        routed = self.validate_policy(policy)
        declared_skills = sorted({skill for phase in self.phases for skill in phase["skills"]})
        unknown_skills = sorted(set(declared_skills) - set(routed["skills"]))
        if unknown_skills:
            raise DomainError(f"unknown decomposition skills: {unknown_skills}")
        declared_areas = sorted({area for phase in self.phases for area in phase["areas"]})
        unknown_areas = sorted(set(declared_areas) - set(routed["areas"]))
        if unknown_areas:
            raise DomainError(f"unrouted decomposition areas: {unknown_areas}")
        focused_areas = sorted({
            area
            for phase in self.phases
            if any(
                routed["skills"][skill][0] == "narrow"
                for skill in phase["skills"]
            )
            for area in phase["areas"]
        })
        if self.kind == "ordinary":
            narrow = sorted(
                f"{skill}:{routed['skills'][skill][1]}"
                for skill in declared_skills
                if routed["skills"][skill][0] == "narrow"
            )
            if len({item.split(":", 1)[1] for item in narrow}) > 1:
                raise DomainError(f"ordinary task combines narrow responsibilities: skills={narrow}")
            area_routes = sorted(f"{area}:{routed['areas'][area]}" for area in focused_areas)
            if len({item.rsplit(":", 1)[1] for item in area_routes}) > 1:
                raise DomainError(f"ordinary task combines unrelated areas: areas={area_routes}")
        else:
            allowed = self.integration["allowed_paths"]
            outside = sorted(
                area for area in focused_areas if not any(_contains(path, area) for path in allowed)
            )
            if outside:
                raise DomainError(f"integration areas outside allowed_paths: {outside}")
        return self
