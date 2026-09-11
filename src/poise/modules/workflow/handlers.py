"""Shared, pure stage strategies; no lifecycle writes or project knowledge."""
from dataclasses import dataclass
from .domain import HandlerKind, exact, text
from ..inspection.domain import FeedbackBook


@dataclass(frozen=True)
class HandlerResult:
    outcome: str | None
    feedback: FeedbackBook


def substantive_feedback(work, book, stage, iteration, label):
    """An explicit repair batch is reusable across result-producing strategies."""
    if isinstance(work,dict) and set(work)=={'resolutions'}:
        return book.propose(work['resolutions'],stage,iteration)
    exact(work,set(),label)
    return book


class ProduceHandler:
    def evaluate(self, work: dict, book: FeedbackBook, stage: str, iteration: int):
        updated=substantive_feedback(work,book,stage,iteration,"produce stage_work")
        return HandlerResult("complete", updated)

    def template(self):
        return {}


class InspectHandler:
    def evaluate(self, work: dict, book: FeedbackBook, stage: str, iteration: int):
        repair=isinstance(work,dict) and 'resolutions' in work
        exact(work, {"coverage","findings","resolution_decisions","resolutions"} if repair else
              {"coverage","findings","resolution_decisions"}, "inspect stage_work")
        if repair and (work['findings'] or work['resolution_decisions']):
            from ..foundation.errors import DomainError
            raise DomainError('Report correction cannot find/accept its own correction in the same inspection')
        text(work["coverage"], "inspection coverage")
        updated = book.inspect(work["findings"], work["resolution_decisions"], stage, iteration)
        if repair:updated=updated.propose(work["resolutions"],stage,iteration)
        return HandlerResult("changes_requested" if updated.open_findings else "clear", updated)

    def template(self):
        return {"coverage":"", "findings":[], "resolution_decisions":[]}


class ReviseHandler:
    def evaluate(self, work: dict, book: FeedbackBook, stage: str, iteration: int):
        exact(work, {"resolutions"}, "revise stage_work")
        return HandlerResult("complete", book.propose(work["resolutions"], stage, iteration))

    def template(self):
        return {"resolutions":[]}


class ObserveHandler:
    def evaluate(self, work: dict, book: FeedbackBook, stage: str, iteration: int):
        updated=substantive_feedback(work,book,stage,iteration,"observe stage_work")
        # Outcome is resolved only by a current EvidenceAssessment.
        return HandlerResult(None, updated)

    def template(self):
        return {}


class CheckHandler:
    def evaluate(self, work: dict, book: FeedbackBook, stage: str, iteration: int):
        updated=substantive_feedback(work,book,stage,iteration,"check stage_work")
        return HandlerResult(None, updated)

    def template(self):
        return {}


class ApplyPlanHandler:
    def evaluate(self, work, book, stage, iteration):
        # Full step bounds are validated by the application before any I/O.
        from ..actions.domain import parse_apply_work
        steps = work.get('plan', {}) if isinstance(work, dict) else {}
        if not isinstance(steps,dict):
            from ..foundation.errors import DomainError
            raise DomainError('Provide an explicit plan object')
        count = len(steps['sources']) if 'sources' in steps else (len(steps['steps']) if 'steps' in steps else 1)
        parse_apply_work(work, max(1,count))
        feedback=book.propose(work['finding_resolutions'],stage,iteration) if work['finding_resolutions'] else book
        return HandlerResult(None, feedback)

    def template(self):
        return {'plan':None,'phase':'prepare','resolutions':[],'finding_resolutions':[]}


class PublishHandler:
    def evaluate(self,work,book,stage,iteration):
        from ..actions.domain import Publication
        if isinstance(work,dict) and 'kind' in work:
            from ..catalogue.publication import DataPublication
            DataPublication.parse(work)
        else:
            Publication.parse(work)
        return HandlerResult(None,book)

    def template(self):
        return {'target_ref':'','expected_commit':'','authorization':''}


# Explicit implemented API, not a goal-type dispatch or fallback.
HANDLERS = {
    HandlerKind.APPLY_PLAN: ApplyPlanHandler(),
    HandlerKind.PUBLISH: PublishHandler(),
    HandlerKind.PRODUCE: ProduceHandler(),
    HandlerKind.INSPECT: InspectHandler(),
    HandlerKind.REVISE: ReviseHandler(),
    HandlerKind.OBSERVE: ObserveHandler(),
    HandlerKind.CHECK: CheckHandler(),
}


def handler(kind: HandlerKind):
    return HANDLERS[kind]
