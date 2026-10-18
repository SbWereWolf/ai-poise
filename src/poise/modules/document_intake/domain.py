"""Selected text identity and prepared-read decisions; no storage or transport."""
from dataclasses import dataclass
import hashlib
import json


def identity(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class ReadScope:
    caller_key: str
    checkout_key: str
    namespace: str
    episode: dict


@dataclass(frozen=True)
class Selection:
    kind: str
    start: int | None
    end: int | None

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != {'kind', 'start', 'end'}:
            raise ValueError('Selection requires exact boundaries')
        if value['kind'] == 'complete' and value['start'] is None and value['end'] is None:
            return cls(**value)
        if (value['kind'] == 'lines' and type(value['start']) is int and type(value['end']) is int
                and 0 < value['start'] <= value['end']):
            return cls(**value)
        raise ValueError('Unsupported selection')


@dataclass(frozen=True)
class PreparedContent:
    key: str
    path: str
    content: str
    normalized_range: str
    content_sha256: str
    content_bytes: int
    content_lines: int


def read_decision(prepared, generation, cache):
    """Cache rows: epoch, path, hash, range, reader; input ordered by recency."""
    matching = [r for r in cache if r[1] == prepared.path and r[3] == prepared.normalized_range
                and r[4] == 'repository-content-read']
    exact = any(r[0] == generation and r[2] == prepared.content_sha256 for r in matching)
    if exact:
        classification = 'exact_repeat'
    elif matching and matching[0][2] != prepared.content_sha256:
        classification = 'content_changed'
    elif matching and matching[0][0] < generation:
        classification = 'new_context_generation'
    elif any(r[1] == prepared.path and r[3] != prepared.normalized_range
             and r[4] == 'repository-content-read' for r in cache):
        classification = 'new_range'
    else:
        classification = 'first_read'
    result = dict(schema_version=1, key=prepared.key, status='unchanged' if exact else 'content',
                  classification=classification, path=prepared.path, hash=prepared.content_sha256,
                  range=prepared.normalized_range, reader_kind='repository-content-read',
                  context_generation=generation, previous_read_available=exact,
                  content_bytes=prepared.content_bytes, content_lines=prepared.content_lines,
                  content_follows=not exact)
    if not exact:
        result['content'] = prepared.content
    return result
