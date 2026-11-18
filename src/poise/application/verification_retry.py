"""Explicit recovery of a terminal check failure under the original submitted result."""
from hashlib import sha256
from pathlib import Path

from ..common import digest, read_json
from ..execution import timeout_profile
from ..modules.foundation.errors import PoiseError
from .check_attempts import pending_retry, validate_retry_budget
from .stage_verification import prepare_candidate


class VerificationRetry:
    def __init__(self, runtime):
        self.runtime = runtime

    def verify(self, args):
        h = self.runtime
        data = h.current_task()
        if data is None or data['claimed_by'] != h.session:
            raise PoiseError('Verification retry requires ownership of the current Task')
        retry = args['retry']
        packet = h.packet_digest({'result': args['result'], 'artifacts': args['artifacts']})
        intent = digest({'operation': 'verification-retry', 'task': data['id'], 'actor': h.session,
                         'packet': packet, 'retry': retry})
        mapping = h.task_commands.verification_retry_mapping(data['id'], retry['request_id'], intent)
        pending = data.get('pending')
        if pending is not None and (mapping is None or pending_retry(pending) is None
                                    or pending.get('attempt_id') != mapping['attempt_id']):
            raise PoiseError('Unknown check outcome or pending external operation; resolve the original attempt')
        stage = h._stage(data)
        if data['status'] != 'active' and not (data['status'] == 'verified' and mapping is not None):
            raise PoiseError('Verification retry requires an active failed submission')
        h.task_commands.reviewer_preflight(data['id'], h.session)
        if stage['handler'] in ('apply_plan', 'publish') or data.get('duplicate_reuse') is not None:
            raise PoiseError('Verification retry cannot repeat a stage action or a reuse operation')

        payload, supplied = prepare_candidate(h.work_resources.factory(), args)
        expected_artifacts = {str(item.path.resolve()): sha256(item.content).hexdigest() for item in supplied}
        if expected_artifacts:
            artifacts = h.validate_verification_artifacts(payload['artifact_paths'], data)
            self.require_supplied_artifacts(artifacts, expected_artifacts)

        workspace = h._verification_workspace(data)
        if data['worktree'] is not None and h._git(workspace, 'symbolic-ref', '--short', 'HEAD') != data['branch']:
            raise PoiseError('Verification retry source branch changed')
        scope = h.validate_stage_scope(data)
        tree, changed = scope['tree'], scope['changed']
        commit = h._git(workspace, 'rev-parse', 'HEAD')
        if h._git(workspace, 'rev-parse', 'HEAD^{tree}') != tree:
            raise PoiseError('Verification retry requires the original committed source tree')
        checks = h._select_checks(data, changed)
        invocations, key = h._verification_execution(data, tree, checks, workspace)
        if data['status'] == 'verified':
            submissions = {receipt.get('submission_digest') for receipt in data['last_report']['checks']}
            submission = mapping['submission_digest'] if submissions == {mapping['submission_digest']} else None
        else:
            submission = h.runner.matching_submission_digest(data['id'], h.session, payload)
        if submission is None:
            raise PoiseError('Verification retry requires the exact failed submission')
        binding = {'actor': h.session, 'stage': stage['id'], 'iteration': data['iteration'],
                   'submission_digest': submission, 'verified_tree': tree, 'execution_key': key,
                   'commit': commit, 'packet': packet}
        if mapping is not None:
            if any(mapping.get(name) != value for name, value in binding.items()):
                raise PoiseError('Verification retry scope or source changed')
            return self.replay(data, retry, intent, mapping, payload, invocations, expected_artifacts)
        return self.start(data, retry, intent, binding, payload, invocations, expected_artifacts)

    @staticmethod
    def require_supplied_artifacts(artifacts, expected):
        actual = {item['path']: item['digest'] for item in artifacts}
        if any(actual.get(path) != digest for path, digest in expected.items()):
            raise PoiseError('Supplied verification artifact missing or changed')

    def start(self, data, retry, intent, binding, payload, invocations, expected_artifacts):
        h = self.runtime
        task, tree, key = data['id'], binding['verified_tree'], binding['execution_key']
        previous = h.task_commands.failed_observation_batch(task, tree, key)
        if previous is None or not any(
                item['id'] == retry['failed_check_id']
                and ((item['guard'] and not item['passed']) or not item['interpretable'])
                for item in previous['receipts']):
            raise PoiseError('The failed receipt is not part of the current terminal failed batch')
        receipts = previous['receipts']
        if not h._intact_receipts(task, receipts, invocations, tree):
            raise PoiseError('Original failed verification receipts are missing or changed')
        budgets = [self.frozen_budget(receipt, invocation)
                   for receipt, invocation in zip(receipts, invocations, strict=True)]
        reservation = {'request_id': retry['request_id'], 'intent': intent, 'budgets': budgets,
                       'predecessor': previous['id'], 'commit': binding['commit'], 'packet': binding['packet']}
        scope = {'intent': intent, 'mapping': binding, 'reservation': reservation,
                 'version': data['_version'], 'supplied_artifacts': expected_artifacts}
        return h.verify(payload, packet_digest=binding['packet'], verification_retry=scope)

    @staticmethod
    def frozen_budget(receipt, invocation):
        method_id = invocation['method']['id']
        profile = timeout_profile(invocation['method']['argv'], method_id, invocation['environment'])
        try:
            snapshot = read_json(Path(receipt['timeout_maintenance']['path']))
            selection = receipt['timeout_selection']
            if (snapshot['profile'] != profile or receipt['timeout_profile'] != profile
                    or snapshot['selection'] != selection):
                raise ValueError('timing identity')
            budget = {'method': method_id, 'prior_receipt': receipt['id'], 'profile': profile,
                      'selection': selection, 'evidence_ids': snapshot['history_evidence_ids'],
                      'cleanup_seconds': receipt['cleanup_seconds']}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PoiseError('Original timeout and capture settings cannot be verified') from exc
        return validate_retry_budget(budget, method_id)

    def replay(self, data, retry, intent, mapping, payload, invocations, expected_artifacts):
        h = self.runtime
        scope = {'intent': intent, 'mapping': mapping, 'supplied_artifacts': expected_artifacts}
        pending = data['pending']
        if pending is not None:
            descriptor = pending_retry(pending)
            if (descriptor is None or descriptor['request_id'] != retry['request_id']
                    or descriptor['intent'] != intent or pending['attempt_id'] != mapping['attempt_id']
                    or digest(descriptor['budgets']) != mapping['budget_digest']
                    or [run['run_id'] for run in pending['runs']] != mapping['runs']):
                raise PoiseError('Unknown check outcome: retry reservation identity does not match')
            for invocation, budget in zip(invocations, descriptor['budgets'], strict=True):
                method = invocation['method']
                if timeout_profile(method['argv'], method['id'], invocation['environment']) != budget['profile']:
                    raise PoiseError('Frozen verification retry timeout profile changed')
        else:
            batch = h.task_commands.verification_retry_batch(data['id'], mapping)
            tree, key = mapping['verified_tree'], mapping['execution_key']
            if batch is None or not h._intact_receipts(data['id'], batch['receipts'], invocations, tree):
                raise PoiseError('Unknown check outcome: retry has no intact completed receipt set')
            receipts = batch['receipts']
            if not h._usable_receipts(data['id'], receipts, invocations, tree):
                return {'status': 'checks_failed', 'task': data['id'], 'stage': mapping['stage'],
                        'attempt': mapping['attempt'], 'checks': receipts, 'replayed': True}
            if data['status'] == 'verified':
                if (data['last_report']['checks'] != receipts
                        or h.work_resources.packet(data) != mapping['packet']):
                    raise PoiseError('Verified result no longer matches this retry')
                return h.verify(None, packet_digest=mapping['packet'])
            latest = h.task_commands.observation_batch(data['id'], tree, key)
            if latest is None or latest['id'] != batch['id']:
                raise PoiseError('The completed retry was superseded before publication')
            scope['batch'] = batch
        value = h.verify(payload, packet_digest=mapping['packet'], verification_retry=scope)
        return {**value, 'replayed': True}
