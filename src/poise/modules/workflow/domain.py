"""Pure explicit route graph. No goal-type names, I/O or implicit next stage."""
from __future__ import annotations
from dataclasses import dataclass, replace
from enum import StrEnum
from ..foundation.errors import DomainError


class HandlerKind(StrEnum):
    PRODUCE = "produce"
    INSPECT = "inspect"
    REVISE = "revise"
    OBSERVE = "observe"
    CHECK = "check"
    APPLY_PLAN = "apply_plan"
    PUBLISH = "publish"


# Protocol vocabulary of the implemented handler families, not process policy.
OUTCOMES = {
    HandlerKind.APPLY_PLAN: frozenset({"complete"}),
    HandlerKind.PUBLISH: frozenset({"complete"}),
    HandlerKind.PRODUCE: frozenset({"complete"}),
    HandlerKind.INSPECT: frozenset({"clear", "changes_requested"}),
    HandlerKind.REVISE: frozenset({"complete"}),
    HandlerKind.OBSERVE: frozenset({"complete"}),
    HandlerKind.CHECK: frozenset({"satisfied", "not_satisfied", "inconclusive"}),
}

POSITIVE_OUTCOME = {
    HandlerKind.APPLY_PLAN: "complete",
    HandlerKind.PUBLISH: "complete",
    HandlerKind.PRODUCE: "complete",
    HandlerKind.INSPECT: "clear",
    HandlerKind.REVISE: "complete",
    HandlerKind.OBSERVE: "complete",
    HandlerKind.CHECK: "satisfied",
}


def exact(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DomainError(f"{where}: требуется точный набор полей {sorted(keys)}")


def text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{where}: требуется непустая строка")
    return value


@dataclass(frozen=True)
class RouteNode:
    stage_id: str
    handler: HandlerKind
    role: str
    transitions: tuple[tuple[str, str | None], ...]
    rework_targets: tuple[str, ...]
    read_only: bool
    allowed_paths: tuple[str, ...]

    def target(self, outcome: str) -> str | None:
        choices = dict(self.transitions)
        if outcome not in choices:
            raise DomainError(f"Этап {self.stage_id} не принимает outcome {outcome}")
        return choices[outcome]


@dataclass(frozen=True)
class RouteDefinition:
    entry: str
    nodes: tuple[RouteNode, ...]

    def __post_init__(self):
        if not self.nodes or len({n.stage_id for n in self.nodes}) != len(self.nodes):
            raise DomainError("Маршрут требует уникальные непустые узлы")
        names = {n.stage_id for n in self.nodes}
        if self.entry not in names:
            raise DomainError("Неизвестная входная точка маршрута")
        for target in (n for n in self.nodes if n.handler == HandlerKind.PUBLISH):
            if target.stage_id == self.entry or not target.read_only:
                raise DomainError("publish requires a read-only stage after inspection")
            predecessors = [(n, outcome) for n in self.nodes for outcome, dest in n.transitions if dest == target.stage_id]
            if not predecessors or any(n.handler != HandlerKind.INSPECT or outcome != "clear" for n, outcome in predecessors):
                raise DomainError("publish can follow only a clear, user-accepted inspection")
            if any(target.stage_id in n.rework_targets for n in self.nodes):
                raise DomainError("Rework must not bypass the inspection before publish")
        for node in self.nodes:
            text(node.stage_id, "stage id")
            text(node.role, "stage role")
            if set(dict(node.transitions)) != OUTCOMES[node.handler]:
                raise DomainError(f"{node.stage_id}: неверный набор outcomes обработчика")
            if len(dict(node.transitions)) != len(node.transitions):
                raise DomainError("Повтор outcome")
            if type(node.read_only) is not bool:
                raise DomainError("read_only должен быть явным bool")
            if (type(node.allowed_paths) is not tuple
                    or any(not isinstance(path, str) or not path for path in node.allowed_paths)
                    or len(set(node.allowed_paths)) != len(node.allowed_paths)):
                raise DomainError("allowed_paths должен содержать уникальные непустые строки")
            if node.handler == HandlerKind.INSPECT and not node.read_only:
                raise DomainError("inspect обязан оставлять предмет read-only")
            if len(set(node.rework_targets)) != len(node.rework_targets):
                raise DomainError("Повтор цели rework")
            if any(t not in names for t in node.rework_targets):
                raise DomainError("Неизвестная цель пользовательского rework")
            for _, target in node.transitions:
                if target is not None and target not in names:
                    raise DomainError(f"Неизвестный следующий этап {target}")
            if node.handler == HandlerKind.INSPECT and node.target("changes_requested") is None:
                raise DomainError("Внутренний inspect с находками должен иметь явный маршрут исправления")
            if node.handler == HandlerKind.REVISE:
                target = node.target("complete")
                if target is None or self.node(target).handler != HandlerKind.INSPECT:
                    raise DomainError("После revise требуется явный inspect исправления")
        # Bounded traversal: each vertex is processed once. No invented DSL.
        reached, pending = set(), [self.entry]
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            node = self.node(current)
            pending.extend(t for _, t in node.transitions if t is not None)
            pending.extend(node.rework_targets)
        if reached != names:
            raise DomainError(f"Недостижимые этапы: {sorted(names - reached)}")
        terminal = {
            node.stage_id
            for node in self.nodes
            if node.target(POSITIVE_OUTCOME[node.handler]) is None
        }
        can_finish = set(terminal)
        for _ in self.nodes:
            can_finish.update(n.stage_id for n in self.nodes if any(t in can_finish for _, t in n.transitions))
        if can_finish != names:
            raise DomainError(
                "Из каждого достижимого этапа должен существовать "
                "путь положительного завершения"
            )

    @classmethod
    def from_process(cls, process: dict) -> RouteDefinition:
        try:
            cfg = process["route"]
            exact(cfg, {"entry"}, "route")
            nodes = []
            for stage in process["stages"]:
                kind = HandlerKind(stage["handler"])
                transitions = stage["transitions"]
                if not isinstance(transitions, dict) or any(t is not None and not isinstance(t, str) for t in transitions.values()):
                    raise DomainError("transitions: требуется outcome → stage ID/null")
                if not isinstance(stage["rework_targets"], list) or any(not isinstance(t, str) for t in stage["rework_targets"]):
                    raise DomainError("rework_targets: требуется явный список ID")
                if kind == HandlerKind.INSPECT and stage["allowed_paths"]:
                    raise DomainError("inspect не разрешает изменения target paths")
                nodes.append(RouteNode(
                    stage["id"],
                    kind,
                    stage["role"],
                    tuple(transitions.items()),
                    tuple(stage["rework_targets"]),
                    stage["read_only"],
                    tuple(stage["allowed_paths"]),
                ))
            return cls(cfg["entry"], tuple(nodes))
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError(f"Неполный/неверный маршрут: {exc}") from exc

    def node(self, stage_id: str) -> RouteNode:
        for node in self.nodes:
            if node.stage_id == stage_id:
                return node
        raise DomainError(f"Этап не объявлен: {stage_id}")

    def with_stage_scopes(self, contracts: list[dict]) -> RouteDefinition:
        """Project the resolved Task write scopes onto this route's graph."""
        if not isinstance(contracts, list) or len(contracts) != len(self.nodes):
            raise DomainError("stage contracts require exact route coverage in route order")
        nodes = []
        for node, contract in zip(self.nodes, contracts, strict=True):
            if not isinstance(contract, dict) or contract.get("stage_id") != node.stage_id:
                raise DomainError("stage contracts require exact route coverage in route order")
            allowed = contract.get("allowed_paths")
            if (not isinstance(allowed, list)
                    or any(not isinstance(path, str) or not path for path in allowed)
                    or len(allowed) != len(set(allowed))):
                raise DomainError(f"{node.stage_id}.allowed_paths: requires unique nonempty strings")
            if node.read_only and allowed:
                raise DomainError(f"{node.stage_id}: read-only stage cannot have writable scope")
            nodes.append(replace(node, allowed_paths=tuple(allowed)))
        return RouteDefinition(self.entry, tuple(nodes))

    def index(self, stage_id: str) -> int:
        return tuple(n.stage_id for n in self.nodes).index(self.node(stage_id).stage_id)

    def next_inspection(self, stage_id: str) -> str | None:
        """Find the inspector of a producer's result, without crossing an inspect."""
        visited = set()
        while stage_id is not None:
            if stage_id in visited:
                raise DomainError("Inspection scope is ambiguous: positive route cycles without an inspector; correct the process graph")
            visited.add(stage_id)
            node = self.node(stage_id)
            if node.handler == HandlerKind.INSPECT:
                return stage_id
            stage_id = node.target(POSITIVE_OUTCOME[node.handler])
        return None

    def inspection_scope(self, stage_id: str) -> tuple[str, ...]:
        """Group original/follow-up inspectors through their corrective result."""
        inspectors = {n.stage_id: n for n in self.nodes if n.handler == HandlerKind.INSPECT}
        adjacent = {sid: set() for sid in inspectors}
        for sid, inspector in inspectors.items():
            corrective = inspector.target("changes_requested")
            following = self.next_inspection(corrective)
            if following is None:
                raise DomainError(f"Inspection scope for {sid} has no corrective inspector; correct the process graph")
            # Reinspection shares the positive result continuation. A different
            # continuation would merge two distinct results through one producer.
            if following != sid and inspector.target("clear") != inspectors[following].target("clear"):
                raise DomainError(f"Inspection scope is ambiguous for {sid}, {corrective}, {following}; declare separate corrective results")
            adjacent[sid].add(following)
            adjacent[following].add(sid)
        subject = self.next_inspection(stage_id)
        if subject is None:
            return ()
        reached, pending = set(), [subject]
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            pending.extend(adjacent[current] - reached)
        return tuple(sorted(reached))

    def can_reach(self, start: str, target: str) -> bool:
        self.node(start)
        self.node(target)
        reached, pending = set(), [start]
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            pending.extend(
                destination
                for _, destination in self.node(current).transitions
                if destination is not None
            )
        return target in reached

    def enter(self, progress: RouteProgress, target: str) -> RouteProgress:
        self.node(target)
        visits = dict(progress.visits)
        visits[target] += 1
        return RouteProgress(tuple(visits.items()), progress.transitions + 1, None, None)


@dataclass(frozen=True)
class RouteProgress:
    visits: tuple[tuple[str, int], ...]
    transitions: int
    outcome: str | None
    stage_work: str | None

    @classmethod
    def initial(cls, route: RouteDefinition):
        return cls(tuple((n.stage_id, 1 if n.stage_id == route.entry else 0) for n in route.nodes), 0, None, None)

    def to_dict(self):
        return {"visits":dict(self.visits), "transitions":self.transitions,
                "outcome":self.outcome, "stage_work":self.stage_work}

    @classmethod
    def from_dict(cls, value):
        exact(value, {"visits", "transitions", "outcome", "stage_work"}, "route progress")
        if not isinstance(value["visits"], dict) or any(type(v) is not int or v < 0 for v in value["visits"].values()):
            raise DomainError("Неверные счётчики посещений")
        if type(value["transitions"]) is not int or value["transitions"] < 0:
            raise DomainError("Неверный счётчик переходов")
        return cls(tuple(value["visits"].items()),value["transitions"],value["outcome"],value["stage_work"])
