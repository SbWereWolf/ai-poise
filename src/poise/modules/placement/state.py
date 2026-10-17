"""Current declarations and batch admission; no attempt history or I/O."""
from copy import deepcopy
from dataclasses import dataclass

from .contracts import (
    IDENTITY_FIELDS, RECORD_FIELDS, PlacementInputError, authorizations,
    diagnostic, exact, key, relative, scope_matches, strings, text,
)


@dataclass(frozen=True)
class PlacementState:
    document: dict

    @classmethod
    def initial(cls, project, checkout, caller_key, entries):
        return cls({'schema': 'poise-placement-state-1', 'project': project, 'checkout': checkout,
                    'caller_key': caller_key, 'baseline': {'entries': sorted(set(entries))},
                    'authorizations': [], 'reservations': []})

    @classmethod
    def parse(cls, raw, project, checkout, caller_key):
        try:
            exact(raw, {'schema', 'project', 'checkout', 'caller_key', 'baseline', 'authorizations', 'reservations'})
            if (raw['schema'] != 'poise-placement-state-1' or raw['project'] != project
                    or raw['checkout'] != checkout or raw['caller_key'] != caller_key):
                raise ValueError('Foreign placement namespace')
            exact(raw['baseline'], {'entries'})
            entries = raw['baseline']['entries']
            if (not strings(entries, ordered=True)
                    or any('/' in v or v in ('.', '..') for v in entries)):
                raise ValueError('Invalid baseline')
            grants = authorizations(raw['authorizations'])
            if grants != raw['authorizations']:
                raise ValueError('Noncanonical authorizations')
            records = raw['reservations']
            if not isinstance(records, list):
                raise ValueError('Invalid reservations')
            keys = []
            for record in records:
                exact(record, RECORD_FIELDS)
                if (not key(record['key']) or any(not text(record[k]) for k in
                        ('purpose', 'recipient', 'lifetime', 'name', 'root', 'category', 'rule'))
                        or not relative(record['path']) or record['path'].split('/')[-1] != record['name']
                        or not text(record['absolute_path']) or not record['absolute_path'].startswith('/')
                        or record['versioning'] not in ('versioned', 'ignored')
                        or not strings(record['authorization_refs'], empty=False, ordered=True)):
                    raise ValueError('Invalid reservation')
                exact(record['owner'], {'kind', 'id'})
                if record['owner']['kind'] not in ('product', 'harness', 'task', 'sprint') or not text(record['owner']['id']):
                    raise ValueError('Invalid owner')
                keys.append(record['key'])
            if keys != sorted(set(keys)):
                raise ValueError('Reservation order/identity conflict')
        except (PlacementInputError, ValueError, TypeError) as exc:
            raise ValueError('Invalid placement state') from exc
        return cls(deepcopy(raw))

    def admit(self, request, policy, checkout, rows, diagnostics):
        document, rows, diagnostics = deepcopy(self.document), deepcopy(rows), list(diagnostics)
        supplied = list(request.authorizations)
        if any(grant['root'] not in policy.roots for grant in supplied):
            raise PlacementInputError('invalid_request', 'request', 'provide:request')
        grants = authorizations(document['authorizations'] + supplied)
        used = []
        by_key = {v['key']: v for v in document['reservations']}
        proposed = []
        for item, row in zip(request.items, rows, strict=True):
            if row['status'] != 'allowed':
                continue
            root = policy.roots[row['root']]
            top = row['path'].split('/')[0]
            needs_grant = (root['owner'] == 'product' and root['path'] == checkout
                           and top not in document['baseline']['entries'])
            matching = [v for v in grants if scope_matches(v, row['root'], row['path'], item)]
            if needs_grant and not matching:
                row['status'] = 'needs_agreement'
                diagnostics.append(diagnostic('approval_required', row['key'], 'items:' + row['key'] + ':path',
                                              'agree:' + row['root'] + ':' + top))
                continue
            if needs_grant:
                row['authorization_refs'] = sorted(set(row['authorization_refs'] + [v['reference'] for v in matching]))
                used.extend(matching)
            record = {**{k: deepcopy(item[k]) for k in ('key', 'purpose', 'recipient', 'lifetime', 'name')},
                      **{k: deepcopy(row[k]) for k in ('root', 'owner', 'category', 'rule', 'versioning',
                                                      'path', 'absolute_path', 'authorization_refs')}}
            old = by_key.get(row['key'])
            if old is not None and any(old[k] != record[k] for k in IDENTITY_FIELDS):
                row['status'] = 'needs_inputs'
                diagnostics.append(diagnostic('reservation_conflict', row['key'], 'items:' + row['key'], 'choose:new_key'))
                continue
            collision = any(v['absolute_path'] == record['absolute_path'] and any(
                v[k] != record[k] for k in ('purpose', 'recipient', 'lifetime', 'name', 'owner', 'path'))
                for v in list(by_key.values()) + proposed if v['key'] != record['key'])
            if collision:
                row['status'] = 'needs_inputs'
                diagnostics.append(diagnostic('destination_conflict', row['key'], 'items:' + row['key'] + ':path', 'choose:placement'))
                continue
            row['reserved'] = old is not None
            proposed.append(record)
        allowed = all(v['status'] == 'allowed' for v in rows)
        if allowed and request.operation == 'prepare':
            by_key.update({v['key']: v for v in proposed})
            document['reservations'] = [by_key[k] for k in sorted(by_key)]
            document['authorizations'] = authorizations(document['authorizations'] + used)
        return PlacementState(document), rows, diagnostics, allowed
