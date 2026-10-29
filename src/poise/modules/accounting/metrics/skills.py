"""Observed skill selection, references and static content read models."""
from collections import defaultdict
from copy import deepcopy
import re


TASK_SKILL_FIELDS = ('task_path', 'finding_id', 'relation', 'findings_despite_selected_read', 'findings_associated_missing')
TASK_REFERENCE_FIELDS = ('task_path', 'relation', 'finding_task', 'association', 'finding_recurrence_findings', 'finding_recurrence_tasks')


def metric(name, value, unit, kind, context):
    return {'name': name, 'value': value, 'unit': unit, 'kind': kind,
            'availability': {'state': 'known' if value is not None else 'unknown',
                             'reason': None if value is not None else 'Required observed denominator or policy is unavailable'},
            'context': deepcopy(context)}


def unavailable(fields, taskful):
    return {name: {'source_id': 'task-adapter', 'state': 'unknown' if taskful else 'not_applicable',
                   'reason': 'No explicit Task finding association is supplied' if taskful else 'No Task association is available'}
            for name in fields}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def skill_readouts(facts, observations, config):
    policies, routings, inventories = {}, {}, []
    for item in observations:
        fields, session = item['fields'], item['scope'].get('session')
        if fields.get('record_type') == 'effective-skill-policy':
            policies[fields['session_id']] = fields
        elif fields.get('record_type') == 'skill-routing':
            routings[session] = fields
        elif fields.get('record_type') == 'skill-inventory':
            inventories.append(fields)
    inventory = inventories[-1] if inventories else None
    reads = [row for row in facts if row['family'] == 'files-skills-checks' and row['name'] == 'output_bytes'
             and row['context'].get('skill')]
    if inventory is None and not policies and not reads:
        return {'skill-selection': [], 'skill-references': []}, {}, {}, None
    exposure = {(row['scope']['session'], row['context']['source_id']): row['value']
                for row in facts if row['family'] == 'exposure' and row['name'] == 'gross_context_exposure'
                and row['availability']['state'] == 'known'}
    sessions = {row['scope']['session'] for row in facts if row['scope'].get('session')}
    taskful = any(row['scope'].get('task') for row in facts)
    names = {row['context']['skill'] for row in reads}
    for policy in policies.values():
        names.update(policy['skills'])
        names.update(policy.get('recommended_skills', []))
    if inventory:
        names.update(row['skill'] for row in inventory['skills'])
    per_skill, per_session, contexts = {}, {}, {}
    for session in sorted(sessions):
        policy, routing = policies.get(session), routings.get(session)
        paths = {row['context']['path'] for row in facts if row['scope'].get('session') == session
                 and row['family'] == 'files-skills-checks' and row['context'].get('path')}
        required, recommended, reasons = set(), set(), defaultdict(list)
        if routing is not None:
            for kind, target in (('required_path_rules', required), ('recommended_path_rules', recommended)):
                for rule in routing.get(kind, []):
                    profiles = set() if policy is None else set(policy.get('profiles', []))
                    if rule.get('profiles_any') and not profiles.intersection(rule['profiles_any']):
                        continue
                    if any(re.search(rule['path_regex'], path) for path in paths):
                        for name in rule['skills']:
                            target.add(name)
                            reasons[name].append('path-rule:' + rule['name'])
            signals = [] if policy is None else policy.get('risk_triggers', [])
            for kind, target in (('required_signal_skills', required), ('recommended_signal_skills', recommended)):
                for signal in signals:
                    target.update(routing.get(kind, {}).get(signal, []))
            profiles = set() if policy is None else set(policy.get('profiles', []))
            if len(profiles.intersection(routing.get('cross_profile_contract_profiles', []))) > 1:
                required.update(routing.get('cross_profile_skills', []))
        names.update(required | recommended)
        contexts[session] = policy, routing, required, recommended, reasons
    for session, (policy, routing, required, recommended, reasons) in contexts.items():
        for skill in sorted(names):
            own = [row for row in reads if row['scope']['session'] == session and row['context']['skill'] == skill]
            selected = policy is not None and skill in policy['skills']
            core = any(row['context']['kind'] == 'core' for row in own)
            references = [row for row in own if row['context']['kind'] == 'reference']
            raw = sum(row['value'] for row in own if row['availability']['state'] == 'known')
            repeated = sum(row['value'] for row in own if row['availability']['state'] == 'known' and row['context']['repeated'])
            context = {'skill': skill, 'session_id': session, 'phase': None if policy is None else policy.get('phase'),
                       'policy_available': policy is not None, 'surface_evaluable': routing is not None,
                       'selected': selected, 'policy_recommended': policy is not None and skill in policy.get('recommended_skills', []),
                       'required_by_surface': skill in required, 'recommended_by_surface': skill in recommended,
                       'core_read': core, 'selection_reasons': [] if policy is None else policy.get('reasons', {}).get('skills', {}).get(skill, []),
                       'surface_reasons': reasons[skill],
                       'policy_hash': None if policy is None else policy.get('manifest_hash'),
                       'field_availability': unavailable(TASK_SKILL_FIELDS, taskful)}
            values = {'sessions_selected': int(selected), 'sessions_core_read': int(core),
                      'sessions_required': int(skill in required), 'selected_instances': int(selected),
                      'required_instances': int(skill in required), 'over_selected_instances': int(selected and skill not in required),
                      'missed_instances': int(skill in required and not selected), 'selected_not_read_instances': int(selected and not core),
                      'reference_reads': len(references), 'raw_read_bytes': raw, 'repeated_read_bytes': repeated,
                      'selection_precision_proxy': ratio(int(selected and skill in required), int(selected)),
                      'selection_recall_proxy': ratio(int(selected and skill in required), int(skill in required)),
                      'context_exposure': sum(exposure.get((session, row['context']['call_id']), 0) for row in own)}
            per_session[session, skill] = (values, context)
    selection = []
    for skill in sorted(names):
        entries = [(values, context) for (session, name), (values, context) in per_session.items() if name == skill]
        if not entries:
            per_skill[skill] = ({'sessions_selected': 0, 'sessions_core_read': 0, 'sessions_required': 0,
                'selected_instances': 0, 'required_instances': 0, 'over_selected_instances': 0, 'missed_instances': 0,
                'selected_not_read_instances': 0, 'reference_reads': 0, 'raw_read_bytes': 0,
                'repeated_read_bytes': 0, 'context_exposure': 0}, {'skill': skill, 'sample_session_ids': []})
            continue
        values = {name: sum(row[name] for row, _ in entries) for name in entries[0][0]
                  if name not in ('selection_precision_proxy', 'selection_recall_proxy')}
        matched = sum(int(ctx['selected'] and ctx['required_by_surface']) for _, ctx in entries)
        values.update(selection_precision_proxy=ratio(matched, values['selected_instances']),
                      selection_recall_proxy=ratio(matched, values['required_instances']))
        context = entries[0][1] if len(entries) == 1 else {'skill': skill, 'sessions': [ctx['session_id'] for _, ctx in entries],
                                                        'field_availability': unavailable(TASK_SKILL_FIELDS, taskful)}
        context = {**context, 'sample_session_ids': sorted({ctx['session_id'] for values, ctx in entries
                    if ctx['selected'] or ctx['required_by_surface'] or ctx['core_read'] or values['reference_reads']})}
        per_skill[skill] = (values, context)
        for name, value in values.items():
            unit = 'ratio' if name.endswith('_proxy') else 'UTF-8 bytes' if name.endswith('_bytes') else 'estimated tokens multiplied by calls' if name == 'context_exposure' else 'count'
            kind = 'proxy' if name.endswith('_proxy') or name == 'context_exposure' else 'observed' if name.endswith('_bytes') else 'derived'
            selection.append(metric(name, value, unit, kind, context))
    summary_names = ('selected_instances', 'required_instances', 'over_selected_instances', 'missed_instances',
                     'selected_not_read_instances', 'reference_reads', 'raw_read_bytes', 'repeated_read_bytes')
    summary = {name: sum(values[name] for values, _ in per_skill.values()) for name in summary_names}
    matched = sum(int(ctx['selected'] and ctx['required_by_surface']) for _, ctx in per_session.values())
    summary.update(selection_precision_proxy=ratio(matched, summary['selected_instances']),
                   selection_recall_proxy=ratio(matched, summary['required_instances']),
                   policy_coverage=ratio(len(sessions.intersection(policies)), len(sessions)))
    for name, value in summary.items():
        unit = 'ratio' if name.endswith('_proxy') or name == 'policy_coverage' else 'UTF-8 bytes' if name.endswith('_bytes') else 'count'
        kind = 'proxy' if unit == 'ratio' else 'observed' if name.endswith('_bytes') else 'derived'
        selection.append(metric(name, value, unit, kind, {'scope': 'summary'}))
    references, reference_stats = [], {}
    paths = sorted({row['context']['path'] for row in reads if row['context']['kind'] == 'reference'})
    for path in paths:
        own = [row for row in reads if row['context'].get('path') == path]
        skill = own[0]['context']['skill']
        own_sessions = {row['scope']['session'] for row in own}
        known = {session for session in own_sessions if session in policies}
        triggered = {session for session in known if any(
            item.get('path') == path for item in policies[session].get('triggered_references', [])) or
            path in policies[session].get('reasons', {}).get('references', {})}
        context = {'skill': skill, 'path': path, 'session_id': next(iter(own_sessions)) if len(own_sessions) == 1 else None,
                   'trigger_known': own_sessions <= known, 'triggered': bool(triggered),
                   'untriggered_read': bool(known - triggered), 'field_availability': unavailable(TASK_REFERENCE_FIELDS, taskful)}
        values = {'sessions': len(own_sessions), 'reads': len(own),
                  'raw_read_bytes': sum(row['value'] for row in own if row['availability']['state'] == 'known'),
                  'repeated_read_bytes': sum(row['value'] for row in own if row['availability']['state'] == 'known' and row['context']['repeated']),
                  'untriggered_sessions': len(known - triggered), 'unknown_trigger_sessions': len(own_sessions - known),
                  'context_exposure': sum(exposure.get((row['scope']['session'], row['context']['call_id']), 0) for row in own)}
        reference_stats[path] = (values, context)
        for name, value in values.items():
            unit = 'UTF-8 bytes' if name.endswith('_bytes') else 'estimated tokens multiplied by calls' if name == 'context_exposure' else 'count'
            kind = 'proxy' if name == 'context_exposure' else 'derived' if name in ('untriggered_sessions', 'unknown_trigger_sessions') else 'observed'
            references.append(metric(name, value, unit, kind, context))
    return {'skill-selection': selection, 'skill-references': references}, per_skill, reference_stats, inventory


def content_readouts(inventory, per_skill, reference_stats, config):
    if inventory is None:
        return []
    result, priorities, measured = [], [], 0
    digests = defaultdict(set)
    for skill in inventory['skills']:
        for file in [skill['core'], *skill['references']]:
            if file['normalized_content_sha256']:
                digests[file['normalized_content_sha256']].add(file['path'])
    duplicates = [sorted(paths) for paths in digests.values() if len(paths) > 1]
    duplicate_paths = {path for paths in duplicates for path in paths}
    for static in inventory['skills']:
        name = static['skill']
        observed, selected_context = per_skill[name]
        samples = len(selected_context['sample_session_ids'])
        sufficient = samples >= config['minimum_skill_sessions']
        measured += sufficient
        protected = name in config['protected_skills']
        issues = ['unreachable-from-skill-graph'] if static['unreachable_paths'] else []
        if sufficient:
            for field, issue in (('over_selected_instances', 'observed-over-selection'),
                                 ('missed_instances', 'observed-missed-trigger'),
                                 ('selected_not_read_instances', 'selected-core-not-read'),
                                 ('repeated_read_bytes', 'repeated-read-cost')):
                if observed[field] > 0:
                    issues.append(issue)
        if sufficient and not protected and issues:
            priorities.append((observed['context_exposure'], observed['repeated_read_bytes'], name))
        context = {**selected_context, 'row_type': 'skill', 'path': static['core']['path'], 'protected': protected,
                   'sample_status': 'measured' if sufficient else 'insufficient-data', 'issues': issues,
                   'unreachable_paths': static['unreachable_paths'],
                   'normalized_content_sha256': static['core']['normalized_content_sha256'],
                   'audit_action': 'observe-only' if protected else 'evidence-review' if sufficient and issues else 'keep'}
        counts = {'core_bytes': static['core']['bytes'],
                  'reachable_reference_bytes': sum(row['bytes'] for row in static['references']),
                  'reachable_reference_count': len(static['references']), 'sample_sessions': samples,
                  'raw_read_bytes': observed['raw_read_bytes'], 'repeated_read_bytes': observed['repeated_read_bytes'],
                  'sessions': samples, 'reads': observed['sessions_core_read'], 'context_exposure': observed['context_exposure']}
        for field, value in counts.items():
            unit = 'UTF-8 bytes' if field.endswith('_bytes') else 'estimated tokens multiplied by calls' if field == 'context_exposure' else 'count'
            result.append(metric(field, value, unit, 'proxy' if field == 'context_exposure' else 'observed', context))
        for reference in static['references']:
            values, prior_context = reference_stats.get(reference['path'], (
                {'sessions': 0, 'reads': 0, 'raw_read_bytes': 0, 'repeated_read_bytes': 0,
                 'unknown_trigger_sessions': 0, 'untriggered_sessions': 0, 'context_exposure': 0}, {}))
            known = values['sessions'] - values['unknown_trigger_sessions']
            matched = known - values['untriggered_sessions']
            sufficient_ref = values['sessions'] >= config['minimum_reference_sessions']
            issues = ['normalized-exact-duplicate'] if reference['path'] in duplicate_paths else []
            if sufficient_ref:
                if values['untriggered_sessions']:
                    issues.append('read-without-trigger')
                if values['repeated_read_bytes']:
                    issues.append('repeated-read-cost')
            context = {**prior_context, 'skill': name, 'row_type': 'reference', 'path': reference['path'],
                       'sample_status': 'measured' if sufficient_ref else 'insufficient-data', 'issues': issues,
                       'normalized_content_sha256': reference['normalized_content_sha256']}
            ratios = {'read_rate_per_core_read_session': ratio(values['sessions'], observed['sessions_core_read']),
                      'trigger_match_rate_on_known_read_sessions': ratio(matched, known),
                      'repeated_byte_share': ratio(values['repeated_read_bytes'], values['raw_read_bytes'])}
            for field, value in ratios.items():
                result.append(metric(field, value, 'ratio', 'proxy', context))
    summary = {'skills_in_repository': len(inventory['skills']), 'skills_in_report': len(per_skill),
               'skills_measured': measured, 'skills_insufficient_data': len(inventory['skills']) - measured,
               'references_returned': sum(len(row['references']) for row in inventory['skills']),
               'priority_candidates': len(sorted(priorities, key=lambda item: (-item[0], -item[1], item[2]))[:config['priority_limit']]),
               'unreachable_skill_local_files': sum(len(row['unreachable_paths']) for row in inventory['skills']),
               'global_unreachable_agent_files': len(inventory['unreachable_paths']),
               'normalized_exact_duplicate_groups': len(duplicates)}
    for field, value in summary.items():
        result.append(metric(field, value, 'count', 'observed', {'scope': 'summary',
                             'unreachable_paths': inventory['unreachable_paths'], 'duplicate_groups': duplicates}))
    return result
