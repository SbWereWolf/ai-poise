"""Explicit documentation subject admission, transport and post-run recheck."""
from copy import deepcopy
import hashlib
import re

from ...modules.project_checks.contracts import CheckFailure, paths


class SubjectAdmission:
    def __init__(self, inputs):
        self.inputs = inputs
        self.snapshots = {}

    def fail(self, code, field, cause):
        raise CheckFailure(code, field, cause,
                           'Укажите согласованный documentation_scope для выбранной рабочей копии и режима.')

    def _paths(self, names, field):
        try:
            paths(names, field)
        except CheckFailure as exc:
            raise CheckFailure('scope_path_invalid', exc.field, exc.cause, exc.recovery) from exc
        if any(not v.lower().endswith('.md') for v in names):
            self.fail('scope_path_invalid', field, 'Область должна содержать Markdown пути.')

    def _candidate(self, declaration):
        if (self.inputs.git('rev-parse', 'HEAD').strip() != declaration['candidate_commit']
                or self.inputs.git('rev-parse', 'HEAD^{tree}').strip() != declaration['candidate_tree']):
            self.fail('scope_candidate_mismatch', 'documentation_scope.candidate_commit',
                      'Checkout не соответствует объявленному кандидату.')
        if self.inputs.git('status', '--porcelain', '--untracked-files=all').strip():
            self.fail('scope_subject_dirty', 'documentation_scope', 'Кандидат содержит несохранённые изменения.')

    def _snapshot(self, names):
        return {v: hashlib.sha256((self.inputs.checkout / v).read_bytes()).hexdigest()
                if (self.inputs.checkout / v).is_file() else None for v in names}

    def admit(self, profile, options):
        supplied = 'documentation_scope' in options
        declaration = options.get('documentation_scope')
        subject = profile.get('subject')
        if supplied and not isinstance(declaration, dict):
            self.fail('scope_shape_invalid', 'documentation_scope', 'Требуется объект документационной области.')
        if subject is None:
            if supplied:
                self.fail('scope_mode_conflict', 'documentation_scope', 'Выбранный профиль не объявляет документационный subject.')
            return None
        mode = subject['mode']
        if mode == 'working':
            if supplied:
                self.fail('scope_mode_conflict', 'documentation_scope', 'Working-профиль не принимает committed declaration.')
            names = options['scope_paths']
            if names is None:
                names = [v for v in (self.inputs.git('diff', '--name-only', '-z', 'HEAD', '--') + '\0' +
                         self.inputs.git('ls-files', '--others', '--exclude-standard', '-z')).split('\0')
                         if v and v.lower().endswith('.md')]
                names = sorted(set(names))
            self._paths(names, 'scope_paths')
            admitted = {'kind': 'documentation', 'mode': mode, 'declaration': None,
                        'paths': list(names), 'state': 'admitted'}
            self.snapshots[id(admitted)] = self._snapshot(names)
            return admitted
        if not supplied:
            self.fail('scope_required', 'documentation_scope', 'Committed-профиль требует явную область.')
        if options['scope_paths'] is not None:
            self.fail('scope_mode_conflict', 'scope_paths', 'Committed declaration требует scope_paths:null.')
        fields = {'version', 'repository_root', 'baseline_commit', 'candidate_commit',
                  'candidate_tree', 'paths', 'required_paths'}
        if set(declaration) != fields or type(declaration.get('version')) is not int:
            self.fail('scope_shape_invalid', 'documentation_scope', 'Неверный набор или тип полей области.')
        if declaration['version'] != 1:
            self.fail('scope_version_unsupported', 'documentation_scope.version', 'Поддерживается version1.')
        root = declaration['repository_root']
        if not isinstance(root, str) or root != str(self.inputs.checkout.resolve()):
            self.fail('scope_root_mismatch', 'documentation_scope.repository_root', 'Объявлен другой корень рабочей копии.')
        self._paths(declaration['paths'], 'documentation_scope.paths')
        self._paths(declaration['required_paths'], 'documentation_scope.required_paths')
        for key, kind in (('baseline_commit', 'commit'), ('candidate_commit', 'commit'), ('candidate_tree', 'tree')):
            identity = declaration[key]
            if not isinstance(identity, str) or re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', identity) is None:
                self.fail('scope_git_identity_invalid', 'documentation_scope.' + key, 'Укажите полный Git object ID.')
            try:
                actual_kind = self.inputs.git('cat-file', '-t', identity).strip()
            except CheckFailure as exc:
                raise CheckFailure('scope_git_identity_invalid', 'documentation_scope.' + key,
                                   'Объект Git отсутствует.', 'Укажите существующий полный Git object ID.') from exc
            if actual_kind != kind:
                self.fail('scope_git_identity_invalid', 'documentation_scope.' + key, f'Требуется объект {kind}.')
        self._candidate(declaration)
        records = self.inputs.git('diff', '--name-status', '-z', '-M', declaration['baseline_commit'],
                                  declaration['candidate_commit'], '--').split('\0')
        delta, renames, index = set(), [], 0
        while index < len(records) and records[index]:
            status = records[index]
            count = 2 if status.startswith(('R', 'C')) else 1
            names = {v for v in records[index + 1:index + count + 1] if v.lower().endswith('.md')}
            delta.update(names)
            if status.startswith('R'):
                renames.append(names)
            index += count + 1
        selected = set(declaration['paths'])
        if (not selected <= delta or not set(declaration['required_paths']) <= selected
                or not selected and bool(delta)
                or any(bool(pair & selected) and not pair <= selected for pair in renames)):
            self.fail('scope_targets_invalid', 'documentation_scope.paths', 'Область не соответствует полному выбранному Markdown delta.')
        admitted = {'kind': 'documentation', 'mode': mode, 'declaration': deepcopy(declaration),
                    'paths': list(declaration['paths']), 'state': 'admitted'}
        self.snapshots[id(admitted)] = self._snapshot(admitted['paths'])
        return admitted

    def recheck(self, subject):
        if subject is None:
            return
        try:
            if subject['declaration'] is not None:
                self._candidate(subject['declaration'])
            if self._snapshot(subject['paths']) != self.snapshots[id(subject)]:
                self.fail('scope_subject_changed', 'documentation_scope', 'Материалы изменились во время проверки.')
        except (CheckFailure, OSError) as exc:
            subject['state'] = 'changed'
            raise CheckFailure('scope_subject_changed', 'documentation_scope', str(exc),
                               'Сохраните изменения и повторите проверку согласованного неизменного кандидата.') from exc
        subject['state'] = 'unchanged'
