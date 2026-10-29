"""Configured day/week/month alternatives over the same normalized observations."""
from datetime import timedelta
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from .domain import instant


def _inside(path, roots):
    selected = PurePosixPath(path)
    return any(selected == PurePosixPath(root) or PurePosixPath(root) in selected.parents for root in roots)


def history_readouts(facts, coverages, policy, week_start):
    eligible = {item['source_key']: item for item in coverages if item.get('source_path') and
                _inside(item['source_path'], policy['logs']) and item.get('session_id')}
    sessions = {}
    zone = ZoneInfo(policy['timezone'])
    for item in eligible.values():
        if not item.get('first_event_at') or not item.get('last_event_at'):
            continue
        session = item['session_id']
        first = instant(item['first_event_at']).astimezone(zone).date()
        last = instant(item['last_event_at']).astimezone(zone).date()
        if session in sessions:
            first, last = min(first, sessions[session]['first']), max(last, sessions[session]['last'])
        sessions.setdefault(session, {'tokens': 0, 'lines': 0, 'complete': True})
        sessions[session].update(first=first, last=last)
        if item['errors'] or item['partial_tail'] or item.get('source_changed'):
            sessions[session]['complete'] = False
    seen_tokens, seen_edits = set(), set()
    duplicate_tokens = duplicate_edits = 0
    for fact in facts:
        if fact['_witness']['source_key'] not in eligible:
            continue
        session = fact['scope'].get('session')
        if session not in sessions:
            continue
        selected = sessions[session]
        if fact['family'] == 'usage-current' and fact['name'] == 'total_tokens':
            key = fact['entity']
            if key in seen_tokens:
                duplicate_tokens += 1
                continue
            seen_tokens.add(key)
            if fact['availability']['state'] == 'known':
                selected['tokens'] += fact['value']
            else:
                selected['complete'] = False
        if fact['family'] == 'files-skills-checks' and fact['name'] in ('lines_added', 'lines_deleted'):
            path = fact['context'].get('path')
            if not isinstance(path, str) or not any(fnmatchcase(path, pattern) for pattern in policy['include']) or any(
                    fnmatchcase(path, pattern) for pattern in policy['exclude']):
                continue
            if fact['key'] in seen_edits:
                duplicate_edits += 1
                continue
            seen_edits.add(fact['key'])
            if fact['availability']['state'] == 'known':
                selected['lines'] += fact['value']
            else:
                selected['complete'] = False
    daily = {}
    for selected in sessions.values():
        days = (selected['last'] - selected['first']).days + 1
        for offset in range(days):
            day = selected['first'] + timedelta(days=offset)
            row = daily.setdefault(day, {'tokens': 0, 'lines': 0, 'complete': True})
            for name in ('tokens', 'lines'):
                quotient, remainder = divmod(selected[name], days)
                row[name] += quotient + int(offset < remainder)
            row['complete'] &= selected['complete']
    alternatives = {}
    for day, values in sorted(daily.items()):
        periods = {'daily': day.isoformat(), 'weekly': (day - timedelta(days=(day.weekday() - week_start) % 7)).isoformat(),
                   'monthly': day.strftime('%Y-%m')}
        for kind, period in periods.items():
            row = alternatives.setdefault((kind, period), {'tokens': 0, 'lines': 0, 'complete': True})
            row['tokens'] += values['tokens']
            row['lines'] += values['lines']
            row['complete'] &= values['complete']
    result = []
    for (kind, period), row in alternatives.items():
        context = {'period_kind': kind, 'period': period, 'complete': row['complete']}
        for name, value, unit in (('tokens', row['tokens'], 'tokens'), ('changed_lines', row['lines'], 'lines')):
            result.append({'name': name, 'value': value, 'unit': unit, 'kind': 'derived',
                           'availability': {'state': 'known' if row['complete'] else 'partial',
                               'reason': None if row['complete'] else 'Registered source has incomplete observations'},
                           'context': {**context, 'known': value}})
        ratio = row['tokens'] / row['lines'] if row['complete'] and row['lines'] else None
        result.append({'name': 'efficiency', 'value': ratio, 'unit': 'tokens per changed line', 'kind': 'derived',
                       'availability': {'state': 'known' if ratio is not None else 'unknown',
                           'reason': None if ratio is not None else 'Complete nonzero changed-line denominator is unavailable'},
                       'context': context})
    days = sorted(daily)
    metadata = {'files': len(eligible), 'sessions': len(sessions), 'duplicate_responses': duplicate_tokens,
                'duplicate_edits': duplicate_edits, 'first_day': days[0].isoformat() if days else None,
                'last_day': days[-1].isoformat() if days else None,
                'diagnostics': [error for item in eligible.values() for error in item['errors']]}
    return result, metadata
