"""Existing accepted-source lineage and current external-observation admission."""
from copy import deepcopy

from .evidence import completed_receipts
from .records import RecordsError, identifier


class ProgressionPolicy:
    @staticmethod
    def stage_proof_contract(process, contract, context, stage_id):
        def layer(value):
            return {**{key: value[key] for key in ('sections', 'routes')},
                    'requirements': [rule for rule in value['requirements'] if stage_id in rule['stages']]}
        checks = next(s['methods'] for s in contract['stage_expectations'] if s['stage_id'] == stage_id)
        return {
            'stage': next(s for s in process['stages'] if s['id'] == stage_id),
            'expectations': next(s for s in contract['stage_expectations'] if s['stage_id'] == stage_id),
            'methods': [m for m in contract['methods'] if m['id'] in checks],
            'evidence_plan': contract['evidence_plan'][stage_id],
            'content': layer(process['content_contract']), 'task_content': layer(context['task_content']),
            'stage_contract': next(s for s in context['stage_contracts'] if s['stage_id'] == stage_id),
            'file_policy': context.get('file_policy'),
        }

    @staticmethod
    def accepted_sources(records, generation):
        """Project the old accepted_sources/rework rule over own ordered history."""
        sources = {}
        for record in records:
            if record.generation > generation:
                continue
            if record.kind == 'accepted_visit' and record.generation < generation:
                sources[record.data['stage_id']] = deepcopy(record.data)
            elif record.kind == 'lifecycle_event' and record.data['action'] == 'rework':
                stage, seen = record.data['target_stage_id'], set()
                while stage in sources and stage not in seen:
                    seen.add(stage)
                    old = sources[stage]
                    sources[stage] = dict(old, reason='superseded_source')
                    stage = old['next_stage']
        return sources

    @staticmethod
    def _source(value, label):
        if not isinstance(value, dict) or not {'commit', 'tree'} <= value.keys():
            raise RecordsError(f'{label}: provide source commit and tree')
        for field in ('commit', 'tree'):
            identifier(value[field], f'{label} {field}')

    @staticmethod
    def admitted_observations(methods, attempt, source, subject_rules):
        """Check external facts, then let EvidenceBook assess proof/review meaning."""
        ProgressionPolicy._source(attempt['source'], 'Observation source')
        if attempt['source'] != source:
            return 'stale_source'
        if attempt['methods'] != methods:
            raise RecordsError('Observation methods must match the current declared stage methods')
        receipts = attempt['receipts']
        if not isinstance(receipts, list):
            raise RecordsError('Observation receipts must be a list')
        by_id = {method['id']: method for method in methods}
        covered = set()
        reason = None
        for receipt in receipts:
            required = {'id', 'method', 'obligations', 'argv', 'cwd', 'environment',
                        'actual_exit_code', 'stdout', 'stderr', 'capture_complete',
                        'interpretable', 'timed_out', 'guard', 'passed', 'commit', 'tree',
                        'source_before', 'source_after', 'source_unchanged'}
            if not isinstance(receipt, dict) or not required <= receipt.keys():
                raise RecordsError('Provide complete declared observation metadata')
            for field in ('id', 'method', 'commit', 'tree'):
                identifier(receipt[field], f'Observation {field}')
            if (not isinstance(receipt['obligations'], list)
                    or any(not isinstance(item, str) or not item for item in receipt['obligations'])):
                raise RecordsError('Observation obligations must be a list of method identities')
            if receipt['actual_exit_code'] is not None and type(receipt['actual_exit_code']) is not int:
                raise RecordsError('Observation actual_exit_code must be an integer or null')
            for field in ('source_before', 'source_after'):
                ProgressionPolicy._source(receipt[field], f'Observation {field}')
            if receipt['method'] not in by_id:
                raise RecordsError('Observation must belong to a declared method')
            method = by_id[receipt['method']]
            if any(receipt[k] != method[k] for k in ('argv', 'cwd', 'environment')):
                raise RecordsError('Observation command must match its declared method')
            if (receipt['source_before'] != source or receipt['source_after'] != source
                    or receipt['commit'] != source['commit'] or receipt['tree'] != source['tree']
                    or receipt['source_unchanged'] is not True):
                reason = 'stale_source'
            if any(type(receipt[k]) is not bool for k in (
                    'capture_complete', 'interpretable', 'timed_out', 'guard', 'passed', 'source_unchanged')):
                raise RecordsError('Observation flags must be booleans')
            if not isinstance(receipt['stdout'], str) or not isinstance(receipt['stderr'], str):
                raise RecordsError('Observation output must be text')
            required_guard = receipt['guard'] or receipt['method'] not in subject_rules
            actual_pass = (receipt['capture_complete'] and receipt['interpretable']
                and not receipt['timed_out'] and type(receipt['actual_exit_code']) is int
                and receipt['actual_exit_code'] == method['expected_exit_code']
                and all(marker in receipt['stdout'] for marker in method['stdout_contains'])
                and all(marker in receipt['stderr'] for marker in method['stderr_contains'])
                and receipt['source_unchanged'])
            if receipt['passed'] != actual_pass:
                raise RecordsError('Contradictory passed flag and actual observation result')
            if not receipt['capture_complete']:
                reason = reason or 'incomplete_capture'
            elif (not receipt['interpretable'] or receipt['timed_out']
                    or receipt['actual_exit_code'] is None or receipt['actual_exit_code'] < 0):
                reason = reason or 'uninterpretable_observation'
            elif required_guard and not actual_pass:
                reason = reason or 'failed_observation'
            if receipt['method'] in subject_rules:
                rule = subject_rules[receipt['method']]
                if (receipt['actual_exit_code'] not in rule['exit_codes']
                        or any(marker not in receipt['stdout'] for marker in rule['stdout_contains'])
                        or any(marker not in receipt['stderr'] for marker in rule['stderr_contains'])):
                    reason = reason or 'uninterpretable_observation'
            if receipt['obligations'] != [receipt['method']]:
                raise RecordsError('Observation obligations must identify the actual declared method')
            covered.update(receipt['obligations'])
        if set(by_id) - covered:
            return reason or 'missing_observation'
        if not completed_receipts(receipts):
            return reason or 'incomplete_capture'
        return reason
