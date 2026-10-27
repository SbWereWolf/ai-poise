"""Immutable metadata shared by routing consumers and focused decomposition."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .generic import configuration_fields, validate_exact_keys


SCHEMA = "poise-skill-catalog-1"
_FIELDS = {"id", "path", "purpose", "class", "level", "responsibility"}
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _exact(value: object, fields: set[str], where: str) -> None:
    validate_exact_keys(value, fields, where, ValueError)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _instruction_path(value: object) -> bool:
    return (
        _text(value)
        and value == value.strip()
        and not any(char in value for char in "\\:*?[]")
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and value.split("/")[-1] == "SKILL.md"
    )


@dataclass(frozen=True)
class SkillMetadata:
    id: str
    path: str
    purpose: str
    skill_class: str
    level: int | None
    responsibility: str | None

    def as_dict(self) -> dict:
        return {"id": self.id, "path": self.path, "purpose": self.purpose,
                "class": self.skill_class, "level": self.level,
                "responsibility": self.responsibility}


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[SkillMetadata, ...]

    @classmethod
    def from_configuration(cls, document: object) -> SkillCatalog:
        raw = configuration_fields(document, {"schema", "skills"}, "skill catalog", ValueError)
        if isinstance(raw["skills"], list):
            raw["skills"] = [configuration_fields(item, _FIELDS, "skill metadata", ValueError)
                             for item in raw["skills"]]
        return cls.parse(raw)

    @classmethod
    def parse(cls, document: object) -> SkillCatalog:
        _exact(document, {"schema", "skills"}, "skill catalog")
        if document["schema"] != SCHEMA:
            raise ValueError("Unsupported skill catalog schema")
        items = document["skills"]
        if not isinstance(items, list) or not items:
            raise ValueError("skill catalog.skills must be a non-empty list")
        result, ids, paths = [], set(), set()
        for item in items:
            _exact(item, _FIELDS, "skill metadata")
            skill_id = item["id"]
            if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id) or skill_id in ids:
                raise ValueError("skill catalog requires unique kebab-case skill IDs")
            if not _instruction_path(item["path"]) or item["path"] in paths:
                raise ValueError(f"skill {skill_id}: requires a unique canonical relative SKILL.md path")
            if not _text(item["purpose"]):
                raise ValueError(f"skill {skill_id}: purpose must be non-empty text")
            if item["class"] not in ("meta", "common", "working"):
                raise ValueError(f"skill {skill_id}: class must be meta, common or working")
            if item["class"] == "working":
                if type(item["level"]) is not int or item["level"] <= 0:
                    raise ValueError(f"skill {skill_id}: working level must be a positive integer")
                if not _text(item["responsibility"]):
                    raise ValueError(f"skill {skill_id}: working responsibility must be explicit")
            elif item["level"] is not None or item["responsibility"] is not None:
                raise ValueError(f"skill {skill_id}: meta/common level and responsibility must be null")
            ids.add(skill_id)
            paths.add(item["path"])
            result.append(SkillMetadata(skill_id, item["path"], item["purpose"],
                                        item["class"], item["level"], item["responsibility"]))
        return cls(tuple(sorted(result, key=lambda item: item.id)))

    def as_dict(self) -> dict:
        return {"schema": SCHEMA, "skills": [skill.as_dict() for skill in self.skills]}

    def select(self, skill_ids: object) -> list[dict]:
        if (not isinstance(skill_ids, list)
                or any(not isinstance(item, str) or not item for item in skill_ids)
                or len(skill_ids) != len(set(skill_ids))):
            raise ValueError("skill_ids must be an explicit list of unique strings")
        unknown = sorted(set(skill_ids) - {skill.id for skill in self.skills})
        if unknown:
            raise ValueError(f"unknown catalog skills: {unknown}")
        selected = set(skill_ids)
        return [skill.as_dict() for skill in self.skills if skill.id in selected]





MATERIAL_FIELDS = {'kind', 'id', 'root', 'path', 'scopes', 'required', 'reasons',
                   'available', 'content'}
CHANGE_FIELDS = ('available', 'content', 'reasons', 'required', 'scopes')
RESPONSE_FIELDS = {'status', 'project', 'checkout', 'instruction_root',
                   'paths', 'materials', 'diagnostics', 'delta'}


class InstructionInputError(ValueError):
    def __init__(self, code, field, action):
        self.code, self.field, self.action = code, field, action
        super().__init__(f'{code}: {field}; {action}')



def text(value):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value


def relative_file(value):
    return (text(value) and not any(c in value for c in '\\:*?[]')
            and all(p not in ('', '.', '..') for p in value.split('/')))


def strings(value):
    return (isinstance(value, list) and all(text(v) for v in value)
            and len(value) == len(set(value)))


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
                                         'instruction policy', ValueError)
            if value['schema'] != 'poise-project-instruction-policy-1' or not text(value['version']):
                raise ValueError('Invalid instruction policy version')
            if not strings(value['universal_skills']) or not isinstance(value['profiles'], list):
                raise ValueError('Explicit instruction collections required')
            catalog.select(value['universal_skills'])
            profiles, names = [], set()
            for raw_profile in value['profiles']:
                p = configuration_fields(raw_profile, {'name', 'paths', 'baseline',
                                         'recommended_skills', 'profile_file'}, 'profile', ValueError)
                if not text(p['name']) or ':' in p['name'] or p['name'] in names:
                    raise ValueError('Unique profile names required')
                if not strings(p['paths']) or not p['paths'] or any(
                        not path.endswith('/') or not relative_file(path[:-1]) for path in p['paths']):
                    raise ValueError('Explicit canonical directory prefixes required')
                for field in ('baseline', 'recommended_skills'):
                    if not strings(p[field]):
                        raise ValueError('Explicit skill lists required')
                    catalog.select(p[field])
                if p['profile_file'] is not None and not relative_file(p['profile_file']):
                    raise ValueError('Explicit relative profile file required')
                names.add(p['name'])
                profiles.append(InstructionProfile(p['name'], tuple(p['paths']), tuple(p['baseline']),
                                                   tuple(p['recommended_skills']), p['profile_file']))
            return cls(value['version'], tuple(value['universal_skills']), tuple(profiles))
        except (ValueError, TypeError) as exc:
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
        if not (previous['status'] in ('ready', 'needs_inputs')):
            raise ValueError('Invalid previous observation')
        for field in ('project', 'checkout', 'instruction_root'):
            if not (previous[field] == context[field]):
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
            else:
                if not (m['content'] is None):
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


def public_result(context, paths, materials, previous):
    return {'status': status_for(materials), **context,
            'paths': paths, 'materials': materials,
            'diagnostics': diagnostics_for(materials), 'delta': material_delta(previous, materials)}
