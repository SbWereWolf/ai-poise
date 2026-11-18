"""Explicit recovery of a terminal check failure under the original submitted result."""
from pathlib import Path

from ..common import digest, read_json
from ..execution import timeout_profile
from ..modules.foundation.errors import PoiseError
from .check_attempts import pending_retry, validate_retry_budget


class VerificationRetry:
    def __init__(self, runtime):
        self.runtime = runtime

    def verify(self, args):
        h = self.runtime
        data = h.current_task()
        if data is None or data['claimed_by'] != h.session:
            raise PoiseError('Verification retry requires ownership of the current Task')
        retry, payload = args['retry'], args['result']
        packet = h.packet_digest({'result': payload, 'artifacts': args['artifacts']})
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
            return self.replay(data, retry, intent, mapping, payload, invocations)
        return self.start(data, retry, intent, binding, payload, invocations, checks)

    def start(self, data, retry, intent, binding, payload, invocations, checks):
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
        if not h.validate_stage_entry(data)['passed']:
            raise PoiseError('stage entry requirements are no longer satisfied')
        h.validate_verification_artifacts(payload['artifact_paths'], data)
        h.validate_proof_result(data, payload, tree)
        for method in checks:
            h.retention.bind_inputs(data, method)
        roots = h._roots(data)
        artifacts = h._candidate_artifacts(data, payload['artifact_paths'], roots)
        if not h._content_gate(data, 'pre', artifacts)['passed']:
            raise PoiseError('Verification retry requires the existing preconditions')
        h.task_commands.begin_check_attempt(
            task, h.session, tree, key, [method['id'] for method in checks],
            h.cfg['limits']['verify_attempts'], data['_version'], binding['submission_digest'],
            h._attempt_identity(tree, invocations),
            retry={'request_id': retry['request_id'], 'intent': intent, 'budgets': budgets,
                   'predecessor': previous['id'], 'commit': binding['commit'], 'packet': binding['packet']},
        )
        mapping = h.task_commands.verification_retry_mapping(task, retry['request_id'], intent)
        return self.replay(h.current_task(), retry, intent, mapping, payload, invocations, initial=True)

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

    def replay(self, data, retry, intent, mapping, payload, invocations, *, initial=False):
        h = self.runtime
        scope = {'retry': retry, 'intent': intent, 'mapping': mapping}
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
        return value if initial else {**value, 'replayed': True}
