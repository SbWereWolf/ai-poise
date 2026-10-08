from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from ..foundation.errors import DomainError
from ..foundation.paths import matches_allowed_path
from ..foundation.validation import validate_exact_keys


def method_expectation_digest(method: dict) -> str:
    """Identify exact verification expectations, including declared retained inputs."""
    expectation = {
        'expected_exit_code': method['expected_exit_code'],
        'stdout_contains': method['stdout_contains'],
        'stderr_contains': method['stderr_contains'],
        'red_failure': method.get('verification_plan', {}).get('red_failure'),
        'outputs': method.get('outputs', []),
        **({'artifact_inputs': method['artifact_inputs']} if 'artifact_inputs' in method else {}),
    }
    return hashlib.sha256(json.dumps(
        expectation, sort_keys=True, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')).hexdigest()


def exact_keys(value: dict, keys: set[str], where: str) -> None:
    validate_exact_keys(value, keys, where, DomainError)


def declared_executable_obligations(value, catalog, where):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise DomainError(f'{where}: требуется список уникальных refs')
    unknown = set(value) - set(catalog)
    if unknown:
        raise DomainError(
            f'{where} ссылается на неизвестные обязательства {sorted(unknown)}'
        )
    return tuple(value)


METHOD_FIELDS = {
    'id', 'argv', 'cwd', 'environment',
    'expected_exit_code', 'stdout_contains', 'stderr_contains',
}
EXPECTATION_FIELDS = {'expected_exit_code', 'stdout_contains', 'stderr_contains'}
OUTPUT_FIELDS = {'id', 'path', 'required'}
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



def _validate_outputs(method: dict, where: str) -> None:
    if 'POISE_RUN_OUTPUT_DIR' in method['environment']:
        raise DomainError(f'{where}.environment: POISE_RUN_OUTPUT_DIR зарезервирован runner')
    outputs = method['outputs']
    if not isinstance(outputs, list):
        raise DomainError(f'{where}.outputs: требуется список declared outputs')
    ids = set()
    paths = set()
    for index, output in enumerate(outputs):
        label = f'{where}.outputs[{index}]'
        exact_keys(output, OUTPUT_FIELDS, label)
        output_id = output['id']
        if (not isinstance(output_id, str) or not output_id or '/' in output_id
                or '\\' in output_id or output_id in ('.', '..')):
            raise DomainError(f'{label}.id: требуется безопасный уникальный id')
        path = _repository_path(output['path'], f'{label}.path')
        if type(output['required']) is not bool:
            raise DomainError(f'{label}.required: требуется bool')
        if output_id in ids or path in paths:
            raise DomainError(f'{label}: повтор declared output id/path')
        ids.add(output_id); paths.add(path)

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
    if (not surface and not (
            isinstance(source, dict)
            and source.get('kind') in ('external', 'repository')
    )):
        raise DomainError(
            f'{where}.verification_plan.change_surface: пустая поверхность требует '
            'explicit external source либо repository baseline guard'
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
    if (not surface and isinstance(source, dict) and source.get('kind') == 'repository'
            and (plan['red_stages'] or len(plan['green_stages']) != 1)):
        raise DomainError(
            f'{where}.verification_plan.change_surface: repository baseline guard '
            'требует единственный GREEN stage как route entry и не допускает RED'
        )


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
    if isinstance(method, dict) and 'outputs' in method:
        fields.add('outputs')
    if isinstance(method, dict) and 'artifact_inputs' in method:
        fields.add('artifact_inputs')
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
    if 'artifact_inputs' in method:
        from .retention import ArtifactInputs
        ArtifactInputs.parse(method['artifact_inputs'], method['environment'])
    if not isinstance(method['id'], str) or not method['id']:
        raise DomainError(f'{where}: требуется id метода')
    if not isinstance(method['cwd'], str):
        raise DomainError(f'{where}: cwd должен быть задан')
    if 'outputs' in method:
        _validate_outputs(method, where)
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
    evidence_kind: str | None = None
    covers: tuple[str, ...] = ()

    @classmethod
    def parse(
        cls,
        raw: dict,
        stage_ids: tuple[str, ...],
        *,
        require_source: bool = False,
        require_plan: bool = False,
    ) -> RegisteredCheck:
        fields = set(raw) if isinstance(raw, dict) else set()
        if fields not in ({"method", "stages"}, {"method", "stages", "evidence_kind", "covers"}):
            raise DomainError(
                "registered method: требуется точный legacy или current набор полей"
            )
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
        evidence_kind = raw.get('evidence_kind')
        covers = raw.get('covers', [])
        if evidence_kind is not None:
            if evidence_kind != 'executable_test':
                raise DomainError('registered method.evidence_kind: неизвестный вид evidence')
            if (not isinstance(covers, list) or not covers
                    or any(not isinstance(item, str) or not item for item in covers)
                    or len(covers) != len(set(covers))):
                raise DomainError('registered method.covers: требуется непустой список обязательств')
        elif covers:
            raise DomainError('registered method.covers требует evidence_kind')
        definition=json.dumps(method,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        return cls(method['id'],definition,tuple(scheduled),evidence_kind,tuple(covers))

    def to_dict(self) -> dict:
        value = {'method':json.loads(self.definition),'stages':list(self.stages)}
        if self.evidence_kind is not None:
            value.update(evidence_kind=self.evidence_kind, covers=list(self.covers))
        return value


@dataclass(frozen=True)
class RegistrySnapshot:
    revision: int
    entries: tuple[RegisteredCheck, ...]

    @classmethod
    def from_dict(cls, raw: dict) -> RegistrySnapshot:
        exact_keys(raw, {'revision', 'entries'}, 'registry snapshot')
        if type(raw['revision']) is not int or raw['revision'] < 0:
            raise DomainError('registry snapshot.revision: требуется неотрицательный int')
        if not isinstance(raw['entries'], list):
            raise DomainError('Реестр методов должен быть списком')
        stages = []
        for entry in raw['entries']:
            scheduled = entry.get('stages') if isinstance(entry, dict) else None
            if (not isinstance(scheduled, list)
                    or any(not isinstance(stage, str) or not stage for stage in scheduled)):
                raise DomainError('Метод имеет неизвестные/повторные этапы')
            for stage in scheduled:
                if stage not in stages:
                    stages.append(stage)
            method = entry.get('method')
            validate_method(method, 'historical method')
            plan = method.get('verification_plan')
            if plan is not None:
                for stage in plan['red_stages'] + plan['green_stages']:
                    if stage not in stages:
                        stages.append(stage)
        entries = CheckRegistry.from_items(raw['entries'], tuple(stages)).entries
        return cls(raw['revision'], entries)

    def to_dict(self) -> dict:
        return {'revision': self.revision, 'entries': [entry.to_dict() for entry in self.entries]}


@dataclass(frozen=True)
class RegistryRequest:
    request_id: str
    digest: str
    revision: int
    audit: str | None = None

    def to_dict(self) -> dict:
        value = {
            'request_id': self.request_id,
            'digest': self.digest,
            'revision': self.revision,
        }
        if self.audit is not None:
            value['audit'] = json.loads(self.audit)
        return value


@dataclass(frozen=True)
class RegistryChangeResult:
    registry: CheckRegistry
    replayed: bool


@dataclass(frozen=True)
class CheckRegistry:
    stages: tuple[str, ...]
    entries: tuple[RegisteredCheck, ...]
    revision: int = 0
    history: tuple[RegistrySnapshot, ...] = ()
    requests: tuple[RegistryRequest, ...] = ()
    executable_obligations: tuple[str, ...] = ()
    inspection_stages: tuple[str, ...] = ()
    obligation_catalog: tuple[str, ...] = ()

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

    def with_executable_obligations(
        self,
        obligations: tuple[str, ...],
        inspection_stages: tuple[str, ...] = (),
        obligation_catalog: tuple[str, ...] | None = None,
    ) -> CheckRegistry:
        if (type(obligations) is not tuple
                or any(not isinstance(item, str) or not item for item in obligations)
                or len(obligations) != len(set(obligations))):
            raise DomainError('Executable obligations должны быть явным уникальным tuple')
        if (type(inspection_stages) is not tuple
                or any(stage not in self.stages for stage in inspection_stages)
                or len(inspection_stages) != len(set(inspection_stages))):
            raise DomainError('Executable inspection stages должны быть явным уникальным tuple')
        catalog = obligations if obligation_catalog is None else obligation_catalog
        if (type(catalog) is not tuple
                or any(not isinstance(item, str) or not item for item in catalog)
                or len(catalog) != len(set(catalog))
                or set(obligations) - set(catalog)):
            raise DomainError('Executable obligation catalog должен содержать явные уникальные refs')
        return CheckRegistry(
            self.stages,
            self.entries,
            self.revision,
            self.history,
            self.requests,
            obligations,
            inspection_stages,
            catalog,
        )

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
        self._validate_coverage_refs(entries)
        return CheckRegistry(
            self.stages,
            entries,
            self.revision,
            self.history,
            self.requests,
            self.executable_obligations,
            self.inspection_stages,
            self.obligation_catalog,
        )

    @property
    def current_snapshot(self) -> RegistrySnapshot:
        return RegistrySnapshot(self.revision, self.entries)

    def apply_change(self, raw: dict) -> RegistryChangeResult:
        fields = set(raw) if isinstance(raw, dict) else set()
        required_fields = {
            'request_id',
            'expected_revision',
            'operations',
            'executable_obligations',
        }
        if fields != required_fields:
            raise DomainError(
                'registry change: требуется точный набор полей request_id, '
                'expected_revision, operations и executable_obligations'
            )
        request_id = raw['request_id']
        expected = raw['expected_revision']
        operations = raw['operations']
        if not isinstance(request_id, str) or not request_id:
            raise DomainError('registry change.request_id: требуется непустая строка')
        if type(expected) is not int or expected < 0:
            raise DomainError('registry change.expected_revision: требуется неотрицательный int')
        if not isinstance(operations, list):
            raise DomainError('registry change.operations: требуется список')
        required = declared_executable_obligations(
            raw['executable_obligations'],
            self.obligation_catalog,
            'registry change.executable_obligations',
        )
        digest = hashlib.sha256(json.dumps(
            raw, sort_keys=True, ensure_ascii=False, separators=(',', ':')
        ).encode('utf-8')).hexdigest()
        previous = next((item for item in self.requests if item.request_id == request_id), None)
        if previous is not None:
            if previous.digest != digest:
                raise DomainError('registry change.request_id уже использован с другим payload')
            return RegistryChangeResult(self, True)
        if expected != self.revision:
            raise DomainError(
                f'registry change revision conflict: expected {expected}, current {self.revision}'
            )
        if not operations and required == self.executable_obligations:
            raise DomainError('registry change: требуется изменение методов или executable obligations')
        current = {entry.method_id: entry for entry in self.entries}
        order = list(self.method_ids)
        touched = set()
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or 'kind' not in operation or 'method_id' not in operation:
                raise DomainError(f'registry change.operations[{index}]: требуется kind и method_id')
            kind = operation['kind']
            method_id = operation['method_id']
            if not isinstance(method_id, str) or not method_id:
                raise DomainError(f'registry change.operations[{index}].method_id: требуется строка')
            if method_id in touched:
                raise DomainError(f'registry change: повтор операции для {method_id}')
            touched.add(method_id)
            if kind in ('add', 'replace'):
                exact_keys(operation, {'kind', 'method_id', 'registration'}, 'registry operation')
                exists = method_id in current
                if kind == 'add' and exists:
                    raise DomainError(f'registry add: метод {method_id} уже существует')
                if kind == 'replace' and not exists:
                    raise DomainError(f'registry replace: неизвестный метод {method_id}')
                entry = RegisteredCheck.parse(
                    operation['registration'], self.stages, require_source=True, require_plan=True
                )
                if entry.method_id != method_id:
                    raise DomainError('registry operation method_id не совпадает с registration')
                current[method_id] = entry
                if not exists:
                    order.append(method_id)
            elif kind == 'remove':
                exact_keys(operation, {'kind', 'method_id'}, 'registry operation')
                if method_id not in current:
                    raise DomainError(f'registry remove: неизвестный метод {method_id}')
                del current[method_id]
                order.remove(method_id)
            elif kind == 'reschedule':
                exact_keys(operation, {'kind', 'method_id', 'stages'}, 'registry operation')
                if method_id not in current:
                    raise DomainError(f'registry reschedule: неизвестный метод {method_id}')
                entry = current[method_id]
                value = entry.to_dict()
                stages = operation['stages']
                if (not isinstance(stages, list) or not stages
                        or any(not isinstance(stage, str) for stage in stages)
                        or len(stages) != len(set(stages)) or set(stages) - set(self.stages)):
                    raise DomainError('registry reschedule: неизвестные/повторные stages')
                plan = value['method']['verification_plan']
                target = 'red_stages' if plan['red_stages'] else 'green_stages'
                plan[target] = list(stages)
                value['stages'] = list(stages)
                current[method_id] = RegisteredCheck.parse(
                    value, self.stages, require_source=True, require_plan=True
                )
            else:
                raise DomainError(f'registry operation: неизвестный kind {kind!r}')
        entries = tuple(current[method_id] for method_id in order)
        _validate_relationships(entries)
        self._validate_coverage_refs(entries)
        revision = self.revision + 1
        updated = CheckRegistry(
            self.stages,
            entries,
            revision,
            self.history + (self.current_snapshot,),
            self.requests + (RegistryRequest(request_id, digest, revision),),
            required,
            self.inspection_stages,
            self.obligation_catalog,
        )
        return RegistryChangeResult(updated, False)

    def bind_request_audit(self, request_id: str, audit: dict) -> CheckRegistry:
        encoded = json.dumps(
            audit, sort_keys=True, ensure_ascii=False, separators=(',', ':')
        )
        found = False
        requests = []
        for request in self.requests:
            if request.request_id != request_id:
                requests.append(request)
                continue
            found = True
            if request.audit is not None and request.audit != encoded:
                raise DomainError(
                    'registry change.request_id уже связан с другим audit receipt'
                )
            requests.append(RegistryRequest(
                request.request_id, request.digest, request.revision, encoded
            ))
        if not found:
            raise DomainError('registry change audit requires a recorded request')
        return CheckRegistry(
            self.stages,
            self.entries,
            self.revision,
            self.history,
            tuple(requests),
            self.executable_obligations,
            self.inspection_stages,
            self.obligation_catalog,
        )

    def _validate_coverage_refs(self, entries: tuple[RegisteredCheck, ...]) -> None:
        if not self.obligation_catalog:
            return
        unknown = {
            obligation for entry in entries for obligation in entry.covers
            if obligation not in self.obligation_catalog
        }
        if unknown:
            raise DomainError(f'Executable coverage ссылается на неизвестные обязательства {sorted(unknown)}')

    def validate_inspection_exit(self, obligations: tuple[str, ...] | None = None) -> None:
        required = self.executable_obligations if obligations is None else obligations
        executable = [
            entry for entry in self.entries
            if entry.evidence_kind == 'executable_test'
            and json.loads(entry.definition)['expected_exit_code'] == 0
            and json.loads(entry.definition)['verification_plan']['green_stages']
        ]
        if not executable:
            raise DomainError('Для выхода из inspection требуется текущий GREEN executable-test')
        covered = {item for entry in executable for item in entry.covers}
        missing = [item for item in required if item not in covered]
        if missing:
            raise DomainError(f'Текущие GREEN executable-test не покрывают {missing}')

    def to_state(self) -> dict:
        return {
            'revision': self.revision,
            'history': [snapshot.to_dict() for snapshot in self.history],
            'requests': [request.to_dict() for request in self.requests],
            'executable_obligations': list(self.executable_obligations),
        }

    def restore_state(self, raw: dict | None) -> CheckRegistry:
        if raw is None:
            return self
        exact_keys(raw, {'revision', 'history', 'requests', 'executable_obligations'}, 'registry state')
        revision = raw['revision']
        if type(revision) is not int or revision < 0:
            raise DomainError('registry state.revision: требуется неотрицательный int')
        history = tuple(RegistrySnapshot.from_dict(item) for item in raw['history'])
        requests = tuple(RegistryRequest(
            item['request_id'],
            item['digest'],
            item['revision'],
            None if 'audit' not in item else json.dumps(
                item['audit'], sort_keys=True, ensure_ascii=False, separators=(',', ':')
            ),
        ) for item in raw['requests'])
        obligations = declared_executable_obligations(
            raw['executable_obligations'],
            self.obligation_catalog,
            'registry state.executable_obligations',
        )
        return CheckRegistry(
            self.stages,
            self.entries,
            revision,
            history,
            requests,
            obligations,
            self.inspection_stages,
            self.obligation_catalog,
        )

    def validate_route(self, route) -> None:
        if tuple(node.stage_id for node in route.nodes) != self.stages:
            raise DomainError('Verification plan route не соответствует этапам реестра')
        for entry in self.entries:
            method = entry.to_dict()['method']
            plan = method.get('verification_plan')
            if plan is None or not plan['green_stages']:
                continue
            if not plan['change_surface']:
                source = method.get('source_under_test')
                if (isinstance(source, dict) and source.get('kind') == 'repository'
                        and plan['green_stages'] != [route.entry]):
                    raise DomainError(
                        f"method {entry.method_id}: repository baseline guard with empty "
                        f"change_surface must run only at route entry {route.entry}"
                    )
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
