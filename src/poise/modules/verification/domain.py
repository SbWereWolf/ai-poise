from __future__ import annotations
from dataclasses import dataclass
import json
import math
from ..foundation.errors import DomainError


def exact_keys(value: dict, keys: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise DomainError(f"{where}: требуется точный набор полей {sorted(keys)}")


def validate_method(method: dict, where: str) -> None:
    exact_keys(method, {'id', 'argv', 'cwd', 'environment', 'timeout_seconds',
                       'expected_exit_code', 'stdout_contains', 'stderr_contains'}, where)
    if not isinstance(method['argv'], list) or not method['argv'] or any(
        not isinstance(s, str) for s in method['argv']
    ):
        raise DomainError(f'{where}: требуется точный непустой argv')
    if not isinstance(method['environment'], dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k,v in method['environment'].items()
    ):
        raise DomainError(f'{where}: environment должен содержать строки')
    if type(method['timeout_seconds']) not in (int, float) or not math.isfinite(method['timeout_seconds']) or method['timeout_seconds'] <= 0:
        raise DomainError(f'{where}: необходим конечный положительный timeout')
    if type(method['expected_exit_code']) is not int:
        raise DomainError(f'{where}: expected_exit_code — целое число')
    for field in ('stdout_contains', 'stderr_contains'):
        if not isinstance(method[field], list) or any(not isinstance(x, str) for x in method[field]):
            raise DomainError(f'{where}.{field}: требуется список строк')
    if not isinstance(method['id'], str) or not method['id']:
        raise DomainError(f'{where}: требуется id метода')
    if not isinstance(method['cwd'], str):
        raise DomainError(f'{where}: cwd должен быть задан')



@dataclass(frozen=True)
class RegisteredCheck:
    method_id: str
    definition: str
    stages: tuple[str, ...]

    @classmethod
    def parse(cls, raw: dict, stage_ids: tuple[str, ...]) -> RegisteredCheck:
        exact_keys(raw, {"method", "stages"}, "registered method")
        validate_method(raw['method'], 'method')
        scheduled = raw['stages']
        if (not isinstance(scheduled,list) or any(not isinstance(s,str) for s in scheduled)
                or len(scheduled)!=len(set(scheduled)) or set(scheduled)-set(stage_ids)):
            raise DomainError("Метод имеет неизвестные/повторные этапы")
        definition=json.dumps(raw['method'],sort_keys=True,ensure_ascii=False,separators=(',',':'))
        return cls(raw['method']['id'],definition,tuple(scheduled))

    def to_dict(self) -> dict:
        return {'method':json.loads(self.definition),'stages':list(self.stages)}


@dataclass(frozen=True)
class CheckRegistry:
    stages: tuple[str, ...]
    entries: tuple[RegisteredCheck, ...]

    @classmethod
    def from_items(cls, items: list[dict], stages: tuple[str, ...]) -> CheckRegistry:
        if not isinstance(items,list):
            raise DomainError("Реестр методов должен быть списком")
        entries=tuple(RegisteredCheck.parse(raw,stages) for raw in items)
        if len({e.method_id for e in entries})!=len(entries):
            raise DomainError("Повтор идентификатора метода")
        return cls(stages,entries)

    @classmethod
    def from_task(cls, methods: list[dict], checks: dict, stages: tuple[str, ...]) -> CheckRegistry:
        if not isinstance(methods,list) or not isinstance(checks,dict) or set(checks)!=set(stages):
            raise DomainError("Требуются методы и явное расписание каждого этапа")
        for values in checks.values():
            if not isinstance(values,list) or any(not isinstance(v,str) for v in values):
                raise DomainError("Расписание должно содержать списки ID методов")
        for method in methods:
            validate_method(method, 'method')
        registry=cls.from_items([{'method':m,'stages':[s for s in stages if m['id'] in checks[s]]} for m in methods],stages)
        missing={v for values in checks.values() for v in values}-set(registry.method_ids)
        if missing:
            raise DomainError(f"Расписание ссылается на неизвестные методы: {sorted(missing)}")
        return registry

    @property
    def method_ids(self) -> tuple[str, ...]:
        return tuple(e.method_id for e in self.entries)

    def extend(self, additions: list[dict]) -> CheckRegistry:
        extra=self.from_items(additions,self.stages)
        current={e.method_id:e for e in self.entries}
        for e in extra.entries:
            if e.method_id in current and current[e.method_id]!=e:
                raise DomainError(f"Запрещена замена точного метода {e.method_id}; зарегистрируйте новый ID")
            current[e.method_id]=e
        return CheckRegistry(self.stages,tuple(current.values()))
