"""Project instruction policy and observations, independent of Task lifecycle.

General mechanisms adapted from ERP commit
16c5b906615e57a5b5f89b495c42b79f51eab87e,
ai-assistant/agent/effective-policy-manifest.py: OrderedValues, ReasonMap,
build_manifest profile/universal selection and file_delta. Adaptations: explicit
POSIX file scopes, mandatory/recommended profiles, canonical material identity,
and changes in all public material fields; no ERP phases, aliases or stores.
"""
from dataclasses import dataclass
import hashlib
import re

from ..foundation.errors import DomainError
from ..foundation.validation import configuration_fields


SCHEMA = 'poise-project-instructions-1'
MATERIAL_FIELDS = {'kind', 'id', 'root', 'path', 'scopes', 'required', 'reasons',
                   'available', 'sha256', 'content'}
CHANGE_FIELDS = ('available', 'content', 'reasons', 'required', 'scopes', 'sha256')
RESPONSE_FIELDS = {'schema', 'status', 'project', 'checkout', 'instruction_root',
                   'policy_sha256', 'paths', 'materials', 'diagnostics', 'delta'}


class InstructionInputError(DomainError):
    def __init__(self, code, field, action):
        self.code, self.field, self.action = code, field, action
        super().__init__(f'{code}: {field}; {action}')

    def response(self):
        return {'schema': SCHEMA, 'status': 'rejected',
                'error': {'code': self.code, 'field': self.field, 'action': self.action}}


def text(value):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value


def relative_file(value):
    return (text(value) and not any(c in value for c in '\\:*?[]')
            and all(p not in ('', '.', '..') for p in value.split('/')))


def strings(value):
    return (isinstance(value, list) and all(text(v) for v in value)
            and len(value) == len(set(value)))


def parse_prepare_request(value):
    fields = {'operation', 'project_config', 'checkout', 'policy', 'paths', 'previous'}
    if not isinstance(value, dict):
        raise InstructionInputError('invalid_request', 'request', 'provide:request')
    if set(value) == fields - {'policy'}:
        raise InstructionInputError('invalid_request', 'policy', 'provide:policy')
    if set(value) != fields or value['operation'] != 'prepare':
        raise InstructionInputError('invalid_request', 'request', 'provide:request')
    for field in ('project_config', 'checkout', 'policy'):
        if not text(value[field]) or not value[field].startswith('/'):
            raise InstructionInputError('invalid_request', field, 'provide:' + field)
    if not isinstance(value['paths'], list):
        raise InstructionInputError('invalid_request', 'paths', 'provide:paths')
    return dict(value)


def prepare_paths(value):
    if not value or any(not relative_file(p) for p in value):
        raise InstructionInputError('invalid_paths', 'paths', 'provide:paths')
    return sorted(set(value))


class OrderedValues:
    """Donor ordered deduplication; final public ordering is canonical."""
    def __init__(self):
        self._items = {}

    def add(self, value):
        if value:
            self._items.setdefault(value, None)

    def values(self):
        return list(self._items)


class ReasonMap:
    def __init__(self):
        self._items = {}

    def add(self, key, reason):
        self._items.setdefault(key, OrderedValues()).add(reason)

    def as_json(self):
        return {key: sorted(values.values()) for key, values in sorted(self._items.items())}


@dataclass(frozen=True)
class InstructionProfile:
    name: str
    paths: tuple[str, ...]
    baseline: tuple[str, ...]
    recommended_skills: tuple[str, ...]
    profile_file: str | None


@dataclass(frozen=True)
class InstructionPolicy:
    version: str
    universal_skills: tuple[str, ...]
    profiles: tuple[InstructionProfile, ...]

    @classmethod
    def from_configuration(cls, raw, catalog):
        try:
            value = configuration_fields(raw, {'schema', 'version', 'universal_skills', 'profiles'},
                                         'instruction policy', DomainError)
            if value['schema'] != 'poise-project-instruction-policy-1' or not text(value['version']):
                raise DomainError('Invalid instruction policy version')
            if not strings(value['universal_skills']) or not isinstance(value['profiles'], list):
                raise DomainError('Explicit instruction collections required')
            catalog.select(value['universal_skills'])
            profiles, names = [], set()
            for raw_profile in value['profiles']:
                p = configuration_fields(raw_profile, {'name', 'paths', 'baseline',
                                         'recommended_skills', 'profile_file'}, 'profile', DomainError)
                if not text(p['name']) or ':' in p['name'] or p['name'] in names:
                    raise DomainError('Unique profile names required')
                if not strings(p['paths']) or not p['paths'] or any(
                        not path.endswith('/') or not relative_file(path[:-1]) for path in p['paths']):
                    raise DomainError('Explicit canonical directory prefixes required')
                for field in ('baseline', 'recommended_skills'):
                    if not strings(p[field]):
                        raise DomainError('Explicit skill lists required')
                    catalog.select(p[field])
                if p['profile_file'] is not None and not relative_file(p['profile_file']):
                    raise DomainError('Explicit relative profile file required')
                names.add(p['name'])
                profiles.append(InstructionProfile(p['name'], tuple(p['paths']), tuple(p['baseline']),
                                                   tuple(p['recommended_skills']), p['profile_file']))
            return cls(value['version'], tuple(value['universal_skills']), tuple(profiles))
        except (DomainError, TypeError, ValueError) as exc:
            raise InstructionInputError('invalid_policy', 'policy', 'correct:policy') from exc


@dataclass(frozen=True)
class MaterialSpec:
    kind: str
    id: str
    root: str
    path: str
    scopes: tuple[str, ...]
    required: bool
    reasons: tuple[str, ...]

    def public(self):
        return {'kind': self.kind, 'id': self.id, 'root': self.root, 'path': self.path,
                'scopes': list(self.scopes), 'required': self.required, 'reasons': list(self.reasons)}


def select_materials(policy, catalog, paths, rules):
    selected, reasons = {}, ReasonMap()

    def add(kind, identity, root, path, scopes, required, reason):
        key = (kind, identity, root, path)
        row = selected.setdefault(key, {'scopes': set(), 'required': False})
        row['scopes'].update(scopes)
        row['required'] |= required
        reasons.add(key, reason)

    metadata = {s.id: s.path for s in catalog.skills}
    for skill in policy.universal_skills:
        add('skill', skill, 'instructions', metadata[skill], paths, True, 'universal')
    for profile in policy.profiles:
        scopes = [p for p in paths if any(p.startswith(prefix) for prefix in profile.paths)]
        if not scopes:
            continue
        if profile.profile_file is not None:
            add('profile', profile.name, 'checkout', profile.profile_file, scopes,
                True, 'profile:' + profile.name)
        for field, required in [('baseline', True), ('recommended_skills', False)]:
            label = 'baseline' if required else 'recommended'
            for skill in getattr(profile, field):
                add('skill', skill, 'instructions', metadata[skill], scopes, required,
                    'profile:' + profile.name + ':' + label)
    for path, scopes in rules:
        for scope in scopes:
            add('rule', path, 'checkout', path, [scope], True, 'ancestor:' + scope)
    labels = reasons.as_json()
    return [MaterialSpec(*key, tuple(sorted(row['scopes'])), row['required'], tuple(labels[key]))
            for key, row in sorted(selected.items())]


def material_key(material):
    return ':'.join(material[field] for field in ('kind', 'id', 'root', 'path'))


def diagnostics_for(materials):
    result = []
    for m in materials:
        if not m['available']:
            result.append({'code': 'missing_required_material' if m['required'] else 'missing_recommended_material',
                           'key': material_key(m), 'action': 'restore:' + m['root'] + ':' + m['path']})
    return sorted(result, key=lambda m: (m['code'], m['key'], m['action']))


def status_for(materials):
    return 'needs_inputs' if any(m['required'] and not m['available'] for m in materials) else 'ready'


def validate_previous(previous, context):
    if previous is None:
        return
    try:
        if not (isinstance(previous, dict) and set(previous) == RESPONSE_FIELDS):
            raise ValueError('Invalid previous observation')
        if not (previous['schema'] == SCHEMA and previous['status'] in ('ready', 'needs_inputs')):
            raise ValueError('Invalid previous observation')
        for field in ('project', 'checkout', 'instruction_root'):
            if not (previous[field] == context[field]):
                raise ValueError('Invalid previous observation')
        if not (isinstance(previous['policy_sha256'], str) and re.fullmatch('[0-9a-f]{64}', previous['policy_sha256'])):
            raise ValueError('Invalid previous observation')
        paths = prepare_paths(previous['paths'])
        if not (paths == previous['paths']):
            raise ValueError('Invalid previous observation')
        if not (isinstance(previous['materials'], list)):
            raise ValueError('Invalid previous observation')
        keys, order = set(), []
        for m in previous['materials']:
            if not (isinstance(m, dict) and set(m) == MATERIAL_FIELDS):
                raise ValueError('Invalid previous observation')
            if not (m['kind'] in ('rule', 'profile', 'skill') and text(m['id']) and ':' not in m['id']):
                raise ValueError('Invalid previous observation')
            if not (m['root'] == ('instructions' if m['kind'] == 'skill' else 'checkout')):
                raise ValueError('Invalid previous observation')
            if not (relative_file(m['path']) and type(m['required']) is bool and type(m['available']) is bool):
                raise ValueError('Invalid previous observation')
            if not (strings(m['scopes']) and m['scopes'] and m['scopes'] == sorted(m['scopes'])):
                raise ValueError('Invalid previous observation')
            if not (set(m['scopes']) <= set(paths)):
                raise ValueError('Invalid previous observation')
            if not (strings(m['reasons']) and m['reasons'] and m['reasons'] == sorted(m['reasons'])):
                raise ValueError('Invalid previous observation')
            if m['available']:
                if not (isinstance(m['content'], str)):
                    raise ValueError('Invalid previous observation')
                if not (m['sha256'] == hashlib.sha256(m['content'].encode('utf-8')).hexdigest()):
                    raise ValueError('Invalid previous observation')
            else:
                if not (m['content'] is None and m['sha256'] is None):
                    raise ValueError('Invalid previous observation')
            key = material_key(m)
            if not (key not in keys):
                raise ValueError('Invalid previous observation')
            keys.add(key)
            order.append((m['kind'], m['id'], m['root'], m['path']))
        if not (order == sorted(order)):
            raise ValueError('Invalid previous observation')
        if not (previous['diagnostics'] == diagnostics_for(previous['materials'])):
            raise ValueError('Invalid previous observation')
        if not (previous['status'] == status_for(previous['materials'])):
            raise ValueError('Invalid previous observation')
        delta = previous['delta']
        if delta is not None:
            if not (isinstance(delta, dict) and set(delta) == {'added', 'removed', 'changed'}):
                raise ValueError('Invalid previous observation')
            for field in ('added', 'removed'):
                if not (strings(delta[field]) and delta[field] == sorted(delta[field])):
                    raise ValueError('Invalid previous observation')
            if not (isinstance(delta['changed'], list)):
                raise ValueError('Invalid previous observation')
            changed_keys = []
            for row in delta['changed']:
                if not (isinstance(row, dict) and set(row) == {'key', 'fields'} and text(row['key'])):
                    raise ValueError('Invalid previous observation')
                if not (strings(row['fields']) and row['fields'] and row['fields'] == sorted(row['fields'])):
                    raise ValueError('Invalid previous observation')
                if not (set(row['fields']) <= set(CHANGE_FIELDS)):
                    raise ValueError('Invalid previous observation')
                changed_keys.append(row['key'])
            if not (changed_keys == sorted(set(changed_keys))):
                raise ValueError('Invalid previous observation')
    except (KeyError, TypeError, ValueError, UnicodeError, InstructionInputError) as exc:
        raise InstructionInputError('invalid_previous', 'previous', 'provide:previous') from exc


def material_delta(previous, current):
    """Donor file_delta maps, adapted to identity and all accepted public fields."""
    if previous is None:
        return None
    before = {material_key(m): m for m in previous['materials']}
    after = {material_key(m): m for m in current}
    changed = []
    for key in sorted(before.keys() & after.keys()):
        fields = [field for field in CHANGE_FIELDS if before[key][field] != after[key][field]]
        if fields:
            changed.append({'key': key, 'fields': fields})
    return {'added': sorted(after.keys() - before.keys()),
            'removed': sorted(before.keys() - after.keys()), 'changed': changed}


def public_result(context, policy_sha256, paths, materials, previous):
    return {'schema': SCHEMA, 'status': status_for(materials), **context,
            'policy_sha256': policy_sha256, 'paths': paths, 'materials': materials,
            'diagnostics': diagnostics_for(materials), 'delta': material_delta(previous, materials)}
