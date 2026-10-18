"""Coordinate prepared command intents, usable completions and bounded coverage."""
from copy import deepcopy
from dataclasses import asdict
import json

from ..modules.document_intake.command import (
    CommandFailure, call_hash, decision, normalize_completion,
)
from ..modules.document_intake.coverage import coverage_decision, union_ranges
from ..modules.document_intake.domain import identity


class CommandIntake:
    def __init__(self, resources, sessions, source, policy, policy_sha256, clock):
        self.resources, self.sessions, self.source = resources, sessions, source
        self.policy, self.policy_sha256, self.clock = policy, policy_sha256, clock

    @staticmethod
    def result(phase):
        return dict(schema='poise-tool-intake-result-1', phase=phase, status='ignored',
                    context=None, call=None, decision=decision('no_content_reader', 'none'),
                    counts=dict(eligible=0, skipped=0, registered=0), error=None, response_path=None)

    def execute(self, event):
        r = self.resources
        phase = 'pre' if event['hook_event_name'] == 'PreToolUse' else 'post'
        result = self.result(phase)
        observed = r.observe_active()
        # Parsing/file observations precede the coordination/SQLite writer gate.
        classified = None
        if phase == 'pre':
            classified = self.source.classify(event['tool_input']['command'], event['execution_cwd'])
        with r.store.locked():
            r.admit(observed, existing_only=phase == 'post')
            result['context'], _ = self.sessions.context()
            generation, calls, history = r.registry.command_snapshot(r.scope)
            self._validate_cached_paths(calls)
            previous = calls.get(event['tool_use_id'])
            if previous is not None:
                if (previous['input_digest'] != identity(event['tool_input']['command'])
                        or previous['tool_name'] != event['tool_name']
                        or (phase == 'pre' and previous['pre_turn_id'] != event['turn_id'])):
                    return self._conflict(result, event)
                if phase == 'pre':
                    return deepcopy(previous['pre_result'])
            if phase == 'post':
                return self._settle(event, previous, generation, result)
            if event['tool_name'] != 'Bash':
                raise CommandFailure('invalid_event', field='tool_name')
            return self._prepare(event, classified, history, generation, result)

    def _path(self, event, phase):
        r = self.resources
        return (r.target / r.session['response_directory'] / r.scope.checkout_key
                / identity([event['tool_use_id'], phase]) / r.bound.config['paths']['response'])

    def _validate_cached_paths(self, calls):
        for record in calls.values():
            event = {'tool_use_id': record['scope']['tool_use_id']}
            for phase in ['pre', 'post']:
                result = record[phase + '_result']
                if result is not None and result['response_path'] != str(self._path(event, phase)):
                    raise CommandFailure('registry_invalid')

    def _call(self, event, *, pre_turn=None, post_turn=None):
        return dict(tool_use_id=event['tool_use_id'], tool_name=event['tool_name'],
                    pre_turn_id=event['turn_id'] if pre_turn is None else pre_turn,
                    post_turn_id=post_turn)

    def _conflict(self, result, event):
        result.update(status='conflict', call=self._call(event),
                      decision=decision('call_conflict', 'deny' if result['phase'] == 'pre' else 'none'))
        return result

    def _prepare(self, event, classified, history, generation, result):
        r = self.resources
        plans = self.source.plans(classified.readers, identity(event['tool_input']['command']), generation)
        result['call'] = self._call(event)
        reason = classified.reason_code if classified.decision == 'deny' else None
        mode = self.policy['classifier']['mode']
        if reason is None:
            reason = coverage_decision(plans, history, generation, self.policy['registry'])
            mode = self.policy['registry']['mode']
        audit = classified.decision == 'audit'
        if reason and reason.startswith('audit_'):
            audit, reason = True, reason.removeprefix('audit_')
        elif reason:
            audit = mode == 'audit'
            if not audit:
                result.update(status='denied', decision=decision(reason, 'deny'))
                return result
        if not plans:
            result['decision'] = decision(reason or classified.reason_code, 'audit' if audit else 'none')
            return result
        if any(not self.source.current(plan) for plan in plans):
            raise CommandFailure('unstable_version')
        path = self._path(event, 'pre')
        result.update(status='pending', decision=decision(reason or 'read_prepared', 'audit' if audit else 'allow'),
                      response_path=str(path))
        record = dict(schema='poise-command-call-1',
                      scope=dict(namespace=r.scope.namespace, caller_key=r.scope.caller_key,
                                 checkout_key=r.scope.checkout_key, tool_use_id=event['tool_use_id']),
                      episode=r.scope.episode, pre_turn_id=event['turn_id'], tool_name=event['tool_name'],
                      input_digest=identity(event['tool_input']['command']), generation=generation,
                      created_clock=asdict(self.clock.observe()), policy_snapshot=deepcopy(self.policy),
                      policy_sha256=self.policy_sha256, plans=plans, state='pending',
                      pre_result=deepcopy(result), post_fingerprint=None, post_result=None,
                      post_turn_id=None, publication_state='unconfirmed')
        r.registry.prepare_call(r.scope, record)
        return result

    def _settle(self, event, previous, generation, result):
        r = self.resources
        if previous is None:
            result.update(status='unmatched', call=self._call(event, post_turn=event['turn_id']),
                          decision=decision('unmatched_call', 'none'))
            return result
        policy = previous['policy_snapshot']
        completion = normalize_completion(event.get('tool_response'), policy['registry']['post_result'])
        if previous['state'] != 'pending':
            if previous['post_fingerprint'] == completion.fingerprint:
                return deepcopy(previous['post_result'])
            return self._conflict(result, event)
        created, now = previous['created_clock'], self.clock.observe()
        if now.comparison_domain != created['comparison_domain'] or now.monotonic_ns < created['monotonic_ns']:
            raise CommandFailure('incomparable_clock')
        expired = now.monotonic_ns - created['monotonic_ns'] > int(
            policy['registry']['retention']['max_pending_age_seconds'] * 1_000_000_000)
        reason, state = completion.reason, 'settled' if completion.usable else 'discarded'
        if expired:
            reason, state = 'pending_expired', 'expired'
        elif generation != previous['generation']:
            reason, state = 'generation_changed', 'discarded'
        plans = previous['plans']
        eligible = [plan for plan in plans if self.source.current(plan)] if state == 'settled' else []
        result.update(status='settled' if state == 'settled' else 'discarded',
                      call=self._call(event, pre_turn=previous['pre_turn_id'], post_turn=event['turn_id']),
                      decision=decision(reason, 'none'), response_path=str(self._path(event, 'post')),
                      counts=dict(eligible=len(plans) if completion.usable else 0,
                                  skipped=len(plans) - len(eligible) if completion.usable else 0,
                                  registered=len({(p['path'], p['operation_digest']) for p in eligible})))
        terminal = previous | dict(state=state, post_fingerprint=completion.fingerprint,
                                   post_result=deepcopy(result), post_turn_id=event['turn_id'])
        saved = r.registry.settle_call(r.scope, call_hash(previous), terminal, eligible)
        return deepcopy(saved['post_result'])

    def coverage(self):
        r = self.resources
        observed = r.observe_active()
        with r.store.locked():
            r.admit(observed, existing_only=True)
            generation, calls, history = r.registry.command_snapshot(r.scope)
            self._validate_cached_paths(calls)
            context, _ = self.sessions.context()
            documents = {}
            stale = set()
            for plan in history:
                key = (plan['path'], plan['version_key'])
                if not self.source.current(plan):
                    stale.add(key)
                    continue
                if plan['generation'] != generation:
                    continue
                document = documents.setdefault(key, dict(path=plan['path'], version_key=plan['version_key'],
                    complete=False, ranges=[], operation_count=0, last_generation=generation))
                document['complete'] |= plan['coverage_kind'] == 'complete'
                document['ranges'] = union_ranges(document['ranges'] + plan['line_ranges'])
                document['operation_count'] += int(plan['coverage_kind'] == 'operation')
            pending = [dict(tool_use_id=tool, pre_turn_id=call['pre_turn_id'], generation=call['generation'])
                       for tool, call in calls.items() if call['state'] == 'pending']
            details = dict(schema='poise-read-coverage-1', documents=list(documents.values()), pending=pending,
                           counts=dict(current_documents=len(documents), stale_documents=len(stale),
                                       pending=len(pending), settled=sum(c['state'] == 'settled' for c in calls.values())),
                           omitted=dict(documents=0, ranges=0, pending=0))
            self._bound_coverage(details)
            return context, details

    def _bound_coverage(self, details):
        limits = self.policy['context']
        documents, pending, omitted = details['documents'], details['pending'], details['omitted']
        omitted['documents'] = max(0, len(documents) - limits['max_documents'])
        del documents[limits['max_documents']:]
        omitted['pending'] = max(0, len(pending) - limits['max_pending_records'])
        del pending[limits['max_pending_records']:]
        for document in documents:
            omitted['ranges'] += max(0, len(document['ranges']) - limits['max_ranges_per_document'])
            del document['ranges'][limits['max_ranges_per_document']:]
        def size():
            return len(json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        while size() > limits['max_bytes']:
            if pending:
                pending.pop(); omitted['pending'] += 1
            elif documents:
                removed = documents.pop(); omitted['documents'] += 1
                omitted['ranges'] += len(removed['ranges'])
            else:
                raise CommandFailure('coverage_budget_too_small', field='context.max_bytes')
