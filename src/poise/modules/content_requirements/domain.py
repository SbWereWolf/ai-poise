from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
from ..content.domain import ContentState, SectionRule, SectionValue
from ..foundation.errors import DomainError


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise DomainError(f"{label}: требуется точный набор полей {sorted(keys)}")
    return value


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{label}: требуется непустая строка")
    return value


def _names(value: object, label: str, allowed: tuple | None, nonempty: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DomainError(f"{label}: требуется список")
    values = tuple(_name(x, label) for x in value)
    if len(values) != len(set(values)) or (nonempty and not values):
        raise DomainError(f"{label}: пустой список или повтор идентификатора")
    if allowed is not None and set(values) - set(allowed):
        raise DomainError(f"{label}: неизвестные ссылки {sorted(set(values)-set(allowed))}")
    return values


def _artifact_source(value: object, stages: tuple[str, ...]) -> dict:
    if not isinstance(value, dict) or "kind" not in value:
        raise DomainError("artifact source: требуется явный kind")
    kind = value["kind"]
    fields = {
        "preexisting": {"kind"},
        "stage_output": {"kind", "producer_stage"},
        "declared_arrival": {"kind", "arrival_stage"},
    }
    if kind not in fields or set(value) != fields[kind]:
        raise DomainError("artifact source: неверный вид или набор полей")
    stage = value.get("producer_stage", value.get("arrival_stage"))
    if stage is not None and stage not in stages:
        raise DomainError("artifact source: неизвестный этап")
    return dict(value)


@dataclass(frozen=True)
class ArtifactFact:
    """Filesystem observations supplied by the adapter, never by the LLM."""
    id: str
    scope: str
    relative_path: str

    def __post_init__(self) -> None:
        _name(self.id, "artifact ID")
        _name(self.relative_path, "artifact path")
        if self.scope not in ("runtime", "task", "sprint"):
            raise DomainError("Неизвестный уровень артефакта")


def count_artifacts(facts: tuple[ArtifactFact, ...], scope: str, pattern: str) -> int:
    return len({(a.scope, a.relative_path) for a in facts
                if a.scope == scope and fnmatch.fnmatchcase(a.relative_path, pattern)})


@dataclass(frozen=True)
class TraceValue:
    route_id: str
    point_id: str
    data: str  # canonical immutable JSON; null is a tombstone, not a fallback

    @property
    def value(self):
        return json.loads(self.data)


@dataclass(frozen=True)
class ContentSnapshot:
    sections: tuple[SectionValue, ...]
    trace: tuple[TraceValue, ...]

    def section_map(self) -> dict[str, SectionValue]:
        return {s.name: s for s in self.sections}

    def trace_map(self) -> dict[tuple[str, str], object]:
        return {(v.route_id, v.point_id): v.value for v in self.trace}

    def with_sections(self, values: tuple[SectionValue, ...]) -> ContentSnapshot:
        sections = self.section_map()
        sections.update((v.name, v) for v in values)
        return ContentSnapshot(tuple(sections[k] for k in sorted(sections)), self.trace)

    def trace_view(self) -> dict:
        view = {}
        for value in self.trace:
            view.setdefault(value.route_id, {})[value.point_id] = value.value
        return view


@dataclass(frozen=True)
class ExtraSection:
    rule: SectionRule
    write_stages: tuple[str, ...]


@dataclass(frozen=True)
class TracePoint:
    id: str
    kind: str
    fields: tuple[tuple[str, tuple[str, ...]], ...]
    write_stages: tuple[str, ...]

    def validate(self, value: object, method_ids: tuple[str, ...]) -> None:
        # Incomplete content is stored, but cannot satisfy a due requirement.
        if value is None:
            return
        if self.kind == "record":
            fields = dict(self.fields)
            if not isinstance(value, dict) or set(value) - fields.keys():
                raise DomainError(f"Точка {self.id}: неизвестные поля/неверный record")
            for key, text in value.items():
                if not isinstance(text, str):
                    raise DomainError(f"Точка {self.id}.{key}: требуется текст")
                if text.strip() and fields[key] and text not in fields[key]:
                    raise DomainError(f"Точка {self.id}.{key}: неизвестное значение")
        elif not isinstance(value, str):
            raise DomainError(f"Точка {self.id}: требуется текст/ссылка на метод")
        elif self.kind == "method" and value.strip() and value not in method_ids:
            raise DomainError(f"Точка {self.id}: неизвестный точный метод {value}")

    def complete(self, value: object) -> bool:
        if self.kind == "record":
            return (isinstance(value, dict) and set(value) == {k for k, _ in self.fields}
                    and all(isinstance(x, str) and x.strip() for x in value.values()))
        return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class TraceRoute:
    id: str
    requirements: tuple[str, ...]
    points: tuple[TracePoint, ...]


@dataclass(frozen=True)
class Requirement:
    id: str
    origin: str
    kind: str
    stages: tuple[str, ...]
    phase: str
    details: str  # immutable validated predicate, no eval/expressions


@dataclass(frozen=True)
class RequirementResult:
    rule_id: str
    origin: str
    target: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"id": self.rule_id, "origin": self.origin, "target": self.target,
                "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class Assessment:
    stage: str
    phase: str
    results: tuple[RequirementResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def require(self) -> None:
        if not self.passed:
            raise DomainError("Не выполнены требования содержимого: " + "; ".join(
                f"{r.rule_id}: {r.detail}" for r in self.results if not r.passed))

    def to_dict(self) -> dict:
        return {"stage": self.stage, "phase": self.phase, "passed": self.passed,
                "requirements": [r.to_dict() for r in self.results]}


@dataclass(frozen=True)
class ContentPolicy:
    """Immutable policy owned by Task; mechanics do not know any goal_type.

    Goal minima and task declarations are additive. No policy override, guessed
    stage names, filesystem access or logical truth judgement is performed here.
    """
    goal_json: str
    task_json: str
    stages: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    method_ids: tuple[str, ...]
    standard_sections: tuple[str, ...]
    sections: tuple[ExtraSection, ...]
    routes: tuple[TraceRoute, ...]
    requirements: tuple[Requirement, ...]

    @classmethod
    def from_layers(cls, goal: dict, task: dict, stages: tuple[str, ...],
                    requirement_ids: tuple[str, ...], method_ids: tuple[str, ...],
                    standard_sections: tuple[str, ...]) -> ContentPolicy:
        policy = cls._parse_layers(
            goal, task, stages, requirement_ids, method_ids, standard_sections, True)
        policy._require_schedulable_trace_requirements(
            frozenset(r.id for r in policy.requirements if r.kind == "trace"))
        return policy

    @classmethod
    def restore_layers(cls, goal: dict, task: dict, stages: tuple[str, ...],
                       requirement_ids: tuple[str, ...], method_ids: tuple[str, ...],
                       standard_sections: tuple[str, ...]) -> ContentPolicy:
        """Structurally restore persisted layers without applying newer creation rules."""
        return cls._parse_layers(
            goal, task, stages, requirement_ids, method_ids, standard_sections, False)

    @classmethod
    def _parse_layers(cls, goal: dict, task: dict, stages: tuple[str, ...],
                      requirement_ids: tuple[str, ...], method_ids: tuple[str, ...],
                      standard_sections: tuple[str, ...], require_artifact_source: bool) -> ContentPolicy:
        for label, values in (("stages", stages), ("task requirements", requirement_ids),
                              ("methods", method_ids), ("standard sections", standard_sections)):
            if type(values) is not tuple:
                raise DomainError(f"{label}: нужен явный неизменный набор")
            for v in values:
                _name(v, label)
            if len(set(values)) != len(values):
                raise DomainError(f"{label}: повтор идентификатора")
        if not stages:
            raise DomainError("Требуется маршрут этапов")
        layers = (("goal", goal), ("task", task))
        sections, routes, requirements = [], [], []
        section_ids, route_ids, rule_ids = set(standard_sections), set(), set()
        for origin, layer in layers:
            _object(layer, {"sections", "routes", "requirements"}, f"{origin} content contract")
            for key in ("sections", "routes", "requirements"):
                if not isinstance(layer[key], list):
                    raise DomainError(f"{origin}.{key}: требуется список")
            for raw in layer["sections"]:
                _object(raw, {"id", "template", "normalization", "write_stages"}, "section definition")
                name = _name(raw["id"], "section id")
                if name in section_ids:
                    raise DomainError(f"Повтор/замена определения секции {name}")
                section_ids.add(name)
                sections.append(ExtraSection(SectionRule(name, raw["template"], raw["normalization"], False),
                    _names(raw["write_stages"], "section write stages", stages, True)))
            for raw in layer["routes"]:
                _object(raw, {"id", "requirements", "points"}, "trace route")
                rid = _name(raw["id"], "route id")
                if rid in route_ids:
                    raise DomainError(f"Повтор/замена маршрута {rid}")
                route_ids.add(rid)
                refs = _names(raw["requirements"], "task requirement refs", requirement_ids, True)
                if not isinstance(raw["points"], list) or not raw["points"]:
                    raise DomainError("Маршрут должен перечислять точки")
                points, point_ids = [], set()
                for p in raw["points"]:
                    _object(p, {"id", "kind", "fields", "write_stages"}, "trace point")
                    pid = _name(p["id"], "point id")
                    if pid in point_ids or p["kind"] not in ("text", "method", "record"):
                        raise DomainError("Повтор точки или неизвестный вид точки")
                    point_ids.add(pid)
                    if not isinstance(p["fields"], dict):
                        raise DomainError("fields должны быть заданы явно")
                    fields = tuple((_name(k, "field id"), _names(v, "allowed values", None, False))
                                   for k, v in sorted(p["fields"].items()))
                    if (p["kind"] == "record") != bool(fields):
                        raise DomainError("record требует поля; text/method требуют fields={}")
                    points.append(TracePoint(pid, p["kind"], fields,
                        _names(p["write_stages"], "point write stages", stages, True)))
                routes.append(TraceRoute(rid, refs, tuple(points)))
        route_map = {r.id: r for r in routes}
        for origin, layer in layers:
            for raw in layer["requirements"]:
                if not isinstance(raw, dict) or "kind" not in raw:
                    raise DomainError("Не определён вид требования содержимого")
                kind = raw["kind"]
                artifact_fields = {"scope", "pattern", "minimum", "maximum", "source"}
                if not require_artifact_source and "source" not in raw:
                    artifact_fields = artifact_fields - {"source"}
                variants = {"section": {"section", "states"},
                            "trace": {"route", "point", "field_equals"},
                            "artifact": artifact_fields,
                            "coverage": {"points"}}
                if kind not in variants:
                    raise DomainError("Неизвестный вид требования содержимого")
                _object(raw, {"id", "kind", "stages", "phase"} | variants[kind], "content requirement")
                rid = _name(raw["id"], "rule id")
                if rid in rule_ids:
                    raise DomainError(f"Повтор/замена обязательства {rid}")
                rule_ids.add(rid)
                due = _names(raw["stages"], "requirement stages", stages, True)
                if raw["phase"] not in ("pre", "post"):
                    raise DomainError("Фаза требования должна быть явно pre или post")
                details = {k: raw[k] for k in variants[kind]}
                if kind == "section":
                    if raw["section"] not in section_ids:
                        raise DomainError("Обязательная секция не объявлена")
                    _names(raw["states"], "section states", tuple(x.value for x in ContentState), True)
                elif kind == "artifact":
                    if raw["scope"] not in ("runtime", "task", "sprint"):
                        raise DomainError("Неизвестная область артефактов")
                    _name(raw["pattern"], "artifact pattern")
                    if (type(raw["minimum"]) is not int or type(raw["maximum"]) is not int
                            or not 0 <= raw["minimum"] <= raw["maximum"]):
                        raise DomainError("Неверные явные границы количества артефактов")
                    if "source" in raw:
                        _artifact_source(raw["source"], stages)
                elif kind == "coverage":
                    _names(raw["points"], "coverage checkpoints", None, False)
                else:
                    if raw["route"] not in route_map:
                        raise DomainError("Неизвестный маршрут трассировки")
                    points = {p.id: p for p in route_map[raw["route"]].points}
                    if raw["point"] not in points:
                        raise DomainError("Неизвестная точка трассировки")
                    point = points[raw["point"]]
                    constraints = raw["field_equals"]
                    fields = dict(point.fields)
                    if not isinstance(constraints, dict) or set(constraints) - fields.keys():
                        raise DomainError("field_equals ссылается на неизвестное поле record")
                    for key, val in constraints.items():
                        _name(val, "field_equals value")
                        if fields[key] and val not in fields[key]:
                            raise DomainError("field_equals требует запрещённое значение")
                requirements.append(Requirement(rid, origin, kind, due, raw["phase"], _json(details)))
        return cls(_json(goal), _json(task), stages, requirement_ids, method_ids, standard_sections,
                   tuple(sections), tuple(routes), tuple(requirements))

    def _require_schedulable_trace_requirements(self, selected: frozenset[str]) -> None:
        routes = {route.id: route for route in self.routes}
        for requirement in self.requirements:
            if requirement.kind != "trace" or requirement.id not in selected:
                continue
            details = json.loads(requirement.details)
            route = routes[details["route"]]
            point = next(point for point in route.points if point.id == details["point"])
            if set(requirement.stages).isdisjoint(point.write_stages):
                raise DomainError(
                    f"Требование {requirement.id}: маршрут {route.id}, точка {point.id}; "
                    f"write_stages={list(point.write_stages)}, "
                    f"due_stages={list(requirement.stages)} не пересекаются")

    def to_layers(self) -> dict:
        return {"goal": json.loads(self.goal_json), "task": json.loads(self.task_json)}

    def extend(self, additions: dict) -> ContentPolicy:
        _object(additions, {"sections", "routes", "requirements"}, "content additions")
        layers = self.to_layers()
        new_requirement_ids = set()
        for group in ("sections", "routes", "requirements"):
            if not isinstance(additions[group], list):
                raise DomainError(f"content additions.{group}: требуется список")
            known = {v["id"]: v for owner in ("goal", "task") for v in layers[owner][group]}
            seen = set()
            for value in additions[group]:
                if not isinstance(value, dict) or "id" not in value:
                    raise DomainError("Добавлению требуется id")
                name = _name(value["id"], "addition ID")
                if name in seen:
                    raise DomainError("Повтор ID в пакете additions")
                seen.add(name)
                if name in known:
                    if known[name] != value:
                        raise DomainError(f"Запрещена замена определения {name}; требуется решение о перепланировании")
                else:
                    layers["task"][group].append(value)
                    if group == "requirements":
                        new_requirement_ids.add(name)
        policy = self._parse_layers(
            layers["goal"], layers["task"], self.stages,
            self.requirement_ids, self.method_ids, self.standard_sections, True
        )
        policy._require_schedulable_trace_requirements(frozenset(new_requirement_ids))
        return policy

    def apply(self, snapshot: ContentSnapshot, stage: str, sections: dict,
              trace: dict) -> ContentSnapshot:
        if stage not in self.stages:
            raise DomainError("Неизвестный этап")
        if not isinstance(sections, dict) or not isinstance(trace, dict):
            raise DomainError("sections/trace должны быть объектами")
        rules = {s.rule.name: s for s in self.sections}
        section_map = snapshot.section_map()
        for name, text in sections.items():
            if name not in rules:
                raise DomainError(f"Необъявленная дополнительная секция {name}")
            extra = rules[name]
            if stage not in extra.write_stages:
                raise DomainError(f"Этап {stage} не может изменять секцию {name}")
            if not isinstance(text, str):
                raise DomainError(f"Секция {name} должна быть текстом")
            norm = extra.rule.normalize(text)
            state = (ContentState.EMPTY if norm == "" else ContentState.TEMPLATE
                     if norm == extra.rule.normalize(extra.rule.template) else ContentState.POPULATED)
            section_map[name] = SectionValue(name, text, state)
        routes = {r.id: r for r in self.routes}
        current = snapshot.trace_map()
        for rid, updates in trace.items():
            if rid not in routes or not isinstance(updates, dict):
                raise DomainError(f"Неизвестный маршрут/неверное обновление {rid}")
            route = routes[rid]
            if set(updates) - {p.id for p in route.points}:
                raise DomainError(f"Маршрут {rid}: неизвестная точка")
            for index, point in enumerate(route.points):
                if point.id not in updates:
                    continue
                if stage not in point.write_stages:
                    raise DomainError(f"Этап {stage} не может изменять точку {rid}/{point.id}")
                value = updates[point.id]
                point.validate(value, self.method_ids)
                key = (rid, point.id)
                if key not in current or current[key] != value:
                    # A route is an explicitly ordered proof chain. Clear only its
                    # downstream points; history persists in repository layers.
                    for later in route.points[index+1:]:
                        if (rid, later.id) in current:
                            current[(rid, later.id)] = None
                current[key] = value
        return ContentSnapshot(tuple(section_map[k] for k in sorted(section_map)),
            tuple(TraceValue(r, p, _json(v)) for (r, p), v in sorted(current.items())))

    def evaluate(self, stage: str, phase: str, snapshot: ContentSnapshot,
                 artifacts: tuple[ArtifactFact, ...]) -> Assessment:
        if stage not in self.stages or phase not in ("pre", "post"):
            raise DomainError("Неизвестный этап/фаза проверки содержимого")
        sections, trace = snapshot.section_map(), snapshot.trace_map()
        points = {(r.id, p.id): p for r in self.routes for p in r.points}
        results = []
        for rule in self.requirements:
            if stage not in rule.stages or phase != rule.phase:
                continue
            d = json.loads(rule.details)
            if rule.kind == "section":
                val = sections.get(d["section"])
                actual = "missing" if val is None else val.state.value
                passed = actual in d["states"]
                target = f"section:{d['section']}"
                detail = f"state={actual}; требуется {d['states']}"
            elif rule.kind == "artifact":
                count = count_artifacts(artifacts,d['scope'],d['pattern'])
                passed = d["minimum"] <= count <= d["maximum"]
                target = f"artifact:{d['scope']}:{d['pattern']}"
                detail = f"unique_files={count}; требуется {d['minimum']}..{d['maximum']}"
            elif rule.kind == "coverage":
                covered = set()
                for route in self.routes:
                    route_points = {p.id:p for p in route.points}
                    if all(pid in route_points and route_points[pid].complete(trace.get((route.id,pid)))
                           for pid in d['points']):
                        covered.update(route.requirements)
                missing = set(self.requirement_ids) - covered
                passed = not missing
                target = "coverage:task-requirements"
                detail = f"uncovered={sorted(missing)}; points={d['points']}"
            else:
                key = (d["route"], d["point"])
                value = trace.get(key)
                point = points[key]
                passed = point.complete(value)
                if passed and d["field_equals"]:
                    passed = all(value[k] == v for k, v in d["field_equals"].items())
                target = f"trace:{d['route']}/{d['point']}"
                detail = "complete" if passed else "точка отсутствует/неполна либо не удовлетворяет field_equals"
            results.append(RequirementResult(rule.id, rule.origin, target, passed, detail))
        return Assessment(stage, phase, tuple(results))

    def describe(self, stage: str, snapshot: ContentSnapshot) -> dict:
        if stage not in self.stages:
            raise DomainError("Неизвестный этап")
        return {
            "sections": [{"id": s.rule.name, "template": s.rule.template,
                          "writable": stage in s.write_stages} for s in self.sections],
            "routes": [{"id": r.id, "requirements": list(r.requirements),
                       "points": [{"id": p.id, "kind": p.kind, "fields": {k:list(v) for k,v in p.fields},
                                   "writable": stage in p.write_stages} for p in r.points]} for r in self.routes],
            "due": [{"id": r.id, "origin": r.origin, "phase": r.phase, "kind": r.kind,
                     **json.loads(r.details)} for r in self.requirements if stage in r.stages],
            "trace": snapshot.trace_view(),
            "sections_current": {s.name: s.content for s in snapshot.sections
                                 if s.name not in self.standard_sections},
        }
