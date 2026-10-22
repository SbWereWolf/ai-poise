"""Stage-owned qualified read/write policy; no filesystem or global role ACL."""
from dataclasses import dataclass
from pathlib import PurePosixPath

from .process.errors import DomainError
from .process.stage_contracts import matches_allowed_path, text_list
from .process.workflow import exact


def relative_path(value):
    if (type(value) is not str or not value or '\x00' in value
            or any(part in ('', '.', '..') for part in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise DomainError('Provide a canonical root-relative file path')
    return value


@dataclass(frozen=True)
class FilePolicy:
    workspace_id: str
    roots: dict
    stages: dict
    process_stages: dict

    @staticmethod
    def root_key(value):
        if (type(value['kind']) is not str or not value['kind'].strip()
                or type(value['position']) is not int or value['position'] < 0):
            raise DomainError('Root requires a text kind and nonnegative integer position')
        return value['kind'], value['position']

    @classmethod
    def parse(cls, value, process, workspace_id):
        exact(value, {'workspace_id', 'roots', 'stages'}, 'file_policy')
        if (type(value['workspace_id']) is not str or not value['workspace_id'].strip()
                or value['workspace_id'] != workspace_id):
            raise DomainError('file_policy workspace_id must match the canonical setup workspace')
        if type(value['roots']) is not list or not value['roots']:
            raise DomainError('file_policy roots requires an explicit nonempty list')
        roots = {}
        for root in value['roots']:
            exact(root, {'kind', 'position', 'path'}, 'file_policy root')
            key = cls.root_key(root)
            if key in roots:
                raise DomainError('Duplicate file_policy root identity')
            path = root['path']
            if (type(path) is not str or not path.strip() or '\x00' in path
                    or not PurePosixPath(path).is_absolute()):
                raise DomainError('file_policy root path requires an absolute directory path')
            roots[key] = path
        if type(value['stages']) is not list:
            raise DomainError('file_policy stages requires an explicit list')
        process_stages = {stage['id']: stage for stage in process['stages']}
        stages = {}
        for stage in value['stages']:
            exact(stage, {'stage_id', 'roots'}, 'file_policy stage')
            sid = stage['stage_id']
            if type(sid) is not str or sid not in process_stages or sid in stages:
                raise DomainError('file_policy stage must identify each saved process stage once')
            if type(stage['roots']) is not list:
                raise DomainError('file_policy stage roots requires an explicit list')
            rules = {}
            for rule in stage['roots']:
                exact(rule, {'kind', 'position', 'read', 'write'}, 'file_policy stage root')
                key = cls.root_key(rule)
                if key not in roots or key in rules:
                    raise DomainError('file_policy stage root must identify each declared root once')
                modes = {}
                for mode in ('read', 'write'):
                    exact(rule[mode], {'allow', 'deny'}, 'file_policy ' + mode)
                    modes[mode] = {kind: text_list(rule[mode][kind], 'file_policy ' + mode + '.' + kind)
                                   for kind in ('allow', 'deny')}
                rules[key] = modes
            if rules.keys() != roots.keys():
                raise DomainError('file_policy stage must declare rules for every root')
            stages[sid] = rules
        if stages.keys() != process_stages.keys():
            raise DomainError('file_policy must declare every saved process stage')
        return cls(value['workspace_id'], roots, stages, process_stages)

    def decide(self, stage_id, root, path, mode):
        relative_path(path)
        key = self.root_key(root)
        if stage_id not in self.stages:
            raise DomainError('Select an existing file_policy stage')
        if key not in self.roots:
            raise DomainError('Select an existing declared file_policy root')
        if mode not in ('read', 'write'):
            raise DomainError('Select read or write mode')
        rule = self.stages[stage_id][key][mode]
        reason = None
        if mode == 'write' and self.process_stages[stage_id]['read_only']:
            reason = 'read_only'
        elif any(matches_allowed_path(path, pattern) for pattern in rule['deny']):
            reason = 'denied'
        elif not any(matches_allowed_path(path, pattern) for pattern in rule['allow']):
            reason = 'outside_allow'
        return {'root': {'kind': key[0], 'position': key[1]}, 'path': path,
                'allowed': reason is None, 'reason': reason}

    def decide_diff(self, stage_id, root, stage_paths, commit_paths):
        paths = sorted(set(stage_paths) | set(commit_paths))
        return [self.decide(stage_id, root, path, 'write') for path in paths]
