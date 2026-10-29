"""Bounded skill/policy inventories using the existing Markdown link owner."""
from collections import deque
import hashlib
from pathlib import Path, PurePosixPath
import posixpath
from urllib.parse import unquote, urlsplit

from ...modules.accounting.metrics.domain import ObservationFacts, identity
from ...modules.foundation.errors import PoiseError
from ..documentation_checks import links
from ..goal_config import strict_json
from .sources import capture_source, file_identity


POLICY_FIELDS = ('schema_version', 'manifest_hash', 'policy_version', 'session_id', 'phase', 'task_path',
                 'applications', 'profiles', 'risk_triggers', 'skills', 'recommended_skills', 'reasons',
                 'triggered_references', 'observed_at')
ROUTING_FIELDS = ('profiles', 'required_path_rules', 'recommended_path_rules', 'required_signal_skills',
                  'recommended_signal_skills', 'cross_profile_contract_profiles', 'cross_profile_skills')


def capture_inventory(source, checkout, max_files):
    files = {}
    for address in source['paths']:
        selected = Path(address)
        candidates = (path for path in selected.rglob('*') if path.is_file()) if selected.is_dir() else [selected]
        for path in candidates:
            if path not in files:
                if len(files) >= max_files:
                    raise PoiseError('Skill source inventory exceeds the configured file bound')
                files[path] = capture_source(path, source)
    return {'path': source['paths'][0], 'source': source, 'checkout': str(checkout),
            'files': [files[path] for path in sorted(files)], 'boundary': sum(item['boundary'] for item in files.values())}


def read_inventory(descriptor, limit):
    if descriptor['boundary'] > limit:
        raise PoiseError('Skill source inventory exceeds the configured source byte bound')
    result = []
    for entry in descriptor['files']:
        path = Path(entry['path'])
        before = path.stat()
        if file_identity(before) != entry['source_identity'] or before.st_size != entry['boundary']:
            raise PoiseError('Captured skill source changed; capture its current inventory')
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise PoiseError('Skill source exceeds the configured source byte bound')
        after = path.stat()
        if file_identity(after) != file_identity(before) or after.st_size != len(raw):
            raise PoiseError('Skill source changed during bounded reading; capture it again')
        try:
            relative = path.relative_to(descriptor['checkout']).as_posix()
        except ValueError:
            relative = str(path)
        result.append({'path': relative, 'raw': raw, 'content_sha256': hashlib.sha256(raw).hexdigest()})
    return result


def skill_source_observations(descriptor, scope, limit, at):
    source = descriptor['source']
    files = read_inventory(descriptor, limit)
    facts = ObservationFacts(scope, source, at)
    if source['kind'] == 'skill-policy':
        for file in files:
            value = strict_json(file['raw'].decode('utf-8'))
            if not isinstance(value, dict):
                raise PoiseError('Effective skill policy/routing source must be an object')
            if 'skills' in value and 'session_id' in value:
                if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
                    raise PoiseError('Unsupported effective skill policy schema')
                fields = {name: value[name] for name in POLICY_FIELDS if name in value}
                fields['record_type'] = 'effective-skill-policy'
            elif 'required_path_rules' in value:
                fields = {name: value[name] for name in ROUTING_FIELDS if name in value}
                fields['record_type'] = 'skill-routing'
            else:
                raise PoiseError('Unrecognized effective skill policy/routing source')
            fields.update(source_path=file['path'], content_sha256=file['content_sha256'])
            facts.describe('skill-selection', identity([source['id'], file['path'], file['content_sha256']]), fields)
    else:
        nodes = {}
        for file in files:
            body = file['raw'].decode('utf-8')
            normalized = '\n'.join(line.rstrip() for line in body.replace('\r\n', '\n').replace('\r', '\n').split('\n')).strip()
            targets = []
            for link in links(body):
                if not link.target:
                    continue
                url = urlsplit(link.target)
                if url.scheme or url.netloc or not url.path:
                    continue
                target = unquote(url.path)
                if target.startswith('/'):
                    target = target.lstrip('/')
                elif not target.startswith(('.agents/', 'docs/', 'ai-assistant/')):
                    target = posixpath.join(str(PurePosixPath(file['path']).parent), target)
                targets.append(posixpath.normpath(target))
            nodes[file['path']] = {'path': file['path'], 'bytes': len(file['raw']),
                                   'lines': body.count('\n') + int(bool(body) and not body.endswith('\n')),
                                   'normalized_content_sha256': hashlib.sha256(normalized.encode()).hexdigest() if normalized else None,
                                   'links': targets}
        roots = [path for path in nodes if path.endswith('/SKILL.md') and '/skills/' in path]
        reached, pending = set(), deque(roots)
        while pending:
            path = pending.popleft()
            if path in reached:
                continue
            reached.add(path)
            if path in nodes:
                pending.extend(nodes[path]['links'])
        skills = []
        for root in sorted(roots):
            owner = str(PurePosixPath(root).parent)
            skills.append({'skill': PurePosixPath(owner).name, 'core': nodes[root],
                           'references': [node for path, node in nodes.items() if path.startswith(owner + '/') and
                                          path != root and path in reached],
                           'unreachable_paths': sorted(path for path in nodes if path.startswith(owner + '/') and path not in reached)})
        fields = {'record_type': 'skill-inventory', 'skills': skills,
                  'unreachable_paths': sorted(path for path in nodes if path not in reached),
                  'files': list(nodes.values())}
        facts.describe('skill-content', identity([source['id'], fields]), fields)
    coverage = {'source_key': identity([source['id'], source['paths']]), 'partial_tail': False,
                'excluded_prefix_events': 0, 'errors': [], 'complete_for_registered_scope': True,
                'inventory_sha256': identity([{k: row[k] for k in ('path', 'content_sha256')} for row in files])}
    return {'kind': 'facts', **facts.export(), 'scope': scope, 'source': source, 'coverage': coverage}
