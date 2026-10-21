"""Safe observations and comparisons; filesystem and native authority are ports."""
from copy import deepcopy
from dataclasses import dataclass
import json

from ..foundation.errors import PoiseError
from ..foundation.validation import configuration_fields, nonempty


CARD_PREFIX = 'Work context: '
CARD_SCHEMA = 'poise-work-context-1'
CHECKPOINT_SCHEMA = 'poise-work-context-checkpoint-1'


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


@dataclass(frozen=True)
class ContextPolicy:
    data: dict

    @classmethod
    def from_configuration(cls, raw):
        fields = {'reader_policy', 'checkpoint_directory', 'checkpoint_filename', 'summary',
                  'instructions', 'work_owner', 'check_owner'}
        configuration_fields(raw, fields, 'general_context')
        for key in ('reader_policy', 'checkpoint_directory', 'checkpoint_filename'):
            nonempty(raw[key], 'general_context.' + key)
        for key in ('checkpoint_directory', 'checkpoint_filename'):
            if (any(part in ('', '.', '..') for part in raw[key].split('/'))
                    or '\x00' in raw[key] or '\\' in raw[key] or raw[key].startswith('/')):
                raise PoiseError('general_context.' + key + ': relative owned path required')
        if '/' in raw['checkpoint_filename']:
            raise PoiseError('general_context.checkpoint_filename: one filename required')
        summary = configuration_fields(raw['summary'], {'max_bytes', 'max_changed_paths',
            'max_instruction_changes', 'max_coverage_items'}, 'general_context.summary')
        for key in ('max_bytes', 'max_changed_paths', 'max_instruction_changes', 'max_coverage_items'):
            if type(summary[key]) is not int or summary[key] <= 0:
                raise PoiseError('general_context.summary.' + key + ': positive integer required')
        minimum = dict(schema=CARD_SCHEMA, status='partial', scope={}, checkpoint={}, generation={},
                       git={}, instructions={}, coverage={}, check={}, work={}, omitted={}, actions=[])
        if summary['max_bytes'] < len((CARD_PREFIX + encode(minimum)).encode()):
            raise PoiseError('general_context.summary.max_bytes: summary budget cannot fit its protocol')
        instructions = configuration_fields(raw['instructions'], {'catalog', 'root', 'policy', 'paths'},
                                            'general_context.instructions')
        for key in ('catalog', 'root', 'policy'):
            nonempty(instructions[key], 'general_context.instructions.' + key)
        if (not isinstance(instructions['paths'], list) or not instructions['paths']
                or any(not isinstance(path, str) or not path for path in instructions['paths'])):
            raise PoiseError('general_context.instructions.paths: explicit paths required')
        for key in ('work_owner', 'check_owner'):
            value = configuration_fields(raw[key], {'enabled'}, 'general_context.' + key)
            if type(value['enabled']) is not bool:
                raise PoiseError('general_context.' + key + '.enabled: boolean required')
        return cls(deepcopy({key: raw[key] for key in fields}))


@dataclass(frozen=True)
class ContextScope:
    project: str
    checkout: str
    episode: dict

    def public(self):
        return dict(project=self.project, checkout=self.checkout, episode=deepcopy(self.episode))


@dataclass(frozen=True)
class ContextEvent:
    id: int
    kind: str
    source: str | None

    def public(self):
        return dict(id=self.id, kind=self.kind, source=self.source)


@dataclass(frozen=True)
class ContextOutcome:
    card: dict | None
    complete: bool
    reason: str | None = None


@dataclass(frozen=True)
class CheckpointState:
    state: str
    value: dict | None

    @classmethod
    def parse(cls, value, scope):
        if (not isinstance(value, dict)
                or set(value) != {'schema', 'scope', 'event', 'captured', 'consumption'}
                or value.get('schema') != CHECKPOINT_SCHEMA or value.get('scope') != scope.public()
                or not isinstance(value.get('captured'), dict)
                or not isinstance(value.get('event'), dict)
                or type(value['event'].get('id')) is not int or value['event']['id'] <= 0
                or value['event'].get('kind') != 'PreCompact'
                or not isinstance(value.get('consumption'), dict)
                or value['consumption'].get('state') not in ('pending', 'used')):
            return cls('invalid', None)
        captured = value['captured']
        if (set(captured) != {'git', 'instructions', 'coverage', 'work', 'check'}
                or any(not isinstance(item, dict) for item in captured.values())
                or type(captured['coverage'].get('generation')) is not int
                or not isinstance(captured['coverage'].get('ref'), str)
                or not isinstance(captured['instructions'].get('materials'), list)):
            return cls('invalid', None)
        consumption = value['consumption']
        if ((consumption['state'] == 'pending' and set(consumption) != {'state'})
                or (consumption['state'] == 'used' and (
                    set(consumption) != {'state', 'event_id'}
                    or type(consumption['event_id']) is not int or consumption['event_id'] <= 0))):
            return cls('invalid', None)
        materials = captured['instructions']['materials']
        fields = {'kind', 'id', 'root', 'path', 'scopes', 'required', 'reasons', 'available', 'sha256'}
        keys = set()
        for material in materials:
            if (not isinstance(material, dict) or set(material) != fields
                    or any(not isinstance(material[key], str) or not material[key]
                           for key in ('kind', 'id', 'root', 'path'))
                    or any(type(material[key]) is not bool for key in ('required', 'available'))
                    or any(not isinstance(material[key], list)
                           or any(not isinstance(item, str) for item in material[key])
                           for key in ('scopes', 'reasons'))
                    or (material['available'] and (not isinstance(material['sha256'], str)
                        or len(material['sha256']) != 64
                        or any(c not in '0123456789abcdef' for c in material['sha256'])))
                    or (not material['available'] and material['sha256'] is not None)):
                return cls('invalid', None)
            key = tuple(material[field] for field in ('kind', 'id', 'root', 'path'))
            if key in keys:
                return cls('invalid', None)
            keys.add(key)
        return cls(value['consumption']['state'], value)


def compare_observations(previous, current):
    before = previous.get('instructions', {}).get('materials', []) if previous else []
    now = current['instructions']
    old = {(m['kind'], m['id'], m['root'], m['path']): m for m in before}
    new = {(m['kind'], m['id'], m['root'], m['path']): m for m in now.get('materials', [])}
    changed = []
    for key in dict.fromkeys([*old, *new]):
        a, b = old.get(key), new.get(key)
        if previous is not None and (a is None or b is None
                or (a['sha256'], a['available']) != (b['sha256'], b['available'])):
            changed.append(key[3])
    unavailable = any(not m['available'] and m['required'] for m in new.values())
    status = now['status']
    if status == 'ready' and unavailable:
        status = 'needs_inputs'
    git = deepcopy(current['git'])
    old_git = previous.get('git') if previous is not None else None
    comparable = (old_git is not None and old_git.get('status') == 'available'
                  and git.get('status') == 'available')
    git['changed_since_capture'] = (any(old_git.get(key) != git[key] for key in ('head', 'status_sha256'))
                                    if comparable else None)
    return dict(git=git, instructions=dict(status=status, changed=list(dict.fromkeys(changed))))


def continuation_actions(card, materials, policy):
    """Describe current reads; an observation never performs or acknowledges them."""
    actions = []
    checkout = card['scope']['checkout']
    if card['git']['changed_since_capture'] is True:
        actions.append('restore:git; inspect HEAD/worktree at ' + checkout)
    changed = card['instructions']['changed']
    if changed:
        current = [m for m in materials if m['path'] in changed and m['available']]
        selected = current[:policy.data['summary']['max_instruction_changes']]
        addresses = []
        for material in selected:
            root = checkout if material['root'] == 'checkout' else policy.data['instructions']['root']
            addresses.append(root.rstrip('/') + '/' + material['path'])
        parts = ['read ' + address for address in dict.fromkeys(addresses)]
        if len({m['path'] for m in selected}) < len(changed):
            refresh = ('refresh missing/omitted instructions through their current owner' if parts else
                       'refresh current instructions using ' + policy.data['instructions']['policy'])
            parts.append(refresh)
        actions.append('restore:instructions; ' + '; '.join(parts))
    generation = card['generation']
    if generation['before'] is not None and generation['before'] != generation['current']:
        actions.append('restore:coverage; read current sources in generation '
                       + str(generation['current']) + '; ' + card['coverage']['ref'])
    return actions


def render_card(card, policy, available_bytes):
    """Bound JSON structurally; retain source references and explicit omissions."""
    value = deepcopy(card)
    limits = policy.data['summary']
    previous_omissions = value['coverage'].get('omitted', {})
    value['omitted']['coverage_items'] = sum(previous_omissions.get(name, 0) for name in ('documents', 'pending'))
    documents = value['coverage'].get('documents', [])
    pending_limit = max(0, limits['max_coverage_items'] - min(len(documents), limits['max_coverage_items']))
    lists = [(value['git'], 'changed_paths', 'changed_paths', limits['max_changed_paths']),
             (value['instructions'], 'changed', 'instruction_changes', limits['max_instruction_changes']),
             (value['coverage'], 'documents', 'coverage_items', limits['max_coverage_items']),
             (value['coverage'], 'pending', 'coverage_items', pending_limit)]
    for owner, name, omission, maximum in lists:
        items = owner.get(name, [])
        value['omitted'][omission] = value['omitted'].get(omission, 0) + max(0, len(items) - maximum)
        owner[name] = items[:maximum]
    maximum = min(available_bytes, limits['max_bytes'])
    def size():
        return len((CARD_PREFIX + encode(value)).encode('utf-8'))
    while size() > maximum:
        candidates = [(owner, name, omission) for owner, name, omission, _ in lists if owner[name]]
        if not candidates:
            raise PoiseError('Work context summary budget cannot fit identity, references and recovery actions; increase the configured native context and summary budgets')
        owner, name, omission = max(candidates, key=lambda item: len(encode(item[0][item[1]]).encode()))
        owner[name].pop()
        value['omitted'][omission] += 1
    return value
