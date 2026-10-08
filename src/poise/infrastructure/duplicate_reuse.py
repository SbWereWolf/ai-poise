"""Git observations and the existing durable check runner for local duplicate reuse."""
import json
from pathlib import Path

from ..modules.foundation.errors import PoiseError
from ..application.artifact_materials import ArtifactMaterialResolver
from ..application.check_attempts import is_check_attempt
from ..artifacts import check_counts
from .artifact_factory import FileArtifactFactory
from .task_paths import task_root, sprint_root
from ..common import descendant


class DuplicateReuseWorkspace:
    def __init__(self, runtime):
        self.h = runtime

    def material_scope(self):
        return self.h.delivery_effects.locked()

    def admit_material(self, task_id, source_task_id):
        self.h.delivery_tools.material_admission(task_id, 'reuse')
        self.h.delivery_tools.material_admission(source_task_id, 'reuse_source')

    def _git(self, cwd, *args):
        return self.h._git(cwd, *args)

    def _repository_base(self):
        repository = Path(self.h.cfg['git']['repository']).resolve(strict=True)
        base_ref = self.h.cfg['git']['base_ref']
        base = self._git(repository, 'rev-parse', '--verify', base_ref + '^{commit}')
        return repository, base_ref, base

    def _integrated(self, commit):
        repository, base_ref, base = self._repository_base()
        try:
            self._git(repository, 'merge-base', '--is-ancestor', commit, base)
        except PoiseError as exc:
            raise PoiseError('Reuse source is not integrated into configured base_ref') from exc
        return repository, base_ref, base

    def _observe(self, execution, source_commit, *, require_clean=True,
                 require_main=True, allow_missing_source=False):
        repository, base_ref, base = (self._integrated(source_commit) if require_main
                                      else self._repository_base())
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
        if require_clean and self._git(path, 'status', '--porcelain'):
            raise PoiseError('Reuse requires a clean local workspace; preserve WIP')
        head = self._git(path, 'rev-parse', 'HEAD')
        try:
            self._git(path, 'merge-base', '--is-ancestor', source_commit, head)
        except PoiseError as exc:
            if allow_missing_source:
                return None
            raise PoiseError('Reuse source is absent from local branch; update it explicitly') from exc
        return {'head': head, 'verified_tree': self._git(path, 'rev-parse', 'HEAD^{tree}'),
                'base_ref': base_ref, 'base_commit': base, 'worktree': str(path)}

    def validate_restart(self, execution, saved):
        observed = self._observe(execution or {'worktree': None, 'branch': None},
                                 saved['source_commit'], require_clean=False,
                                 require_main=False, allow_missing_source=True)
        if observed is None:
            return None
        if saved.get('worktree') is not None and observed['worktree'] != saved['worktree']:
            raise PoiseError('Local repair must preserve its acquired worktree')
        return observed

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

    def _artifact_locations(self, task_id, sprint_id):
        h = self.h
        roots = {'task': task_root(h.state, h.paths, task_id, sprint_id)}
        owners = {'task': task_id}
        if sprint_id is not None:
            roots['sprint'] = sprint_root(h.state, h.paths, sprint_id)
            owners['sprint'] = sprint_id
        return roots, owners

    def _artifact_factory(self, roots):
        h = self.h
        return FileArtifactFactory(roots, h.cfg['batch'],
            descendant(h.state, h.cfg['batch']['artifact_lock']),
            h.cfg['limits']['lock_seconds'], h.cfg['limits']['lock_poll_seconds'])

    def _artifact_rules(self, task_id, context, stage_id, records):
        # Reuse delivers the accepted result, not artifacts for simulated prior stages.
        stage = next((s for s in context['process']['stages'] if s['id'] == stage_id), None)
        if stage is None:
            raise PoiseError('Accepted artifact stage is not in the local process')
        check_counts(records, context['contract']['artifact_requirements'])
        check_counts(records, stage['artifact_requirements'])
        with self.h.store.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            for assessment in task.assess_artifact_delivery(stage_id, self.h._artifact_facts(records)):
                assessment.require()

    def plan_artifacts(self, task_id, context, source):
        roots, owners = self._artifact_locations(task_id, context['sprint_id'])
        source_roots, source_owners = self._artifact_locations(source['task_id'], source['sprint_id'])
        source_roots = {s: str(source_roots[s]) for s in {a['scope'] for a in source['records']}}
        planned = self._artifact_factory(roots).plan_material_copy(
            source['records'], source_roots, source_owners, owners,
            materials=ArtifactMaterialResolver(self.h.delivery_tools).resolve(source['task_id'],source['records']))
        existing = self.h._candidate_artifacts(
            {'id': task_id, 'sprint_id': context['sprint_id']}, [], roots)
        merged = {a['id']: a for a in existing}
        for a in planned:
            if a['id'] in merged and any(merged[a['id']][k] != a[k]
                    for k in ('owner', 'scope', 'path', 'digest')):
                raise PoiseError('Artifact reuse conflicts with a registered local file')
            merged[a['id']] = a
        self._artifact_rules(task_id, context, source['stage'], list(merged.values()))
        return planned

    def deliver_artifacts(self, task_id):
        data = self.h.task_queries.record(task_id)
        candidate = data['duplicate_reuse']['candidate']
        source = candidate['artifact_source']
        roots, owners = self._artifact_locations(task_id, data['sprint_id'])
        source_roots, source_owners = self._artifact_locations(source['task_id'], source['sprint_id'])
        source_roots = {s: str(source_roots[s]) for s in {a['scope'] for a in source['records']}}
        # Recheck the immutable plan before any new file is published on retry.
        factory = self._artifact_factory(roots)
        planned = factory.plan_material_copy(source['records'], source_roots, source_owners, owners,
            materials=ArtifactMaterialResolver(self.h.delivery_tools).resolve(source['task_id'],source['records']))
        if planned != candidate['artifact_delivery']:
            raise PoiseError('Artifact reuse delivery plan changed')
        published = (factory.copy_materials(source['records'], source_roots, source_owners, owners,
            materials=ArtifactMaterialResolver(self.h.delivery_tools).resolve(source['task_id'],source['records']))
                     if planned else [])
        if published != planned:
            raise PoiseError('Artifact reuse publication differs from its candidate')
        return self.validate_artifacts(task_id)

    def validate_artifacts(self, task_id):
        data = self.h.task_queries.record(task_id)
        candidate = data['duplicate_reuse']['candidate']
        roots, owners = self._artifact_locations(task_id, data['sprint_id'])
        factory = self._artifact_factory(roots)
        # Identical owner roots make the shared planner a no-write integrity check.
        expected = candidate['artifact_delivery']
        own_roots = {s: str(roots[s]) for s in {a['scope'] for a in expected}}
        factory.plan_registered_copy(expected, own_roots, owners, owners)
        actual = self.h._candidate_artifacts(data, [a['path'] for a in expected], roots)
        by_id = {a['id']: a for a in actual}
        if any(a['id'] not in by_id or any(by_id[a['id']][k] != a[k]
               for k in ('owner', 'scope', 'path', 'digest')) for a in expected):
            raise PoiseError('Reuse local artifact delivery changed')
        self._artifact_rules(task_id, data, candidate['artifact_stage'], actual)
        return actual

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
        delivery = self.plan_artifacts(task_id, data, candidate['artifact_source'])
        if delivery != candidate['artifact_delivery']:
            raise PoiseError('Reuse permanent artifact provenance changed')
        self.validate_artifacts(task_id)
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
                h.cfg['limits']['verify_attempts'], data['_version'], candidate_digest,
                h._attempt_identity(tree, invocations))
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
