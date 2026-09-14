"""Pure, catalog-backed validation of focused Task decomposition declarations."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass

from ..foundation.errors import DomainError
from ..skills.catalog import SkillCatalog
from ..verification.domain import exact_keys


def _unique_strings(value, where):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(value) != len(set(value))):
        raise DomainError(f"{where}: requires unique strings")
    return tuple(sorted(value))


def _valid_pattern(value):
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and not any(ord(char) < 32 or char in "\\:" for char in value)
            and all(part not in ("", ".", "..") for part in value.split("/")))


def _contains(pattern, area):
    """Conservative containment of declared globs, not glob-on-glob matching.

    Equality, the explicit whole-tree pattern, and literal directory/** parents
    prove containment. More complex patterns require an exact declaration; an
    unproved relation must not silently grant a broader write scope.
    """
    if pattern == "**" or pattern == area:
        return True
    if pattern.endswith("/**") and not any(char in pattern[:-3] for char in "*?["):
        return area.startswith(pattern[:-3] + "/")
    return False


def _issue(code, location, message):
    return {"code": code, "location": location, "message": message}


def _ordered(issues):
    unique = {(item["location"], item["code"], item["message"]) for item in issues}
    return tuple(_issue(code, location, message) for location, code, message in sorted(unique))


class DecompositionError(DomainError):
    """All independently detectable defects, usable by current Task/Sprint owners."""
    def __init__(self, diagnostics):
        self.diagnostics = _ordered(diagnostics)
        super().__init__("\n".join(item["message"] for item in self.diagnostics))


@dataclass(frozen=True)
class FocusedDecomposition:
    kind: str
    phases: tuple[dict, ...]
    integration: dict | None

    @classmethod
    def _parse(cls, declaration, stages):
        issues = []
        expected = ()
        try:
            if not isinstance(stages, (list, tuple)) or not stages:
                raise DomainError("process stages: requires a non-empty list of stage IDs")
            expected = _unique_strings(list(stages), "process stages")
        except DomainError as exc:
            issues.append(_issue("process.stages", "process.stages", str(exc)))
        if not isinstance(declaration, dict):
            issues.append(_issue("decomposition.fields", "decomposition", "decomposition: ожидается объект"))
            return cls("", (), None), issues
        try:
            exact_keys(declaration, {"kind", "phases", "integration"}, "decomposition")
        except DomainError as exc:
            issues.append(_issue("decomposition.fields", "decomposition", str(exc)))
        kind = declaration.get("kind")
        if kind not in ("ordinary", "integration"):
            issues.append(_issue("decomposition.kind", "decomposition.kind",
                                 "decomposition kind must be ordinary or integration"))
            kind = ""
        phases = declaration.get("phases")
        if not isinstance(phases, list):
            issues.append(_issue("phases.type", "decomposition.phases", "decomposition phases must be a list"))
            phases = []
        parsed, names = [], []
        for index, phase in enumerate(phases):
            if not isinstance(phase, dict):
                issues.append(_issue("phase.fields", f"decomposition.phases[{index}]",
                                     f"decomposition phase {index}: expected object"))
                continue
            stage = phase.get("stage")
            valid_stage = isinstance(stage, str) and bool(stage.strip())
            label = stage if valid_stage else index
            where, location = f"decomposition phase {label}", f"decomposition.phases[{label}]"
            phase_fields = {"stage", "skills", "areas"}
            missing, extra = sorted(phase_fields - set(phase)), sorted(set(phase) - phase_fields)
            if missing or extra:
                issues.append(_issue("phase.fields", location,
                                     f"{where}: отсутствуют {missing}; неизвестные поля {extra}"))
            if not valid_stage:
                issues.append(_issue("phase.stage", location, f"{where}: stage must be a non-empty string"))
            else:
                names.append(stage)
            values = {}
            for field in ("skills", "areas"):
                if field not in phase:
                    continue  # Already diagnosed by the exact field check.
                try:
                    values[field] = _unique_strings(phase[field], f"{where} {field}")
                    if field == "areas":
                        invalid = [item for item in values[field] if not _valid_pattern(item)]
                        if invalid:
                            raise DomainError(f"{where} areas: noncanonical relative patterns: {invalid}")
                except DomainError as exc:
                    values.pop(field, None)
                    issues.append(_issue(f"phase.{field}", location, str(exc)))
            if valid_stage and set(values) == {"skills", "areas"}:
                parsed.append({"stage": stage, **values})
        counts = Counter(names)
        missing = sorted(set(expected) - set(names))
        duplicate = sorted(name for name, count in counts.items() if count > 1)
        unknown = sorted(set(names) - set(expected))
        if missing or duplicate or unknown:
            issues.append(_issue("phases.coverage", "decomposition.phases",
                                 f"decomposition phases mismatch: missing={missing}; duplicate={duplicate}; unknown={unknown}"))
        integration = deepcopy(declaration.get("integration"))
        if kind == "ordinary" and integration is not None:
            issues.append(_issue("ordinary.integration", "decomposition.integration",
                                 "ordinary task integration must be null"))
        if kind == "integration":
            fields = {"component_inputs", "combined_result", "integration_checks", "allowed_paths"}
            try:
                exact_keys(integration, fields, "integration")
            except DomainError as exc:
                issues.append(_issue("integration.fields", "decomposition.integration", str(exc)))
            if isinstance(integration, dict):
                for field in sorted(fields & set(integration)):
                    value = integration[field]
                    valid = (isinstance(value, str) and bool(value.strip())
                             if field == "combined_result" else
                             isinstance(value, list) and bool(value)
                             and all(isinstance(item, str) and item.strip() for item in value)
                             and len(value) == len(set(value)))
                    if not valid:
                        issues.append(_issue("integration.required", f"decomposition.integration.{field}",
                                             f"integration task requires non-empty fields: {[field]}"))
                    elif field == "allowed_paths":
                        invalid = sorted(item for item in value if not _valid_pattern(item))
                        if invalid:
                            issues.append(_issue("integration.paths", "decomposition.integration.allowed_paths",
                                                 f"integration allowed_paths: noncanonical relative patterns: {invalid}"))
        return cls(kind, tuple(sorted(parsed, key=lambda phase: phase["stage"])),
                   integration if isinstance(integration, dict) else None), issues

    @classmethod
    def parse(cls, declaration, stages):
        result, issues = cls._parse(declaration, stages)
        if issues:
            raise DecompositionError(issues)
        return result

    @staticmethod
    def validate_policy(policy):
        catalog_responsibilities = None
        if isinstance(policy, dict) and "catalog" in policy:
            exact_keys(policy, {"catalog", "skill_ids", "areas"}, "task_decomposition")
            catalog = SkillCatalog.parse(policy["catalog"])
            definitions = catalog.decomposition_skills(policy["skill_ids"])
            # An omitted selectable skill must not turn its known area into a
            # non-narrow area and silently evade the responsibility check.
            catalog_responsibilities = {s.responsibility for s in catalog.skills if s.skill_class == "working"}
        else:
            exact_keys(policy, {"skills", "areas"}, "task_decomposition")
            definitions = policy["skills"]
        if not isinstance(definitions, list) or not definitions:
            raise DomainError("task_decomposition.skills must be a non-empty list")
        skills = {}
        for item in definitions:
            exact_keys(item, {"id", "class", "responsibility"}, "task_decomposition skill")
            skill_id, skill_class, responsibility = item["id"], item["class"], item["responsibility"]
            if not isinstance(skill_id, str) or not skill_id.strip() or skill_id in skills:
                raise DomainError("task_decomposition skill ids must be unique strings")
            if skill_class not in ("meta", "general", "narrow"):
                raise DomainError("task_decomposition skill class must be meta, general or narrow")
            if skill_class == "narrow":
                if not isinstance(responsibility, str) or not responsibility.strip():
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
            if (not _valid_pattern(path) or path in areas
                    or not isinstance(responsibility, str) or not responsibility.strip()):
                raise DomainError("task_decomposition areas require unique canonical relative paths and responsibilities")
            areas[path] = responsibility
        narrow = ({responsibility for kind, responsibility in skills.values() if kind == "narrow"}
                  if catalog_responsibilities is None else catalog_responsibilities)
        return {"skills": skills, "areas": areas, "narrow_responsibilities": narrow}

    def _diagnostics(self, routed):
        issues = []
        skills, areas = routed["skills"], routed["areas"]
        declared_skills = {skill for phase in self.phases for skill in phase["skills"]}
        declared_areas = {area for phase in self.phases for area in phase["areas"]}
        unknown_skills, unknown_areas = sorted(declared_skills - set(skills)), sorted(declared_areas - set(areas))
        if unknown_skills:
            issues.append(_issue("skills.unknown", "decomposition",
                                 f"unknown decomposition skills: {unknown_skills}"))
        if unknown_areas:
            issues.append(_issue("areas.unknown", "decomposition",
                                 f"unrouted decomposition areas: {unknown_areas}"))
        narrow_policy = routed["narrow_responsibilities"]

        def responsibility_sets(phase_skills, phase_areas):
            narrow = {skill: skills[skill][1] for skill in phase_skills
                      if skill in skills and skills[skill][0] == "narrow"}
            focused = {area: areas[area] for area in phase_areas
                       if area in areas and areas[area] in narrow_policy}
            return narrow, focused

        narrow, focused = responsibility_sets(declared_skills, declared_areas)
        skill_owners, area_owners = set(narrow.values()), set(focused.values())
        if self.kind == "ordinary":
            if len(skill_owners) > 1:
                detail = sorted(f"{skill}:{owner}" for skill, owner in narrow.items())
                issues.append(_issue("ordinary.skills", "decomposition",
                                     f"ordinary task combines narrow responsibilities: skills={detail}"))
            if len(area_owners) > 1:
                detail = sorted(f"{area}:{owner}" for area, owner in focused.items())
                issues.append(_issue("ordinary.areas", "decomposition",
                                     f"ordinary task combines unrelated areas: areas={detail}"))
        if area_owners - skill_owners:
            issues.append(_issue("task.responsibility", "decomposition",
                                 f"{self.kind} task narrow skill and area responsibilities mismatch: "
                                 f"skills={sorted(skill_owners)}; areas={sorted(area_owners)}"))
        for phase in self.phases:
            stage = phase["stage"]
            location = f"decomposition.phases[{stage}]"
            phase_skills, phase_areas = responsibility_sets(phase["skills"], phase["areas"])
            ps, pa = set(phase_skills.values()), set(phase_areas.values())
            if pa - ps:
                issues.append(_issue("phase.responsibility", location,
                                     f"decomposition phase {stage}: narrow skill and area responsibilities mismatch: "
                                     f"skills={sorted(ps)}; areas={sorted(pa)}"))
            if self.kind == "ordinary":
                if len(ps) > 1:
                    issues.append(_issue("phase.skills", location,
                                         f"decomposition phase {stage}: combines narrow responsibilities: {sorted(ps)}"))
                if len(pa) > 1:
                    issues.append(_issue("phase.areas", location,
                                         f"decomposition phase {stage}: combines unrelated areas: {sorted(pa)}"))
        if self.kind == "integration" and self.integration is not None:
            allowed = self.integration.get("allowed_paths")
            if isinstance(allowed, list) and allowed and all(_valid_pattern(path) for path in allowed):
                outside = sorted(area for area in declared_areas if not any(_contains(path, area) for path in allowed))
                if outside:
                    issues.append(_issue("integration.scope", "decomposition.integration.allowed_paths",
                                         f"integration areas outside allowed_paths: {outside}"))
        return issues

    @classmethod
    def _checked(cls, declaration, stages, policy):
        result, issues = cls._parse(declaration, stages)
        try:
            routed = cls.validate_policy(policy)
        except DomainError as exc:
            issues.append(_issue("policy.invalid", "task_decomposition", str(exc)))
        else:
            issues.extend(result._diagnostics(routed))
        return result, _ordered(issues)

    @classmethod
    def inspect(cls, declaration, stages, policy):
        """Return all independent diagnostics; skip checks needing malformed data."""
        return cls._checked(declaration, stages, policy)[1]

    @classmethod
    def require_valid(cls, declaration, stages, policy):
        result, issues = cls._checked(declaration, stages, policy)
        if issues:
            raise DecompositionError(issues)
        return result

    def validate(self, policy):
        issues = self._diagnostics(self.validate_policy(policy))
        if issues:
            raise DecompositionError(issues)
        return self
