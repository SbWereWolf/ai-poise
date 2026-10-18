"""Complete protocol rendering and pre-registration admission cost."""
from copy import deepcopy
import json


def canonical_result(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n'


def text_frames(result):
    if result['operation'] != 'read' or not result['items']:
        return canonical_result(result)
    parts = []
    common = {k: v for k, v in result.items() if k != 'items'}
    for item in result['items']:
        header = common | {'item': {k: v for k, v in item.items() if k != 'content'}}
        parts.append(canonical_result(header))
        if item.get('content_follows'):
            parts.append(item['content'])
    return ''.join(parts)


class FullReadCost:
    @staticmethod
    def measure(result, prepared, representation):
        candidate = deepcopy(result)
        bodies = {p.key: p.content for p in prepared if hasattr(p, 'content')}
        if representation == 'json':
            for item in candidate['items']:
                if item['status'] == 'unchanged':
                    item['content'] = bodies[item['key']]
            body = canonical_result(candidate)
        else:
            common = {k: v for k, v in candidate.items() if k != 'items'}
            parts = []
            for item in candidate['items']:
                parts.append(canonical_result(common | {'item': {k: v for k, v in item.items() if k != 'content'}}))
                if item['status'] != 'failed':
                    parts.append(bodies[item['key']])
            body = ''.join(parts)
        return len(body.encode('utf-8')), len(body.splitlines())
