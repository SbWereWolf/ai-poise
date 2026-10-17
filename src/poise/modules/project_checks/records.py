"""Validate the named-check records consumed by history, results and reuse.

These are persisted domain values, not configuration or a transport parser.
The ledger owns JSON decoding, row addresses and contextual storage failures.
"""
import re

from ..accounting.clock import ClockObservation
from ..foundation.errors import DomainError
from .contracts import exact, number, require, selection, text


def string(value, field, *, nullable=False):
    require(nullable and value is None or isinstance(value, str), field, 'Требуется строка.')


def boolean(value, field):
    require(type(value) is bool, field, 'Требуется boolean.')


def numeric(value, field, *, nullable=False, integer=False):
    if value is None and nullable:
        return
    number(value, field, integer=integer, zero=True)


def enumeration(value, choices, field, *, nullable=False):
    require(nullable and value is None or isinstance(value, str) and value in choices,
            field, 'Неподдерживаемое значение.')


def array(value, field):
    require(isinstance(value, list), field, 'Требуется список.')
    return enumerate(value)


def strings(value, field):
    for i, item in array(value, field):
        string(item, f'{field}[{i}]')


def chosen(value, field):
    selection(value, field)
    if value['kind'] == 'profile':
        text(value['workload_class'], field + '.workload_class')
        text(value['runtime_class'], field + '.runtime_class')


def checksum(value, field, *, git=False):
    require(isinstance(value, str) and re.fullmatch(
        r'(?:[0-9a-f]{40}|[0-9a-f]{64})' if git else r'[0-9a-f]{64}', value) is not None,
        field, 'Требуется полный checksum.')


def file_reference(value, field):
    if value is None:
        return
    exact(value, ('path', 'sha256', 'bytes'), field)
    text(value['path'], field + '.path')
    checksum(value['sha256'], field + '.sha256')
    numeric(value['bytes'], field + '.bytes', integer=True)


def clock(value, field):
    if value is None:
        return
    exact(value, ('audit_utc', 'comparison_domain', 'monotonic_ns'), field)
    text(value['audit_utc'], field + '.audit_utc')
    text(value['comparison_domain'], field + '.comparison_domain')
    numeric(value['monotonic_ns'], field + '.monotonic_ns', integer=True)
    try:
        ClockObservation(**value)
    except DomainError as exc:
        require(False, field + '.audit_utc', str(exc))


def cache(value, field):
    exact(value, ('eligible', 'hit', 'stored', 'key', 'source_result_id', 'reason'), field)
    for name in ('eligible', 'hit', 'stored'):
        boolean(value[name], field + '.' + name)
    for name in ('key', 'source_result_id'):
        string(value[name], field + '.' + name, nullable=True)
    text(value['reason'], field + '.reason')


def error(value, field):
    if value is None:
        return
    exact(value, ('code', 'field', 'cause', 'reason', 'recovery', 'references'), field)
    for name in ('code', 'field', 'cause', 'reason', 'recovery'):
        string(value[name], field + '.' + name)
    refs = value['references']
    where = field + '.references'
    exact(refs, ('response_path', 'record_paths', 'stdout_paths', 'stderr_paths'), where)
    string(refs['response_path'], where + '.response_path', nullable=True)
    for name in ('record_paths', 'stdout_paths', 'stderr_paths'):
        strings(refs[name], where + '.' + name)


def subject(value, field):
    if value is None:
        return
    exact(value, ('kind', 'mode', 'declaration', 'paths', 'state'), field)
    enumeration(value['kind'], ('documentation',), field + '.kind')
    enumeration(value['mode'], ('working', 'committed'), field + '.mode')
    enumeration(value['state'], ('admitted', 'unchanged', 'changed'), field + '.state')
    strings(value['paths'], field + '.paths')
    declaration = value['declaration']
    if declaration is None:
        require(value['mode'] == 'working', field + '.declaration', 'Требуется committed declaration.')
        return
    where = field + '.declaration'
    exact(declaration, ('version', 'repository_root', 'baseline_commit', 'candidate_commit',
                        'candidate_tree', 'paths', 'required_paths'), where)
    require(type(declaration['version']) is int and declaration['version'] == 1,
            where + '.version', 'Требуется version1.')
    text(declaration['repository_root'], where + '.repository_root')
    for name in ('baseline_commit', 'candidate_commit', 'candidate_tree'):
        checksum(declaration[name], where + '.' + name, git=True)
    for name in ('paths', 'required_paths'):
        strings(declaration[name], where + '.' + name)


def process_run(value, project, field='run'):
    exact(value, ('schema', 'run_id', 'project', 'checkout', 'selection', 'profile_sha256',
                  'argv', 'cwd', 'runtime', 'status', 'executed', 'actual_exit_code',
                  'started', 'finished', 'duration_seconds', 'termination', 'timeout',
                  'progress', 'parser', 'evidence', 'cache', 'subject', 'stdout',
                  'stderr', 'record_path', 'verified_success', 'error'), field)
    enumeration(value['schema'], ('poise-named-check-run-1',), field + '.schema')
    require(value['project'] == project, field + '.project', 'Запись другого проекта.')
    for name in ('run_id', 'checkout', 'cwd', 'record_path'):
        text(value[name], field + '.' + name)
    chosen(value['selection'], field + '.selection')
    require(value['selection']['kind'] == 'profile', field + '.selection.kind', 'Требуется профиль.')
    checksum(value['profile_sha256'], field + '.profile_sha256')
    strings(value['argv'], field + '.argv')
    require(bool(value['argv']), field + '.argv', 'Требуется непустая команда.')
    runtime = value['runtime']
    exact(runtime, ('executable', 'platform', 'invocation'), field + '.runtime')
    string(runtime['executable'], field + '.runtime.executable', nullable=True)
    string(runtime['platform'], field + '.runtime.platform')
    enumeration(runtime['invocation'], ('exec', 'windows_cmd'), field + '.runtime.invocation')
    enumeration(value['status'], ('passed', 'failed', 'timed_out', 'stalled', 'cancelled', 'unconfirmed'),
                field + '.status')
    for name in ('executed', 'verified_success'):
        boolean(value[name], field + '.' + name)
    # Process exits may be negative signal numbers; bool is never an exit code.
    require(value['actual_exit_code'] is None or type(value['actual_exit_code']) is int,
            field + '.actual_exit_code', 'Требуется integer или null.')
    numeric(value['duration_seconds'], field + '.duration_seconds', nullable=True)
    for name in ('started', 'finished'):
        clock(value[name], field + '.' + name)
    termination = value['termination']
    exact(termination, ('capture_complete', 'child_reaped', 'process_group_stopped',
                        'timed_out', 'cancelled', 'timeout_reason'), field + '.termination')
    for name in ('capture_complete', 'child_reaped', 'process_group_stopped', 'timed_out', 'cancelled'):
        boolean(termination[name], field + '.termination.' + name)
    string(termination['timeout_reason'], field + '.termination.timeout_reason', nullable=True)
    timeout = value['timeout']
    exact(timeout, ('configured_seconds', 'effective_seconds', 'basis', 'evidence_run_id', 'override_reason'),
          field + '.timeout')
    for name in ('configured_seconds', 'effective_seconds'):
        numeric(timeout[name], field + '.timeout.' + name)
    enumeration(timeout['basis'], ('initial', 'verified_success_history', 'diagnostic_override'),
                field + '.timeout.basis')
    for name in ('evidence_run_id', 'override_reason'):
        string(timeout[name], field + '.timeout.' + name, nullable=True)
    progress = value['progress']
    exact(progress, ('mode', 'observed', 'completed', 'total', 'stage', 'max_gap_seconds'), field + '.progress')
    enumeration(progress['mode'], ('regex', 'unavailable'), field + '.progress.mode')
    boolean(progress['observed'], field + '.progress.observed')
    for name in ('completed', 'total'):
        numeric(progress[name], field + '.progress.' + name, nullable=True, integer=True)
    numeric(progress['max_gap_seconds'], field + '.progress.max_gap_seconds', nullable=True)
    string(progress['stage'], field + '.progress.stage', nullable=True)
    parsed = value['parser']
    if parsed is not None:
        where = field + '.parser'
        exact(parsed, ('name', 'confirmed', 'summary', 'counts', 'failures'), where)
        string(parsed['name'], where + '.name')
        string(parsed['summary'], where + '.summary')
        boolean(parsed['confirmed'], where + '.confirmed')
        require(isinstance(parsed['counts'], dict), where + '.counts', 'Требуется объект.')
        for name, count in parsed['counts'].items():
            numeric(count, where + '.counts.' + name, integer=True)
        for i, failure in array(parsed['failures'], where + '.failures'):
            address = where + f'.failures[{i}]'
            exact(failure, ('name', 'message', 'source'), address)
            for name in ('name', 'message', 'source'):
                string(failure[name], address + '.' + name, nullable=name == 'source')
    evidence = value['evidence']
    exact(evidence, ('status', 'manifest_path', 'files'), field + '.evidence')
    enumeration(evidence['status'], ('not_declared', 'complete', 'missing', 'incomplete', 'invalid'),
                field + '.evidence.status')
    string(evidence['manifest_path'], field + '.evidence.manifest_path', nullable=True)
    for i, reference in array(evidence['files'], field + '.evidence.files'):
        require(reference is not None, field + f'.evidence.files[{i}]', 'Требуется файл.')
        file_reference(reference, field + f'.evidence.files[{i}]')
    cache(value['cache'], field + '.cache')
    subject(value['subject'], field + '.subject')
    for name in ('stdout', 'stderr'):
        file_reference(value[name], field + '.' + name)
    error(value['error'], field + '.error')


def execution_item(value, project, field='result'):
    exact(value, ('kind', 'selection', 'status', 'result_id', 'record_path', 'provenance',
                  'resolved_dependencies', 'steps', 'not_executed', 'cache'), field)
    enumeration(value['kind'], ('execution',), field + '.kind')
    chosen(value['selection'], field + '.selection')
    enumeration(value['status'], ('ready', 'passed', 'failed', 'timed_out', 'stalled', 'cancelled',
                                  'unconfirmed', 'unavailable'), field + '.status')
    for name in ('result_id', 'record_path'):
        text(value[name], field + '.' + name)
    provenance = value['provenance']
    where = field + '.provenance'
    exact(provenance, ('project', 'checkout', 'policy_sha256', 'profiles_sha256', 'source_commit', 'source_tree'), where)
    require(provenance['project'] == project, where + '.project', 'Результат другого проекта.')
    text(provenance['checkout'], where + '.checkout')
    for name in ('policy_sha256', 'profiles_sha256', 'source_commit', 'source_tree'):
        checksum(provenance[name], where + '.' + name, git=name in ('source_commit', 'source_tree'))
    strings(value['resolved_dependencies'], field + '.resolved_dependencies')
    for i, step in array(value['steps'], field + '.steps'):
        where = field + f'.steps[{i}]'
        exact(step, ('ordinal', 'package_id', 'driver', 'selection', 'record'), where)
        number(step['ordinal'], where + '.ordinal', integer=True)
        for name in ('package_id', 'driver'):
            string(step[name], where + '.' + name, nullable=True)
        chosen(step['selection'], where + '.selection')
        process_run(step['record'], project, where + '.record')
    for i, step in array(value['not_executed'], field + '.not_executed'):
        where = field + f'.not_executed[{i}]'
        exact(step, ('ordinal', 'selection', 'reason'), where)
        number(step['ordinal'], where + '.ordinal', integer=True)
        chosen(step['selection'], where + '.selection')
        string(step['reason'], where + '.reason')
    cache(value['cache'], field + '.cache')
