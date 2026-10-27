"""Coordinate one durable transfer through the native result/route/claim owners."""
import json
from uuid import uuid4

from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.tasks.handoff import StageHandoffState, normal_progression
from ..modules.tasks.handoff_ports import HandoffEffects
from ..modules.tasks.ports import TaskReadSet


class StageResultHandoffCommands:
    def __init__(self, unit_of_work, effects: HandoffEffects, actor):
        self.uow, self.effects, self.actor = unit_of_work, effects, actor

    def _load(self, request_id):
        inputs = self.uow().capture(TaskReadSet((), handoff_requests=(request_id,)))
        return inputs.handoff(request_id)

    def _save(self, state, inputs=None):
        if inputs is None:
            owned = state.release is None
            inputs = self.uow().capture(TaskReadSet(
                (state.task_id,) if owned else (),
                actors=(self.actor,) if owned else (),
                handoff_requests=(state.request_id,),
            ))
            if owned and inputs.task(state.task_id).state.claimed_by != self.actor:
                raise PoiseError('Handoff phase ownership changed; preserve its original intent')
        encoded = json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with self.uow() as uow:
            uow.require_inputs(inputs)
            uow.handoffs.save_prepared(self.actor, state, encoded, state.revision - 1)
        return state

    def _admit(self, args, intent_digest):
        self.effects.require_owner(args['task_id'])
        prepared = self.effects.prepare_verification(args)
        source = self.effects.source(args['task_id'])
        inputs = self.uow().capture(TaskReadSet(
            (args['task_id'],), actors=(self.actor,), history_ids=(args['task_id'],),
            handoff_requests=(args['request_id'],),
        ))
        previous = inputs.handoff(args['request_id'])
        if previous is not None:
            previous.require_identity(self.actor, args['task_id'], intent_digest)
            return previous, None
        task = inputs.task(args['task_id'])
        if task.state.claimed_by != self.actor or task.state.status.value not in ('active', 'verified'):
            raise PoiseError('handoff task_id requires an owned unfinished stage result')
        active = inputs.progression_view(args['task_id'])
        target = args.get('target_stage')
        progression_id = (active['request_id'] if active is not None
                          and active['status'] == 'active' and active['target_stage'] == target
                          else str(uuid4()))
        state = StageHandoffState(
            request_id=args['request_id'], actor=self.actor, task_id=args['task_id'],
            intent_digest=intent_digest,
            packet_digest=self.effects.digest({'result': args['result'], 'artifacts': args['artifacts']}),
            origin={'stage': task.stage.stage_id, 'iteration': task.state.iteration,
                    'status': task.state.status.value, 'submission_digest': task.state.submission_digest},
            admitted_source=source, progression_id=progression_id, target_stage=target,
            release_id=str(uuid4()), release_reason=args['reason'], revision=1,
            verification=None, verified_source=None, progression_started=None,
            progression=None, release_context=None, release_intent=None, release=None, completed=False,
        )
        return self._save(state, inputs), prepared

    def _blocked(self, state, phase, error=None):
        task, request = state.task_id, state.request_id
        continuation = f'Повторите точный handoff request_id={request} task_id={task}.'
        reason = str(error) if error is not None else ''
        pending = self.effects.pending(task)
        if isinstance(pending, dict) and pending.get('kind') == 'check_attempt':
            runs = ', '.join(run['run_id'] for run in pending['runs'] if run['started'])
            continuation = (
                f'show kind=work_overview: контекст attempt={pending["attempt_id"]} run={runs}. '
                'Исход внешней проверки остаётся неизвестным: сначала установите его и восстановите '
                'исходную immutable receipt и её файлы. Либо используйте отдельно разрешённый '
                'task action=restart после остановки/проверки эффектов. Чтение контекста не разрешает '
                f'повторный запуск неизвестной команды. {continuation}'
            )
        elif error is not None and any(word in reason.casefold() for word in ('work packet', 'receipt')):
            if 'work packet' in reason.casefold():
                continuation = (
                    f'show kind=task для Task {task}; согласуйте explicit rework текущего результата '
                    'через его владельца, затем используйте новый handoff request_id.'
                )
            elif 'receipt' in reason.casefold():
                continuation = (
                    f'show kind=evidence для Task {task}; восстановите исходные immutable receipts '
                    f'и их файлы через владельца доказательств. {continuation}'
                )
        elif error is None and phase == 'verification' and state.verification is not None:
            outcome = state.verification
            if outcome['status'] == 'checks_failed':
                failed = [f'{r["method"]}: exit={r["actual_exit_code"]}' for r in outcome['checks'] if not r['passed']]
                reason = 'Проверки результата не прошли: ' + ', '.join(failed)
                continuation = (
                    f'bootstrap decision=rework rework_stage={state.origin["stage"]}: '
                    'исправьте причину подтверждённой неуспешной проверки через её этап, '
                    'затем передайте новый результат с новым handoff request_id. '
                    'Текущий идентификатор сохраняет прежний результат.'
                )
            else:
                reason = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
                continuation = f'show kind=evidence для Task {task}; устраните указанный native gate. {continuation}'
        elif error is None and phase == 'progression' and state.progression is not None and not normal_progression(state.progression):
            reason = json.dumps(state.progression, ensure_ascii=False, sort_keys=True)
            stage = state.progression['stage']
            continuation = (
                f'show kind=stage_criteria stage_id={stage} для Task {task}; '
                f'проверьте конкретный native gate из progression.failure/recovery. {continuation}'
            )
        elif not isinstance(error, VersionConflict) and reason and any(word in reason.casefold() for word in ('source', 'исход', 'changed', 'измен')):
            continuation = (
                f'show kind=task для Task {task}; сохраните WIP и согласуйте explicit rework '
                f'через текущего владельца результата, затем используйте новый handoff request_id. {continuation}'
            )
        return state.project(phase=phase, blocked={'reason': reason, 'continuation': continuation})

    def _release(self, state):
        if state.release_intent is not None:
            original = self.effects.original_release(state.release_intent)
            if original is not None:
                return state.changed(release=original)
        self.effects.require_verified_source(state)
        context = self.effects.context(state.task_id)
        if state.release_context is not None and context != state.release_context:
            raise PoiseError('Handoff source stage/result changed before release')
        data = self.effects.require_owner(state.task_id)
        if state.release_intent is None:
            state = self._save(state.changed(
                release_context=context,
                release_intent={'task_id': state.task_id, 'request_id': state.release_id,
                                'expected_version': data['version'], 'reason': state.release_reason},
            ))
        try:
            receipt = self.effects.release(state.release_intent)
        except VersionConflict:
            # A known refusal has no release effect. Never revise an unknown release intent.
            if self.effects.original_release(state.release_intent) is None:
                self.effects.require_verified_source(state)
                current = self.effects.require_owner(state.task_id)
                if (self.effects.context(state.task_id) == state.release_context
                        and current['version'] != state.release_intent['expected_version']):
                    self._save(state.changed(release_intent={
                        **state.release_intent, 'expected_version': current['version']}))
            raise
        if receipt['replayed']:
            receipt = self.effects.original_release(state.release_intent)
            if receipt is None:
                raise PoiseError('Original release outcome is unconfirmed; retry the exact handoff')
        return state.changed(release=receipt)

    def apply(self, args):
        intent_digest = self.effects.digest(args)
        state = self._load(args['request_id'])
        prepared = None
        if state is not None:
            state.require_identity(self.actor, args['task_id'], intent_digest)
            if state.completed:
                return state.project(phase='complete', replayed=True)
        else:
            state, prepared = self._admit(args, intent_digest)
        phase = 'verification'
        try:
            if state.needs_verification:
                outcome = self.effects.recovered_verification(state)
                if outcome is None:
                    if prepared is None:
                        prepared = self.effects.prepare_verification(args)
                    self.effects.require_unverified_source(state, prepared)
                    outcome = self.effects.execute_verification(prepared)
                proof = ({'commit': outcome['verification_commit'], 'tree': outcome['verified_tree']}
                         if outcome['status'] == 'verified' else None)
                state = state.changed(verification=outcome, verified_source=proof)
                state = self._save(state)
            if state.verification['status'] != 'verified':
                return self._blocked(state, phase)
            phase = 'progression'
            if not normal_progression(state.progression):
                if state.progression_started is None:
                    state = state.changed(progression_started=self.effects.progression_context(state))
                    state = self._save(state)
                self.effects.require_progression_resume(state)
                outcome = self.effects.advance(state)
                state = state.changed(progression=outcome)
                state = self._save(state)
            if not normal_progression(state.progression):
                return self._blocked(state, phase)
            phase = 'release'
            if state.release is None:
                state = self._release(state)
                state = self._save(state)
            finished = state.finish()
            state = self._save(finished)
            return state.project(phase='complete')
        except (PoiseError, OSError) as error:
            return self._blocked(state, phase, error)
