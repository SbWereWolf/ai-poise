"""External identity and declared inventory are input facts, not workflow authority."""
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
from ..artifact_factory.domain import exact
from ..foundation.errors import DomainError


@dataclass(frozen=True)
class RuntimeIdentity:
    key: str
    kind: str
    external_session: str
    agent_id: str

    @classmethod
    def parse(cls,project,adapter_id,value):
        if not isinstance(value,dict) or value.get('kind') not in ('external','generated'):
            raise DomainError('Explicit external/generated identity required')
        field='session_id' if value['kind']=='external' else 'request_id'
        exact(value,{'kind',field,'agent_id'},'runtime identity')
        for name in (field,'agent_id'):
            if not isinstance(value[name],str) or not value[name].strip():raise DomainError(f'{name} required')
        data=[project,adapter_id,value['kind'],value[field],value['agent_id']]
        key=hashlib.sha256(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        return cls(key,value['kind'],value[field],value['agent_id'])


def validate_inventory(items,required):
    if not isinstance(items,list) or not isinstance(required,list) or any(not isinstance(x,str) or not x for x in required):
        raise DomainError('Capabilities and required set must be explicit lists')
    found={}
    for item in items:
        exact(item,{'id','status','tool_ref','project_path','source','version'},'capability')
        name=item['id']
        if not isinstance(name,str) or not name or name in found:raise DomainError('Unique capability ID required')
        if item['status'] not in ('available','unavailable'):raise DomainError('Unknown availability')
        if item['source'] not in ('runtime_manifest','agent_reported'):raise DomainError('Inventory provenance required')
        for field in ('tool_ref','project_path','version'):
            if item[field] is not None and (not isinstance(item[field],str) or not item[field]):raise DomainError(f'{field}: string or null required')
        if item['status']=='available' and item['tool_ref'] is None:raise DomainError('Available capability needs actual tool reference')
        found[name]=item
    missing=[x for x in required if x not in found or found[x]['status']!='available']
    if missing:raise DomainError(f'Required capabilities unavailable: {missing}')
    return deepcopy(items)
