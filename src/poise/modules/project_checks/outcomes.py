"""Truthful terminal and aggregate outcomes; transport exit is separate."""


def item_status(item):
    if item.get('kind') == 'reuse':
        return item['source']['status']
    if item.get('kind') == 'preview':
        return 'planned'
    return item.get('status', 'ready')


def aggregate(items, ordinary='passed'):
    if not items:
        return 'ready'
    for item in items:
        status = item_status(item)
        if status not in ('ready', 'planned', 'passed', 'published', 'unchanged'):
            return status
    return ordinary


def exit_code(status):
    if status in ('ready', 'planned', 'passed', 'published', 'unchanged'):
        return 0
    if status == 'invalid':
        return 2
    if status == 'unavailable':
        return 3
    return 1


def cache_metadata(eligible, key, reason):
    return {'eligible': eligible, 'hit': False, 'stored': False, 'key': key,
            'source_result_id': None, 'reason': reason}


def not_executed(selection):
    return {'kind': 'not_executed', 'selection': selection,
            'reason': 'Предшествующий шаг не завершился подтверждённым успехом.'}


def omitted_step(ordinal, selection):
    return {'ordinal': ordinal, 'selection': selection,
            'reason': 'Предшествующий шаг не завершился подтверждённым успехом.'}
