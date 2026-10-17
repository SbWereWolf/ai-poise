"""Coordinate one preparation attempt; external effects never hold a UoW."""
from uuid import uuid4
from ..modules.worktree_preparation.domain import PreparationAttempt
from ..modules.worktree_preparation.ports import PreparationEffects


class WorktreePreparation:
    def __init__(self, unit_of_work, actor: str, effects: PreparationEffects):
        self.unit_of_work, self.actor, self.effects = unit_of_work, actor, effects

    def _save(self, attempt):
        with self.unit_of_work() as uow:
            return uow.preparation.save(self.actor, attempt, attempt.revision)

    def _select(self, coordinates, request_id):
        task_id = coordinates['task_id']
        with self.unit_of_work() as uow:
            latest = uow.preparation.latest(task_id)
            if latest is not None and latest.phase != 'completed':
                return latest
            if request_id is None:
                return latest
            return uow.preparation.by_request(task_id, request_id)

    def prepare(self, coordinates, request_id):
        attempt = self._select(coordinates, request_id)
        if attempt is not None:
            if attempt.phase != 'completed':
                attempt = self.reconcile(attempt)
                if attempt.phase != 'completed':
                    return attempt.projection()
            if request_id is None or request_id == attempt.request_id:
                return attempt.projection()
            # An unresolved old attempt is now actually registered. Resolve an
            # earlier completed explicit request before considering a new one.
            attempt = self._select(coordinates, request_id)
            if attempt is not None:
                return attempt.projection()
        with self.unit_of_work() as uow:
            latest = uow.preparation.latest(coordinates['task_id'])
            attempt = PreparationAttempt.begin(coordinates, uuid4().hex, request_id)
            attempt = uow.preparation.save(self.actor, attempt, 0 if latest is None else latest.revision)
        try:
            material = self.effects.capture_configuration(attempt)
            self.effects.publish_inputs(material)
            self.effects.execute_operations(material)
            capture = self.effects.save_capture(material)
            attempt = self._save(attempt.captured(capture))
        except Exception as exc:
            return self._save(attempt.unconfirmed(str(exc))).projection()
        return self.reconcile(attempt).projection()

    def reconcile(self, attempt):
        if attempt.phase != 'captured':
            return attempt.unconfirmed(attempt.reason or 'Подготовка начата, но полный результат ещё не подтверждён; исследуйте сохранённые материалы')
        try:
            report = self.effects.confirm_capture(attempt.capture)
            receipt, records = self.effects.publish_receipt(report)
        except Exception as exc:
            return self._save(attempt.unconfirmed(str(exc)))
        with self.unit_of_work() as uow:
            uow.artifacts.link_task(attempt.coordinates['task_id'], records)
            return uow.preparation.save(self.actor, attempt.complete(receipt), attempt.revision)
