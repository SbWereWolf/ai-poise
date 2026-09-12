from __future__ import annotations
from dataclasses import dataclass
import json
from ..foundation.errors import DomainError


def exact_keys(value: dict, keys: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise DomainError(f"{where}: требуется точный набор полей {sorted(keys)}")


METHOD_FIELDS = {
    'id', 'argv', 'cwd', 'environment',
    'expected_exit_code', 'stdout_contains', 'stderr_contains',
}
EXPECTATION_FIELDS = {'expected_exit_code', 'stdout_contains', 'stderr_contains'}


def _repository_path(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value:
        raise DomainError(f'{where}: требуется repository-relative path, получено {value!r}; исправьте path')
    if value.startswith('/') or '..' in value.split('/'):
        raise DomainError(f'{where}: путь {value!r} должен находиться внутри repository; исправьте path')
    return value


def _validate_source(method: dict, where: str) -> None:
    source = method['source_under_test']
    if not isinstance(source, dict) or 'kind' not in source:
        raise DomainError(f'{where}.source_under_test: требуется явный source contract')
    if source['kind'] == 'external':
        exact_keys(source, {'kind', 'reason'}, f'{where}.source_under_test')
        if not isinstance(source['reason'], str) or not source['reason'].strip():
            raise DomainError(f'{where}.source_under_test.reason: требуется непустая причина')
        return
    if source['kind'] != 'repository':
        raise DomainError(f'{where}.source_under_test: неизвестный kind')
    exact_keys(source, {'kind', 'bindings'}, f'{where}.source_under_test')
    bindings = source['bindings']
    if not isinstance(bindings, list) or not bindings:
        raise DomainError(f'{where}.source_under_test.bindings: требуется непустой список')
    identities = set()
    cwd_count = 0
    for index, binding in enumerate(bindings):
        label = f'{where}.source_under_test.bindings[{index}]'
        if not isinstance(binding, dict) or 'kind' not in binding:
            raise DomainError(f'{label}: требуется binding object')
        if binding['kind'] == 'cwd':
            exact_keys(binding, {'kind', 'path'}, label)
            cwd_count += 1
            identity = ('cwd',)
        elif binding['kind'] == 'environment':
            exact_keys(binding, {'kind', 'name', 'path'}, label)
            name = binding['name']
            if (not isinstance(name, str) or not name or '=' in name or '\x00' in name):
                raise DomainError(f'{label}.name: требуется имя environment binding')
            if name in method['environment']:
                raise DomainError(f'{label}: environment binding конфликтует с environment метода')
            identity = ('environment', name)
        else:
            raise DomainError(f'{label}: неизвестный binding kind')
        _repository_path(binding['path'], f'{label}.path')
        if identity in identities:
            raise DomainError(f'{label}: повторный source_under_test binding')
        identities.add(identity)
    if cwd_count > 1:
        raise DomainError(f'{where}.source_under_test: допустим только один cwd binding')


def validate_method(method: dict, where: str, *, require_source: bool = False) -> None:
    fields = METHOD_FIELDS | ({'source_under_test'} if require_source else set())
    if not require_source and isinstance(method, dict) and 'source_under_test' in method:
        fields.add('source_under_test')
    exact_keys(method, fields, where)
    if not isinstance(method['argv'], list) or not method['argv'] or any(
        not isinstance(s, str) for s in method['argv']
    ):
        raise DomainError(f'{where}: требуется точный непустой argv')
    if not isinstance(method['environment'], dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k,v in method['environment'].items()
    ):
        raise DomainError(f'{where}: environment должен содержать строки')
    if type(method['expected_exit_code']) is not int:
        raise DomainError(f'{where}: expected_exit_code — целое число')
    for field in ('stdout_contains', 'stderr_contains'):
        if not isinstance(method[field], list) or any(not isinstance(x, str) for x in method[field]):
            raise DomainError(f'{where}.{field}: требуется список строк')
    if not isinstance(method['id'], str) or not method['id']:
        raise DomainError(f'{where}: требуется id метода')
    if not isinstance(method['cwd'], str):
        raise DomainError(f'{where}: cwd должен быть задан')
    if 'source_under_test' in method:
        _repository_path(method['cwd'], f'{where}.cwd')
        if method['expected_exit_code'] != 0 and not any(
            marker for field in ('stdout_contains', 'stderr_contains') for marker in method[field]
        ):
            raise DomainError(f'{where}: nonzero failure требует непустой stdout/stderr marker')
        _validate_source(method, where)


def _without(method: dict, fields: set[str]) -> dict:
    return {key: value for key, value in method.items() if key not in fields}


def _validate_relationships(entries: tuple[RegisteredCheck, ...]) -> None:
    for index, left in enumerate(entries):
        left_method = left.to_dict()['method']
        for right in entries[index + 1:]:
            right_method = right.to_dict()['method']
            if _without(left_method, {'id'}) == _without(right_method, {'id'}):
                raise DomainError(
                    f'семантический дубликат методов {left.method_id} и {right.method_id}'
                )
            if (set(left.stages) & set(right.stages)
                    and _without(left_method, {'id'} | EXPECTATION_FIELDS)
                    == _without(right_method, {'id'} | EXPECTATION_FIELDS)):
                raise DomainError(
                    f'конфликт методов {left.method_id} и {right.method_id} на одном этапе'
                )



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
            label = f"method {method.get('id')}" if isinstance(method, dict) and method.get('id') else 'method'
            validate_method(method, label, require_source=True)
        registry=cls.from_items([{'method':m,'stages':[s for s in stages if m['id'] in checks[s]]} for m in methods],stages)
        missing={v for values in checks.values() for v in values}-set(registry.method_ids)
        if missing:
            raise DomainError(f"Расписание ссылается на неизвестные методы: {sorted(missing)}")
        _validate_relationships(registry.entries)
        return registry

    @property
    def method_ids(self) -> tuple[str, ...]:
        return tuple(e.method_id for e in self.entries)

    def extend(self, additions: list[dict]) -> CheckRegistry:
        for addition in additions:
            if not isinstance(addition, dict) or 'method' not in addition:
                raise DomainError('registered method: требуется method')
            validate_method(addition['method'], 'method', require_source=True)
        extra=self.from_items(additions,self.stages)
        current={e.method_id:e for e in self.entries}
        for e in extra.entries:
            if e.method_id in current and current[e.method_id]!=e:
                raise DomainError(f"Запрещена замена точного метода {e.method_id}; зарегистрируйте новый ID")
            current[e.method_id]=e
        entries=tuple(current.values())
        _validate_relationships(entries)
        return CheckRegistry(self.stages,entries)
