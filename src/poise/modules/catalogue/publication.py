"""A publication references already reviewed content, never a replacement draft."""
from dataclasses import dataclass
from ..foundation.errors import DomainError
from ..verification.domain import exact_keys


@dataclass(frozen=True)
class DataPublication:
    kind: str
    section: str
    authorization: str

    @classmethod
    def parse(cls,value):
        exact_keys(value,{'kind','section','authorization'},'data publication')
        if value['kind'] not in ('tasks','sprint'):
            raise DomainError('Explicit tasks/sprint publication required')
        if any(not isinstance(value[k],str) or not value[k].strip() for k in ('section','authorization')):
            raise DomainError('Reviewed section and user authorization required')
        return cls(**value)

    def to_dict(self):return {'kind':self.kind,'section':self.section,'authorization':self.authorization}
