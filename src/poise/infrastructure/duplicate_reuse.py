"""Git observations and the existing durable check runner for local duplicate reuse."""
import json
from pathlib import Path

from ..modules.foundation.errors import PoiseError
from ..application.check_attempts import is_check_attempt


class DuplicateReuseWorkspace:
    def __init__(self, runtime):
        self.h = runtime

    def _git(self, cwd, *args):
        return self.h._git(cwd, *args)

    def _integrated(self, commit):
        repository = Path(self.h.cfg['git']['repository']).resolve(strict=True)
        base_ref = self.h.cfg['git']['base_ref']
        base = self._git(repository, 'rev-parse', '--verify', base_ref + '^{commit}')
        try:
            self._git(repository, 'merge-base', '--is-ancestor', commit, base)
        except PoiseError as exc:
            raise PoiseError('Reuse source is not integrated into configured base_ref') from exc
        return repository, base_ref, base

    def _observe(self, execution, source_commit):
        repository, base_ref, base = self._integrated(source_commit)
        path = repository if execution['worktree'] is None else Path(execution['worktree']).resolve(strict=True)
        if self._git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir') != self._git(
                repository, 'rev-parse', '--path-format=absolute', '--git-common-dir'):
            raise PoiseError('Reuse workspace belongs to another repository')
        if execution['worktree'] is not None and self._git(path, 'symbolic-ref', '--short', 'HEAD') != execution['branch']:
            raise PoiseError('Reuse worktree branch changed')
        for marker in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
            marker_path = Path(self._git(path, 'rev-parse', '--git-path', marker))
            if not marker_path.is_absolute():
                marker_path = path / marker_path
            if marker_path.exists():
                raise PoiseError('Reuse cannot run during an unfinished Git operation')
        if self._git(path, 'status', '--porcelain'):
            raise PoiseError('Reuse requires a clean local workspace; preserve WIP')
        head = self._git(path, 'rev-parse', 'HEAD')
        try:
            self._git(path, 'merge-base', '--is-ancestor', source_commit, head)
        except PoiseError as exc:
            raise PoiseError('Reuse source is absent from local branch; update it explicitly') from exc
        return {'head': head, 'verified_tree': self._git(path, 'rev-parse', 'HEAD^{tree}'),
                'base_ref': base_ref, 'base_commit': base, 'worktree': str(path)}

    def prepare(self, task_id, context, source_commit, execution, saved):
        h = self.h
        repository, ref, base = self._integrated(source_commit)
        if execution is None:
            execution = h._execution_reservation(task_id, base, context['process']['worktree_required'])
            path = execution['worktree'] or str(repository)
            observation = {'head': base, 'verified_tree': self._git(repository, 'rev-parse', base + '^{tree}'),
                           'base_ref': ref, 'base_commit': base, 'worktree': path}
            if execution['worktree'] is None:
                observation = self._observe(execution, source_commit)
        elif isinstance(execution['pending'], dict) and execution['pending'].get('kind') == 'worktree_setup':
            observation = saved['candidate']
        else:
            observation = self._observe(execution, source_commit)
        if saved is not None:
            for key in ('head', 'verified_tree', 'base_ref', 'worktree'):
                if observation[key] != saved['candidate'][key]:
                    raise PoiseError('Reuse workspace changed after candidate preparation')
        return observation, execution

    def handoff_preflight(self, task_id):
        data = self.h.task_queries.record(task_id)
        if self.h.handoff_tools.commands.latest(task_id) is None:
            return None
        return self.h.handoff_tools.validate_resume(data)

    def reconcile(self, task_id):
        self.h._reconcile_task_worktree(self.h.task_queries.record(task_id))

    @staticmethod
    def _checks(methods):
        return [dict(m, obligations=[m['id']], guard=True, observation_rules=[]) for m in methods]

    def _context(self, task_id, methods):
        h = self.h
        data = h.task_queries.record(task_id)
        candidate = data['duplicate_reuse']['candidate']
        observed = self._observe(data, candidate['source_commit'])
        if any(observed[k] != candidate[k] for k in ('head', 'verified_tree', 'base_ref', 'worktree')):
            raise PoiseError('Reuse workspace changed after local verification')
        checks = self._checks(methods)
        tree = observed['verified_tree']
        invocations, key = h._verification_execution(data, tree, checks, Path(observed['worktree']))
        return data, checks, tree, invocations, key

    def check(self, task_id, methods):
        h = self.h
        data, checks, tree, invocations, key = self._context(task_id, methods)
        with h.store.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            candidate_digest = task.check_candidate_digest
            batch = task.evidence_book.submission_batch(
                task.stage.stage_id, task.state.iteration, candidate_digest, tree, key)
        if batch is not None:
            if not h._intact_receipts(task_id, batch['receipts'], invocations, tree):
                raise PoiseError('Reuse receipt files changed; do not retry blindly')
            return tree, key, batch['receipts']
        if not is_check_attempt(data['pending']):
            h.runner.begin_check_attempt(task_id, h.session, tree, key, [m['id'] for m in methods],
                h.cfg['limits']['verify_attempts'], data['_version'], candidate_digest)
        else:
            h.runner.current_check_attempt(task_id, h.session, tree, key, [m['id'] for m in methods])
        data = h.task_queries.record(task_id)
        receipts = h._execute_checks(data, h._stage(data), tree, checks, invocations, h._roots(data))
        self._context(task_id, methods)  # No check may change the tested repository.
        if not h._intact_receipts(task_id, receipts, invocations, tree):
            raise PoiseError('Reuse receipts changed before finalization')
        h.runner.record_observations(task_id, h.session, tree, key, receipts)
        return tree, key, receipts

    def validate_verified(self, task_id, methods):
        data, _, tree, invocations, key = self._context(task_id, methods)
        proof, report = data['duplicate_reuse'], data['last_report']
        if (not proof['verified'] or report is None or report['execution_key'] != key
                or proof['execution_key'] != key or data['pending'] is not None
                or not self.h._usable_receipts(task_id, report['checks'], invocations, tree)):
            raise PoiseError('Reuse receipt or verification inputs changed')
        return report
