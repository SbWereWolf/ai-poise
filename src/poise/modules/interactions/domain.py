"""Observed user-message identities. Reason is supplied, never inferred from wording."""
from dataclasses import dataclass
import hashlib
import json
from ..artifact_factory.domain import exact
from ..foundation.errors import DomainError


@dataclass(frozen=True)
class UserMessage:
    identity: str
    data: str

    @classmethod
    def parse(cls, value, source, reasons):
        exact(value,{'conversation_id','message_id','occurred_at','reason','subject'},'user message')
        for key in ('conversation_id','message_id'):
            if not isinstance(value[key],str) or not value[key].strip():
                raise DomainError(f'{key} must be a stable source-scoped identity')
        if value['reason'] is not None and value['reason'] not in reasons:
            raise DomainError('Unknown message reason; no automatic classification')
        if value['subject'] is not None and (not isinstance(value['subject'],str) or not value['subject']):
            raise DomainError('subject must be a reference or null')
        if value['occurred_at'] is not None and not isinstance(value['occurred_at'],str):
            raise DomainError('occurred_at must be timestamp text or null')
        data=json.dumps({'source':source,**value},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        key=json.dumps([source['id'],value['conversation_id'],value['message_id']],ensure_ascii=False,separators=(',',':'))
        return cls(hashlib.sha256(key.encode()).hexdigest(),data)


class InteractionLedger:
    @staticmethod
    def merge(existing, messages):
        candidate=dict(existing)
        for m in messages:
            if m.identity in candidate and candidate[m.identity]!=m.data:
                raise DomainError('Message identity reused with conflicting event content')
            candidate[m.identity]=m.data
        return candidate
