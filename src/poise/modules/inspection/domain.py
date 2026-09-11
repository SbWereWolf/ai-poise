"""Immutable task-local inspection records. Acceptance is separate from a fix."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from ..foundation.errors import DomainError


def record(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DomainError(f"{where}: неверный набор полей")
    if any(not isinstance(v, str) or not v.strip() for v in value.values()):
        raise DomainError(f"{where}: поля должны быть непустыми строками")


@dataclass(frozen=True)
class Finding:
    id: str
    subject: str
    description: str
    evidence: str
    stage: str
    iteration: int


@dataclass(frozen=True)
class Resolution:
    id: str
    finding_id: str
    description: str
    evidence: str
    stage: str
    iteration: int


@dataclass(frozen=True)
class ReviewDecision:
    resolution_id: str
    decision: str
    reason: str
    stage: str
    iteration: int


@dataclass(frozen=True)
class FeedbackBook:
    findings: tuple[Finding, ...]
    resolutions: tuple[Resolution, ...]
    decisions: tuple[ReviewDecision, ...]

    @classmethod
    def empty(cls):
        return cls((), (), ())

    @property
    def open_findings(self):
        accepted = {d.resolution_id for d in self.decisions if d.decision == "accepted"}
        fixed = {r.finding_id for r in self.resolutions if r.id in accepted}
        return tuple(f for f in self.findings if f.id not in fixed)

    @property
    def pending_resolutions(self):
        decided = {d.resolution_id for d in self.decisions}
        return tuple(r for r in self.resolutions if r.id not in decided)

    def propose(self, items, stage, iteration):
        if not isinstance(items, list) or not items:
            raise DomainError("revise требует предложения исправлений")
        if self.pending_resolutions:
            raise DomainError("Предыдущие исправления ещё не осмотрены")
        open_ids = {f.id for f in self.open_findings}
        proposals = []
        existing = {r.id for r in self.resolutions}
        for item in items:
            record(item, {"id","finding_id","description","evidence"}, "resolution")
            if item["id"] in existing:
                raise DomainError("Повтор ID исправления")
            if item["finding_id"] not in open_ids:
                raise DomainError("Исправление не относится к открытой находке")
            existing.add(item["id"])
            proposals.append(Resolution(**item, stage=stage, iteration=iteration))
        covered = [r.finding_id for r in proposals]
        if set(covered) != open_ids or len(set(covered)) != len(covered):
            raise DomainError("revise должен предложить ровно одно исправление каждой открытой находки")
        return FeedbackBook(self.findings, self.resolutions + tuple(proposals), self.decisions)

    def inspect(self, findings, decisions, stage, iteration):
        if not isinstance(findings, list) or not isinstance(decisions, list):
            raise DomainError("Находки и решения должны быть списками")
        pending = {r.id for r in self.pending_resolutions}
        results = []
        for item in decisions:
            record(item, {"resolution_id","decision","reason"}, "resolution decision")
            if item["resolution_id"] not in pending:
                raise DomainError("Можно осмотреть только ожидающее решение исправление")
            if item["decision"] not in ("accepted", "rejected"):
                raise DomainError("Решение по исправлению: accepted или rejected")
            results.append(ReviewDecision(**item, stage=stage, iteration=iteration))
        if {d.resolution_id for d in results} != pending or len(results) != len(pending):
            raise DomainError("Требуется ровно одно решение по каждому ожидающему исправлению")
        known = {f.id for f in self.findings}
        new = []
        for item in findings:
            record(item, {"id","subject","description","evidence"}, "finding")
            if item["id"] in known:
                raise DomainError("Повтор ID находки; прежняя запись не заменяется")
            known.add(item["id"])
            new.append(Finding(**item, stage=stage, iteration=iteration))
        return FeedbackBook(self.findings + tuple(new), self.resolutions, self.decisions + tuple(results))

    def to_dict(self):
        return {"findings":[asdict(x) for x in self.findings],
                "resolutions":[asdict(x) for x in self.resolutions],
                "decisions":[asdict(x) for x in self.decisions]}

    def context(self):
        return {**self.to_dict(), "open_findings":[asdict(x) for x in self.open_findings],
                "pending_resolutions":[asdict(x) for x in self.pending_resolutions]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"findings","resolutions","decisions"}:
            raise DomainError("Неверный snapshot осмотра")
        try:
            return cls(tuple(Finding(**x) for x in value["findings"]),
                       tuple(Resolution(**x) for x in value["resolutions"]),
                       tuple(ReviewDecision(**x) for x in value["decisions"]))
        except (TypeError, KeyError) as exc:
            raise DomainError(f"Неверная запись осмотра: {exc}") from exc
