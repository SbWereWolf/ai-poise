"""Specialized projections of the observed MCP thread/page/turns wire format."""
import json


def thread_views(response, profile):
    from .tool_results import reject
    if profile['content_index'] >= len(response['content']):
        raise ValueError('Thread content item is missing')
    item = response['content'][profile['content_index']]
    if item['type'] != 'text' or not isinstance(item['text'], str):
        raise ValueError('Thread content must be JSON text')
    value = json.loads(item['text'])
    if type(value.get('schemaVersion')) is not int or value['schemaVersion'] != profile['expected_version']:
        reject('unmapped_input', 'format', 'configure:input')
    thread, page, turns = value['thread'], value['page'], value['turns']
    identity, status = thread['id'], thread['status']['type']
    if not isinstance(identity, str) or not isinstance(status, str) or type(page['hasMore']) is not bool or not isinstance(turns, list):
        raise ValueError('Invalid thread state')
    turn_status, message = 'none', ''
    if turns:
        latest = turns[0]
        turn_status = latest['status']
        if not isinstance(turn_status, str): raise ValueError('Invalid turn status')
        error = latest.get('error')
        if error:
            message = error['message']
        else:
            for entry in reversed(latest['items']):
                if entry.get('type') == 'agentMessage':
                    message = entry['text']; break
        if not isinstance(message, str): raise ValueError('Invalid turn message')
    fields = {'thread_id': identity, 'status': status, 'turn_status': turn_status,
              'has_more': 'true' if page['hasMore'] else 'false'}
    brief = profile['brief_template'].format(**fields)
    def expanded(text): return profile['expanded_template'].format(**fields, brief=brief, message=text)
    # This is the common presentation parser, never an edit of the machine payload.
    limit = profile['expanded']
    low, high = 0, len(message)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = expanded(message[:middle])
        if len(candidate) <= limit['chars'] and len(candidate.encode()) <= limit['bytes']:
            low = middle
        else: high = middle - 1
    return brief, expanded(message[:low])
