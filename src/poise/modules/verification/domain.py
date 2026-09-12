from __future__ import annotations
from dataclasses import dataclass
import json
from ..foundation.errors import DomainError
from ..foundation.paths import matches_allowed_path


def exact_keys(value: dict, keys: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise DomainError(f"{where}: требуется точный набор полей {sorted(keys)}")


METHOD_FIELDS = {
    'id', 'argv', 'cwd', 'environment',
    'expected_exit_code', 'stdout_contains', 'stderr_contains',
}
EXPECTATION_FIELDS = {'expected_exit_code', 'stdout_contains', 'stderr_contains'}
PLAN_FIELDS = {
    'responsibility', 'change_surface', 'red_stages', 'green_stages', 'red_failure',
}
RED_FAILURE_FIELDS = {'exit_code', 'stdout_equals', 'stderr_equals'}


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


def _validate_plan(method: dict, where: str, stage_ids: tuple[str, ...] | None = None) -> None:
    plan = method['verification_plan']
    exact_keys(plan, PLAN_FIELDS, f'{where}.verification_plan')
    if not isinstance(plan['responsibility'], str) or not plan['responsibility'].strip():
        raise DomainError(f'{where}.verification_plan.responsibility: требуется непустая строка')
    surface = plan['change_surface']
    if (not isinstance(surface, list) or any(not isinstance(path, str) for path in surface)
            or len(surface) != len(set(surface))):
        raise DomainError(f'{where}.verification_plan.change_surface: требуется список уникальных путей')
    for path in surface:
        _repository_path(path, f'{where}.verification_plan.change_surface')
    source = method.get('source_under_test')
    if not surface and (not isinstance(source, dict) or source.get('kind') != 'external'):
        raise DomainError(
            f'{where}.verification_plan.change_surface: пустая поверхность допустима только для external source'
        )
    planned = []
    for field in ('red_stages', 'green_stages'):
        values = plan[field]
        if (not isinstance(values, list) or any(not isinstance(stage, str) or not stage for stage in values)
                or len(values) != len(set(values))):
            raise DomainError(f'{where}.verification_plan.{field}: требуется список уникальных этапов')
        planned.extend(values)
    if not planned or len(planned) != len(set(planned)):
        raise DomainError(f'{where}.verification_plan: red_stages/green_stages должны быть непустыми и не пересекаться')
    if stage_ids is not None:
        unknown = set(planned) - set(stage_ids)
        if unknown:
            raise DomainError(f'{where}.verification_plan: неизвестные этапы {sorted(unknown)}')
    failure = plan['red_failure']
    if plan['red_stages']:
        exact_keys(failure, RED_FAILURE_FIELDS, f'{where}.verification_plan.red_failure')
        if type(failure['exit_code']) is not int or failure['exit_code'] == 0:
            raise DomainError(f'{where}.verification_plan.red_failure.exit_code: требуется ненулевой int')
        for field in ('stdout_equals', 'stderr_equals'):
            if not isinstance(failure[field], str):
                raise DomainError(f'{where}.verification_plan.red_failure.{field}: требуется строка')
        if method['expected_exit_code'] != failure['exit_code']:
            raise DomainError(f'{where}.verification_plan.red_failure.exit_code не совпадает с expected_exit_code')
        if plan['green_stages']:
            raise DomainError(f'{where}.verification_plan: один метод не может одновременно быть RED и GREEN')
        if (any(marker not in failure['stdout_equals'] for marker in method['stdout_contains'])
                or any(marker not in failure['stderr_equals'] for marker in method['stderr_contains'])):
            raise DomainError(f'{where}.verification_plan.red_failure не содержит declared stdout/stderr markers')
    elif failure is not None:
        raise DomainError(f'{where}.verification_plan.red_failure должен быть null без red_stages')
    if plan['green_stages'] and method['expected_exit_code'] != 0:
        raise DomainError(f'{where}.verification_plan.green_stages требуют expected_exit_code=0')


def validate_method(
    method: dict,
    where: str,
    *,
    require_source: bool = False,
    require_plan: bool = False,
    stage_ids: tuple[str, ...] | None = None,
) -> None:
    fields = METHOD_FIELDS | ({'source_under_test'} if require_source else set())
    if not require_source and isinstance(method, dict) and 'source_under_test' in method:
        fields.add('source_under_test')
    if require_plan:
        fields.add('verification_plan')
    elif isinstance(method, dict) and 'verification_plan' in method:
        fields.add('verification_plan')
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
    if 'verification_plan' in method:
        _validate_plan(method, where, stage_ids)


def _without(method: dict, fields: set[str]) -> dict:
    return {key: value for key, value in method.items() if key not in fields}


def _validate_relationships(entries: tuple[RegisteredCheck, ...]) -> None:
    for index, left in enumerate(entries):
        left_method = left.to_dict()['method']
        for right in entries[index + 1:]:
            right_method = right.to_dict()['method']
            if _without(left_method, {'id', 'verification_plan'}) == _without(
                right_method, {'id', 'verification_plan'}
            ):
                raise DomainError(
                    f'семантический дубликат методов {left.method_id} и {right.method_id}'
                )
            if (set(left.stages) & set(right.stages)
                    and _without(left_method, {'id', 'verification_plan'} | EXPECTATION_FIELDS)
                    == _without(right_method, {'id', 'verification_plan'} | EXPECTATION_FIELDS)):
                raise DomainError(
                    f'конфликт методов {left.method_id} и {right.method_id} на одном этапе'
                )



@dataclass(frozen=True)
class RegisteredCheck:
    method_id: str
    definition: str
    stages: tuple[str, ...]

    @classmethod
    def parse(
        cls,
        raw: dict,
        stage_ids: tuple[str, ...],
        *,
        require_source: bool = False,
        require_plan: bool = False,
    ) -> RegisteredCheck:
        exact_keys(raw, {"method", "stages"}, "registered method")
        method = raw['method']
        label = f"method {method.get('id')}" if isinstance(method, dict) and method.get('id') else 'method'
        validate_method(
            method,
            label,
            require_source=require_source,
            require_plan=require_plan,
            stage_ids=stage_ids,
        )
        scheduled = raw['stages']
        if (not isinstance(scheduled,list) or any(not isinstance(s,str) for s in scheduled)
                or len(scheduled)!=len(set(scheduled)) or set(scheduled)-set(stage_ids)):
            raise DomainError("Метод имеет неизвестные/повторные этапы")
        if 'verification_plan' in method:
            declared = method['verification_plan']['red_stages'] + method['verification_plan']['green_stages']
            if set(declared) != set(scheduled):
                raise DomainError(
                    f"method {method['id']}: verification_plan schedule {declared} "
                    f"не совпадает с расписанием {scheduled}; исправьте schedule"
                )
        definition=json.dumps(method,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        return cls(method['id'],definition,tuple(scheduled))

    def to_dict(self) -> dict:
        return {'method':json.loads(self.definition),'stages':list(self.stages)}


@dataclass(frozen=True)
class CheckRegistry:
    stages: tuple[str, ...]
    entries: tuple[RegisteredCheck, ...]

    @classmethod
    def from_items(
        cls,
        items: list[dict],
        stages: tuple[str, ...],
        *,
        require_source: bool = False,
        require_plan: bool = False,
    ) -> CheckRegistry:
        if not isinstance(items,list):
            raise DomainError("Реестр методов должен быть списком")
        entries=tuple(RegisteredCheck.parse(
            raw,
            stages,
            require_source=require_source,
            require_plan=require_plan,
        ) for raw in items)
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
        registry=cls.from_items(
            [{'method':m,'stages':[s for s in stages if m['id'] in checks[s]]} for m in methods],
            stages,
            require_source=True,
            require_plan=True,
        )
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
        extra=self.from_items(
            additions,
            self.stages,
            require_source=True,
            require_plan=True,
        )
        current={e.method_id:e for e in self.entries}
        for e in extra.entries:
            if e.method_id in current and current[e.method_id]!=e:
                raise DomainError(f"Запрещена замена точного метода {e.method_id}; зарегистрируйте новый ID")
            current[e.method_id]=e
        entries=tuple(current.values())
        _validate_relationships(entries)
        return CheckRegistry(self.stages,entries)

    def validate_route(self, route) -> None:
        if tuple(node.stage_id for node in route.nodes) != self.stages:
            raise DomainError('Verification plan route не соответствует этапам реестра')
        for entry in self.entries:
            method = entry.to_dict()['method']
            plan = method.get('verification_plan')
            if plan is None or not plan['green_stages'] or not plan['change_surface']:
                continue
            for green_stage in plan['green_stages']:
                self._validate_green_paths(entry.method_id, green_stage, plan['change_surface'], route)

    @staticmethod
    def _validate_green_paths(method_id, green_stage, surface, route) -> None:
        missing = []
        conflicting_paths = []
        for item in surface:
            pending = [(route.entry, (route.entry,))]
            seen = set()
            witness = None
            while pending:
                current, path = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                node = route.node(current)
                if any(matches_allowed_path(item, allowed) for allowed in node.allowed_paths):
                    continue
                if current == green_stage:
                    witness = path
                    break
                for _, target in node.transitions:
                    if target is not None:
                        pending.append((target, path + (target,)))
            if witness is not None:
                missing.append(item)
                conflicting_paths.append('; '.join(
                    f"{stage} allowed_paths={list(route.node(stage).allowed_paths)}"
                    for stage in witness
                ))
        if missing:
            raise DomainError(
                f"method {method_id}: GREEN stage {green_stage} precedes declared "
                f"change_surface {missing}; conflicting route scopes: "
                f"{' | '.join(conflicting_paths)}; исправьте allowed_paths, route или verification_plan"
            )
