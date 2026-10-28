"""Coordinate admitted project checks through their existing and specific owners."""
from copy import deepcopy

from ..modules.project_checks.contracts import CheckFailure, ObservedCheckFailure, UnstartedCheck
from ..modules.project_checks.outcomes import aggregate, cache_metadata, item_status, not_executed, omitted_step
from ..modules.project_checks.profiles import resolved_selection
from ..modules.project_checks.timing import eligible


class ProjectChecks:
    def __init__(self, configuration, inputs, runtime, ledger, cache, execution,
                 timing, subject, publisher, environment):
        self.configuration = configuration
        self.inputs, self.runtime, self.ledger = inputs, runtime, ledger
        self.cache, self.execution, self.timing = cache, execution, timing
        self.subject, self.publisher, self.environment = subject, publisher, environment
        self.warnings = []
        self.provenance = None
        self.known_records = []
        self.establish_session = None
        self.failure = None
        self.invocation = None

    def _check_cancellation(self):
        if self.invocation is not None:
            self.invocation.before_step()

    def catalogue(self, kind, field):
        value = getattr(self.configuration, kind)
        if value is None:
            raise CheckFailure(kind + '_unavailable', field, f'Каталог {kind} отключён.',
                               f'Выберите настроенный каталог {kind}.', status='unavailable')
        return value

    def execution_environment(self):
        environment = dict(self.environment)
        for key in self.configuration.policy['environment']['clear_keys']:
            environment.pop(key, None)
        return environment

    def describe(self, selected, field):
        kind = selected['kind']
        if kind == 'profile':
            profile = self.configuration.profiles.select(selected, field, self.warnings)
            invocation = self.runtime.resolve(profile, environment=self.execution_environment())
            return {'kind': 'profile', 'value': {'selection': resolved_selection(profile),
                    'platforms': list(profile['platforms']), 'invocation': profile['invocation'],
                    'profile': deepcopy(profile), 'profile_sha256': self.configuration.profiles_sha256,
                    'available': invocation['available'],
                    'unavailable_reason': None if invocation['available'] else 'runtime_unavailable'}}
        if kind == 'suite_set':
            return self.suite_set(selected, field, 'describe', None)
        catalogue = self.catalogue('packages' if kind == 'package' else 'suites', field)
        return {'kind': kind, 'value': deepcopy(catalogue.get(selected['id'], field + '.id'))}

    def suite_set(self, selected, field, operation, options):
        catalogue = self.catalogue('suites', field)
        members, matches, unmatched, reason = catalogue.select(selected['profile'], selected['changed_paths'])
        items = []
        for member in members:
            choice = {'kind': 'suite', 'id': member['id']}
            items.append(self.describe(choice, field) if operation == 'describe' else self.plan(choice, options, field))
        return {'kind': 'suite_selection', 'selection': deepcopy(selected),
                'status': 'ready' if operation == 'describe' or not items else 'planned',
                'suite_ids': [v['id'] for v in members], 'source_matches': matches,
                'unmatched_paths': unmatched, 'reason': reason, 'items': items}

    def profile_step(self, profile, options, field, *, package_id=None, driver=None, argv=None, cwd=None):
        environment = self.execution_environment()
        invocation = self.runtime.resolve(profile, argv=argv, cwd=cwd, environment=environment)
        subject = self.subject.admit(profile, options)
        chosen = resolved_selection(profile)
        expected_identity = (self.configuration.bound.config['project'], chosen['id'],
                             chosen['workload_class'], chosen['runtime_class'])
        samples = self.ledger.samples(expected_identity)
        hard, stall, basis = self.timing.limits(profile, samples)
        timeout = {'configured_seconds': profile['execution_timeout_seconds'], 'effective_seconds': hard,
                   'basis': basis, 'evidence_run_id': None, 'override_reason': None}
        override = options['timeout_override']
        if override is not None:
            evidence = self.ledger.run(override['evidence_run_id'])
            self.timing.admit_override(override, hard, evidence, expected_identity,
                                      self.ledger.consumed(override['evidence_run_id']),
                                      field + '.options.timeout_override')
            timeout.update(effective_seconds=override['seconds'], basis='diagnostic_override',
                           evidence_run_id=override['evidence_run_id'], override_reason=override['reason'])
        return {'profile': profile, 'invocation': invocation, 'subject': subject,
                'environment': environment, 'timeout': timeout, 'stall': stall,
                'package_id': package_id, 'driver': driver, 'selection': chosen, 'field': field}

    def inventory(self, catalogue):
        candidates, membership = self.inputs.package_inventory(catalogue)
        catalogue.validate_inventory(candidates, membership)

    def plan(self, selected, options, field):
        kind = selected['kind']
        if kind == 'suite_set':
            return self.suite_set(selected, field, 'preview', options)
        if kind == 'suite':
            suite = self.catalogue('suites', field).get(selected['id'], field + '.id')
            children = [self.plan(v, options, field + f'.commands[{i}]') for i, v in enumerate(suite['commands'])]
            return {'selection': deepcopy(selected), 'dependencies': [], 'children': children,
                    'steps': [step for child in children for step in child['steps']], 'options': options,
                    'package': None}
        steps, dependencies, package = [], [], None
        if kind == 'profile':
            profile = self.configuration.profiles.select(selected, field + '.selection', self.warnings)
            steps.append(self.profile_step(profile, options, field))
            selected = resolved_selection(profile)
        else:
            catalogue = self.catalogue('packages', field)
            package = catalogue.get(selected['id'], field + '.selection.id')
            self.inventory(catalogue)
            ordered = catalogue.order(selected['id'])
            dependencies = [v['package_id'] for v in ordered[:-1]]
            for member in ordered:
                profile = self.configuration.profiles.select(
                    {'kind': 'profile', 'id': 'test-package', 'workload_class': member['package_id'],
                     'runtime_class': None}, field + '.selection', self.warnings)
                for step in member['steps']:
                    driver = self.configuration.drivers[step['driver']]
                    argv = [*driver['argv_prefix'], *step['arguments'],
                            *[p if driver['path_prefix'] == '.' else driver['path_prefix'] + '/' + p
                              for p in step['test_paths']]]
                    steps.append(self.profile_step(profile, options, field, package_id=member['package_id'],
                                                   driver=step['driver'], argv=argv, cwd=driver['cwd']))
        return {'selection': deepcopy(selected), 'dependencies': dependencies, 'steps': steps,
                'options': deepcopy(options), 'package': package, 'children': None}

    def preview(self, plan):
        if plan.get('kind') == 'suite_selection':
            return {**plan, 'items': [self.preview(v) for v in plan['items']]}
        return {'kind': 'preview', 'selection': deepcopy(plan['selection']),
                'resolved_dependencies': list(plan['dependencies']),
                'steps': [{'ordinal': i + 1, 'selection': deepcopy(step['selection']),
                           'argv': list(step['invocation']['argv']), 'cwd': step['invocation']['cwd'],
                           'runtime_class': step['profile']['runtime_class'], 'status': 'planned',
                           'subject': None if step['subject'] is None else step['subject']['declaration'],
                           'available': step['invocation']['available'],
                           'unavailable_reason': None if step['invocation']['available'] else 'runtime_unavailable',
                           'invocation': step['profile']['invocation']}
                          for i, step in enumerate(plan['steps'])]}

    def _admit_runtime(self, plan):
        if plan.get('kind') == 'suite_selection':
            for child in plan['items']:
                self._admit_runtime(child)
            return
        for step in plan['steps']:
            self.runtime.prerequisites(step['profile'], step['invocation'], step['environment'], step['field'])

    def admit_batch(self, request):
        self.ledger.admit()
        operation = request.operation
        admitted = []
        for i, item in enumerate(request.items):
            field = f'request.items[{i}]'
            if operation == 'list':
                kind = item['kind']
                catalogue = self.catalogue(kind, field + '.kind')
                if kind == 'profiles':
                    values = [v for v in catalogue.profiles if item['platform'] is None or item['platform'] in v['platforms']]
                    admitted.append([self.describe(resolved_selection(v), field) for v in values])
                elif kind == 'packages':
                    admitted.append([{'kind': 'package', 'value': deepcopy(v)} for v in catalogue.packages])
                else:
                    admitted.append([{'kind': 'suite', 'value': deepcopy(v)} for v in catalogue.suites])
            elif operation == 'describe':
                admitted.append(self.describe(item, field))
            elif operation in ('preview', 'run'):
                plan = self.plan(item['selection'], item['options'], field)
                if operation == 'run':
                    self._admit_runtime(plan)
                admitted.append(plan)
            elif operation == 'package_impact':
                admitted.append(self.catalogue('packages', field).impact(item['changed_paths']))
            elif operation == 'result':
                admitted.append(self.ledger.result(item['result_id'], field + '.result_id'))
            else:
                profile = self.configuration.profiles.select(item['profile'], field + '.profile', self.warnings)
                self.publisher.admit(item, field)
                expected = (self.configuration.bound.config['project'], profile['command_id'],
                            profile['workload_class'], profile['runtime_class'])
                samples = [self.ledger.run(identity) for identity in item['evidence_run_ids']]
                if any(v is None or not eligible(v, expected) for v in samples):
                    raise CheckFailure('invalid_history', field + '.evidence_run_ids',
                                       'Не все указанные наблюдения являются пригодными успешными запусками этого профиля.',
                                       'Укажите реальные успешные complete наблюдения выбранного профиля.')
                admitted.append({'request': item, 'field': field, 'profile': profile,
                                 'values': self.timing.calibrated_values(samples)})
        return admitted

    def _provenance(self):
        if self.provenance is None:
            configuration = self.configuration
            self.provenance = {'project': configuration.bound.config['project'],
                               'checkout': str(configuration.bound.checkout),
                               'policy_sha256': configuration.policy_sha256,
                               'profiles_sha256': configuration.profiles_sha256,
                               'source_commit': self.inputs.git('rev-parse', 'HEAD').strip(),
                               'source_tree': self.inputs.git('rev-parse', 'HEAD^{tree}').strip()}
        return deepcopy(self.provenance)

    def run(self, plan):
        self._check_cancellation()
        if plan.get('kind') == 'suite_selection':
            items, stopped = [], False
            for child in plan['items']:
                try:
                    item = not_executed(child['selection']) if stopped else self.run(child)
                except CheckFailure as exc:
                    if not items:
                        raise
                    self.failure = exc
                    item = not_executed(child['selection'])
                    stopped = True
                items.append(item)
                stopped = stopped or item_status(item) not in ('passed', 'ready')
            return {**plan, 'items': items,
                    'status': (self.partial_status(self.failure) if self.failure is not None else
                               aggregate([v for v in items if v['kind'] != 'not_executed']))}
        if plan['children'] is not None:
            steps, omitted, stopped = [], [], False
            for child in plan['children']:
                if stopped:
                    offset = len(steps) + len(omitted)
                    omitted.extend(omitted_step(offset + i + 1, child['selection'])
                                   for i in range(len(child['steps'])))
                    continue
                try:
                    child_result = self.run(child)
                except CheckFailure as exc:
                    if not steps:
                        raise
                    self.failure = exc
                    offset = len(steps) + len(omitted)
                    omitted.extend(omitted_step(offset + i + 1, child['selection'])
                                   for i in range(len(child['steps'])))
                    stopped = True
                    continue
                execution = child_result['source'] if child_result['kind'] == 'reuse' else child_result
                offset = len(steps)
                steps.extend(execution['steps'])
                omitted.extend({**v, 'ordinal': offset + v['ordinal']} for v in execution['not_executed'])
                stopped = execution['status'] != 'passed'
            for i, step in enumerate(steps):
                step = deepcopy(step)
                step['ordinal'] = i + 1
                steps[i] = step
            return self.save_execution(plan, steps, omitted, cache_metadata(False, None, 'composed_sources'),
                                       status=None if self.failure is None else self.partial_status(self.failure))
        profile = plan['steps'][0]['profile']
        eligible_cache = all(v['profile']['cache']['enabled'] for v in plan['steps']) and plan['options']['timeout_override'] is None
        key = None
        if eligible_cache:
            key, _ = self.inputs.key(profile, plan['options']['scope_paths'], plan['package'])
        metadata = cache_metadata(eligible_cache, key, 'fresh' if plan['options']['fresh'] else 'disabled' if not eligible_cache else 'miss')
        if eligible_cache and not plan['options']['fresh']:
            source = self.cache.lookup(key)
            if source is not None:
                hit = cache_metadata(True, key, 'hit')
                hit.update(hit=True, source_result_id=source['result_id'])
                return {'kind': 'reuse', 'selection': deepcopy(plan['selection']), 'cache': hit, 'source': source}
        steps, omitted, stopped = [], [], False
        for i, step in enumerate(plan['steps']):
            step_selection = ({'kind': 'package', 'id': step['package_id']}
                              if step['package_id'] is not None else step['selection'])
            if stopped:
                omitted.append(omitted_step(i + 1, step_selection))
                continue
            try:
                self._check_cancellation()
                record = self.execute_step(step, metadata)
            except ObservedCheckFailure as exc:
                record = exc.record
                self.failure = exc
            except CheckFailure as exc:
                if not steps:
                    raise
                self.failure = exc
                for ordinal, pending in enumerate(plan['steps'][i:], start=i + 1):
                    choice = ({'kind': 'package', 'id': pending['package_id']}
                              if pending['package_id'] is not None else pending['selection'])
                    omitted.append(omitted_step(ordinal, deepcopy(choice)))
                metadata.update(stored=False, reason=exc.code)
                return self.save_execution(plan, steps, omitted, metadata, status=self.partial_status(exc))
            self.known_records.append(record)
            self.ledger.save_run(record)
            steps.append({'ordinal': i + 1, 'package_id': step['package_id'], 'driver': step['driver'],
                          'selection': deepcopy(step['selection']), 'record': record})
            stopped = record['status'] != 'passed' or self.failure is not None
        if eligible_cache and not stopped:
            final_key, _ = self.inputs.key(profile, plan['options']['scope_paths'], plan['package'])
            if final_key != key:
                metadata['reason'] = 'declared_inputs_changed_during_run'
            else:
                metadata.update(stored=True, reason='fresh_stored' if plan['options']['fresh'] else 'miss_stored')
        result = self.build_execution(plan, steps, omitted, metadata,
                                      status=None if self.failure is None else self.partial_status(self.failure))
        if metadata['stored']:
            try:
                self.cache.store(key, result)
            except CheckFailure as exc:
                if exc.code != 'cache_store_failed':
                    raise
                metadata.update(stored=False, reason=exc.code)
                self.warnings.append(f'{exc.code}: {exc.cause} {exc.recovery}')
        self.ledger.save_result(result)
        return result

    @staticmethod
    def partial_status(failure):
        return 'unconfirmed' if failure.status == 'invalid' else failure.status

    def execute_step(self, step, metadata):
        self._check_cancellation()
        self.runtime.prerequisites(step['profile'], step['invocation'], step['environment'], step['field'])
        addresses = self.ledger.allocate(process=True)
        if self.invocation is not None:
            self.invocation.allocated(addresses)
        evidence_id = step['timeout']['evidence_run_id']
        if evidence_id is not None:
            self.ledger.reserve_diagnostic(evidence_id, addresses['id'])
        try:
            return self.execution.run(step, addresses, deepcopy(step['timeout']), step['stall'], metadata, step['subject'])
        except UnstartedCheck:
            if evidence_id is not None:
                self.ledger.release_unstarted_diagnostic(evidence_id, addresses['id'])
            raise

    def build_execution(self, plan, steps, omitted, metadata, *, status=None):
        addresses = self.ledger.allocate(process=False)
        result = {'kind': 'execution', 'selection': deepcopy(plan['selection']),
                  'status': aggregate([v['record'] for v in steps]) if status is None else status,
                  'result_id': addresses['id'],
                  'record_path': addresses['record_path'], 'provenance': self._provenance(),
                  'resolved_dependencies': list(plan['dependencies']), 'steps': steps,
                  'not_executed': omitted, 'cache': metadata}
        return result

    def save_execution(self, plan, steps, omitted, metadata, *, status=None):
        result = self.build_execution(plan, steps, omitted, metadata, status=status)
        self.ledger.save_result(result)
        return result

    def execute(self, request, *, admitted=None):
        if admitted is None:
            admitted = self.admit_batch(request)
        if self.establish_session is not None:
            self.establish_session()
        operation = request.operation
        if operation == 'list':
            return [v for group in admitted for v in group], 'ready'
        if operation in ('describe', 'result', 'package_impact'):
            return admitted, 'ready'
        if operation == 'preview':
            return [self.preview(v) for v in admitted], 'planned'
        if operation == 'calibrate':
            results = self.publisher.publish(admitted)
            status = aggregate(results, 'published' if any(v['status'] == 'published' for v in results) else 'unchanged')
            return results, status
        results, stopped = [], False
        for plan in admitted:
            if stopped:
                results.append(not_executed(plan['selection']))
                continue
            try:
                result = self.run(plan)
            except CheckFailure as exc:
                if not results:
                    raise
                self.failure = exc
                results.append(not_executed(plan['selection']))
                stopped = True
                continue
            results.append(result)
            stopped = item_status(result) not in ('passed', 'ready')
        return results, (self.failure.status if self.failure is not None else
                         aggregate([v for v in results if v['kind'] != 'not_executed']))
