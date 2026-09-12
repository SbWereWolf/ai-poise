"""Pure independent process definition; declarative changes, not raw JSON Patch."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from ..foundation.errors import DomainError
from ..workflow.domain import RouteDefinition
from ..content.domain import SectionRule
from ..content_requirements.domain import ContentPolicy

# Structural protocol vocabulary, not task-stage policy or runtime defaults.
PROCESS_FIELDS = frozenset({"goal_type", "stages", "content_contract", "route", "benefit"})
STAGE_FIELDS = frozenset({"id", "instruction", "read_only", "allowed_paths", "normalization",
    "sections", "required_sections", "artifact_requirements", "handler", "transitions", "rework_targets"})
ROUTE_FIELDS = frozenset({"entry"})
GROUPS = {"stage": "stages", "section": "sections", "trace_route": "routes", "requirement": "requirements"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def problem(path, code, message):
    return {"path": path, "code": code, "message": message}


class BatchValidationError(DomainError):
    def __init__(self, issues):
        self.issues = tuple(dict(i) for i in issues)
        super().__init__("; ".join(f"{i['path']}: {i['message']}" for i in self.issues))


def require_shape(value, fields, where):
    if not isinstance(value, dict) or set(value) != fields:
        raise DomainError(f"{where}: требуется точный набор полей {sorted(fields)}")


def names(value, where, *, nonempty):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise DomainError(f"{where}: требуется явный список непустых строк")
    if len(set(value)) != len(value) or (nonempty and not value):
        raise DomainError(f"{where}: повтор или недопустимый пустой список")


def validate_stage(stage):
    require_shape(stage, STAGE_FIELDS, "stage")
    for key in ("id", "instruction", "handler"):
        if not isinstance(stage[key], str) or not stage[key].strip():
            raise DomainError(f"{key}: требуется непустая строка")
    if type(stage["read_only"]) is not bool:
        raise DomainError("read_only: нужен явный bool")
    names(stage["allowed_paths"], "allowed_paths", nonempty=False)
    names(stage["required_sections"], "required_sections", nonempty=False)
    names(stage["rework_targets"], "rework_targets", nonempty=False)
    if stage["normalization"] not in ("strip", "exact"):
        raise DomainError("Требуется явная normalization strip или exact")
    if not isinstance(stage["sections"], dict):
        raise DomainError("sections: требуется объект")
    for name, text in stage["sections"].items():
        SectionRule(name, text, stage["normalization"], name in stage["required_sections"])
    if set(stage["required_sections"]) - stage["sections"].keys():
        raise DomainError("Обязательная секция не объявлена")
    if not isinstance(stage["artifact_requirements"], list):
        raise DomainError("artifact_requirements: нужен явный список")
    for rule in stage["artifact_requirements"]:
        require_shape(rule, {"scope", "pattern", "minimum", "maximum"}, "artifact requirement")
        if rule["scope"] not in ("runtime", "task", "sprint") or not isinstance(rule["pattern"], str) or not rule["pattern"]:
            raise DomainError("Неизвестный scope/пустой pattern артефактов")
        if (type(rule["minimum"]) is not int or type(rule["maximum"]) is not int
                or not 0 <= rule["minimum"] <= rule["maximum"]):
            raise DomainError("Неверные границы количества артефактов")


def validate_process(data):
    """Validate independent pack; task-specific IDs are resolved again at task creation."""
    issues = []
    try:
        require_shape(data, PROCESS_FIELDS, "process")
        if not isinstance(data["goal_type"], str) or not data["goal_type"].strip():
            raise DomainError("goal_type: непустое имя")
        if not isinstance(data["stages"], list) or not data["stages"]:
            raise DomainError("Нужен непустой список этапов")
    except DomainError as exc:
        raise BatchValidationError([problem("process", "shape", str(exc))]) from exc
    from ..accounting.domain import BenefitDefinition
    try: BenefitDefinition.parse(data["benefit"])
    except DomainError as exc: issues.append(problem("benefit", "benefit_contract", str(exc)))
    for index, stage in enumerate(data["stages"]):
        try:
            validate_stage(stage)
        except DomainError as exc:
            issues.append(problem(f"stages[{index}]", "stage_contract", str(exc)))
    if issues:
        raise BatchValidationError(issues)
    try:
        RouteDefinition.from_process(data)
    except DomainError as exc:
        issues.append(problem("route", "route_contract", str(exc)))
    try:
        contract = data["content_contract"]
        require_shape(contract, {"sections", "routes", "requirements"}, "content_contract")
        if any(not isinstance(contract[k], list) for k in contract):
            raise DomainError("content_contract: группы должны быть списками")
        refs = set()
        for route in contract["routes"]:
            if not isinstance(route, dict) or "requirements" not in route:
                raise DomainError("У маршрута нет requirements")
            names(route["requirements"], "route.requirements", nonempty=True)
            refs.update(route["requirements"])
        # Pack validates shape and internal links, not existence of future task requirements.
        ContentPolicy.from_layers(contract, {"sections": [], "routes": [], "requirements": []},
            tuple(s["id"] for s in data["stages"]), tuple(sorted(refs)), (),
            tuple(sorted({n for s in data["stages"] for n in s["sections"]})))
    except DomainError as exc:
        issues.append(problem("content_contract", "content_contract", str(exc)))
    if issues:
        raise BatchValidationError(issues)


@dataclass(frozen=True)
class GoalTypeDefinition:
    document: str

    @property
    def data(self):
        return json.loads(self.document)

    @property
    def revision(self):
        return fingerprint(self.data)

    @classmethod
    def parse(cls, data):
        validate_process(data)
        return cls(canonical(data))

    @classmethod
    def build(cls, goal_type, source, changes):
        if not isinstance(goal_type, str) or not goal_type.strip():
            raise BatchValidationError([problem("goal_type", "shape", "Нужно явное имя типа цели")])
        if not isinstance(changes, list):
            raise BatchValidationError([problem("changes", "shape", "Нужен явный список изменений")])
        data = deepcopy(source)
        if not isinstance(data, dict):
            raise BatchValidationError([problem("process", "shape", "Нужен полный объект шаблона/конфига")])
        # Owner identity is the explicitly requested goal type, not an inferred default.
        data["goal_type"] = goal_type
        groups, issues = {}, []
        try:
            groups["stage"] = {s["id"]: deepcopy(s) for s in data["stages"]}
            for kind, group in GROUPS.items():
                values = data["stages"] if kind == "stage" else data["content_contract"][group]
                if not isinstance(values, list) or len({v["id"] for v in values}) != len(values):
                    raise DomainError("Объекты должны иметь уникальные ID")
                groups[kind] = {v["id"]: deepcopy(v) for v in values}
        except (KeyError, TypeError, DomainError) as exc:
            raise BatchValidationError([problem("process", "shape", f"Неполный исходный контракт: {exc}")]) from exc
        touched = {}
        prepared = []
        for index, change in enumerate(changes):
            label = f"changes[{index}]"
            try:
                if not isinstance(change, dict) or not isinstance(change.get("op"), str):
                    raise DomainError("Нужна именованная предметная операция")
                op = change["op"]
                if op == "set_benefit":
                    require_shape(change,{"op","value"},op)
                    from ..accounting.domain import BenefitDefinition
                    BenefitDefinition.parse(change["value"])
                    kind=key="benefit";marks={(kind,key,"*")}
                elif op in ("patch_stage", "patch_route"):
                    require_shape(change, {"op", "id", "set"} if op == "patch_stage" else {"op", "set"}, op)
                    values = change["set"]
                    allowed = STAGE_FIELDS - {"id"} if op == "patch_stage" else ROUTE_FIELDS
                    if not isinstance(values, dict) or not values or set(values) - allowed:
                        raise DomainError("patch: нужны непустые известные свойства без изменения ID")
                    kind = "stage" if op == "patch_stage" else "route"
                    key = change["id"] if op == "patch_stage" else "route"
                    marks = {(kind, key, field) for field in values}
                else:
                    parts = op.split("_", 1)
                    if len(parts) != 2 or parts[0] not in ("put", "remove") or parts[1] not in GROUPS:
                        raise DomainError(f"Неизвестная операция {op}")
                    verb, kind = parts
                    require_shape(change, {"op", "value"} if verb == "put" else {"op", "id"}, op)
                    if verb == "put":
                        if not isinstance(change["value"], dict) or "id" not in change["value"]:
                            raise DomainError("put: нужен полный предметный объект с id")
                        key = change["value"]["id"]
                    else:
                        key = change["id"]
                    marks = {(kind, key, "*")}
                if not isinstance(key, str) or not key.strip():
                    raise DomainError("Нужен непустой ID объекта")
                identity = (kind, key)
                prior = touched[identity] if identity in touched else set()
                fields = {field for _, _, field in marks}
                if prior and ("*" in prior or "*" in fields or fields & prior):
                    issues.append(problem(label, "conflicting_change", f"Пересекающиеся изменения {kind}:{key}"))
                touched[identity] = prior | fields
                prepared.append((label, change, kind, key))
            except (DomainError, TypeError) as exc:
                issues.append(problem(label, "change_contract", str(exc)))
        if issues:
            raise BatchValidationError(issues)
        # Apply declarations first so references can precede targets in the request.
        for label, change, kind, key in prepared:
            op = change["op"]
            if op.startswith("put_"):
                groups[kind][key] = deepcopy(change["value"])
        for label, change, kind, key in prepared:
            op = change["op"]
            if op == "set_benefit":
                data["benefit"]=deepcopy(change["value"])
            elif op == "patch_route":
                if not isinstance(data.get("route"), dict):
                    issues.append(problem(label, "missing_object", "route не объявлен"))
                else:
                    data["route"].update(deepcopy(change["set"]))
            elif op == "patch_stage":
                if key not in groups[kind]:
                    issues.append(problem(label, "missing_object", f"Этап {key} не объявлен"))
                else:
                    groups[kind][key].update(deepcopy(change["set"]))
            elif op.startswith("remove_"):
                if key not in groups[kind]:
                    issues.append(problem(label, "missing_object", f"{kind}:{key} не объявлен"))
                else:
                    del groups[kind][key]
        if issues:
            raise BatchValidationError(issues)
        # Stage order is presentation only. Stable order makes batch order irrelevant.
        stage_order = [v["id"] for v in source["stages"] if v["id"] in groups["stage"]]
        stage_order.extend(sorted(groups["stage"].keys() - set(stage_order)))
        data["stages"] = [groups["stage"][k] for k in stage_order]
        for kind, group in GROUPS.items():
            if kind != "stage":
                order = [v["id"] for v in source["content_contract"][group] if v["id"] in groups[kind]]
                order.extend(sorted(groups[kind].keys() - set(order)))
                data["content_contract"][group] = [groups[kind][k] for k in order]
        return cls.parse(data)
