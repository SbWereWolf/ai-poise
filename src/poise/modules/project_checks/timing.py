"""Calibration uses only comparable public Clock observations and actual runs."""
from dataclasses import asdict
import math

from .contracts import CheckFailure


def observation(value):
    return asdict(value)


def elapsed(start, finish):
    if (start['comparison_domain'] != finish['comparison_domain'] or
            finish['monotonic_ns'] < start['monotonic_ns']):
        return None
    return (finish['monotonic_ns'] - start['monotonic_ns']) / 1_000_000_000


def identity(record):
    chosen = record['selection']
    return record['project'], chosen['id'], chosen['workload_class'], chosen['runtime_class']


def eligible(record, expected_identity):
    return (identity(record) == expected_identity and record['verified_success']
            and record['status'] == 'passed' and record['executed']
            and record['duration_seconds'] is not None
            and record['termination']['capture_complete']
            and record['started'] is not None and record['finished'] is not None
            and elapsed(record['started'], record['finished']) is not None)


class TimingPolicy:
    def __init__(self, multiplier, override_max):
        self.multiplier = multiplier
        self.override_max = override_max

    def limits(self, profile, samples):
        if not samples:
            return profile['execution_timeout_seconds'], profile['stall_timeout_seconds'], 'initial'
        hard = math.ceil(max(v['duration_seconds'] for v in samples) * self.multiplier)
        gaps = [v['progress']['max_gap_seconds'] for v in samples
                if v['progress']['mode'] == 'regex'
                and v['progress']['max_gap_seconds'] is not None]
        stall = math.ceil(max(gaps) * self.multiplier) if gaps else profile['stall_timeout_seconds']
        return hard, stall, 'verified_success_history'

    def admit_override(self, override, ordinary, evidence, expected_identity, consumed, field):
        seconds = override['seconds']
        if seconds <= ordinary:
            raise CheckFailure('override_invalid', field + '.seconds',
                               f'Запрошено {seconds} при обычном ограничении {ordinary}.',
                               f'Укажите seconds больше {ordinary} и не выше настроенного максимума с доказательством исходного прерывания.',
                               reason=f'Диагностическое ограничение {seconds} должно превышать обычное ограничение {ordinary}.')
        if seconds > self.override_max:
            raise CheckFailure('override_invalid', field + '.seconds',
                               f'Превышен настроенный максимум {self.override_max}.',
                               f'Укажите seconds не выше {self.override_max}.')
        if (evidence is None or identity(evidence) != expected_identity or
                evidence['status'] not in ('timed_out', 'stalled') or
                evidence['timeout']['basis'] == 'diagnostic_override'):
            raise CheckFailure('override_invalid', field + '.evidence_run_id',
                               'Нет исходного прерывания этой проверки.',
                               'Укажите реальный timed_out или stalled запуск этого профиля без diagnostic override.')
        if consumed:
            raise CheckFailure('override_consumed', field + '.evidence_run_id',
                               'Основание уже использовано для диагностического запуска.',
                               'Получите новое исходное прерывание обычного запуска.')

    def calibrated_values(self, samples):
        duration = max(v['duration_seconds'] for v in samples)
        gaps = [v['progress']['max_gap_seconds'] for v in samples
                if v['progress']['mode'] == 'regex' and v['progress']['max_gap_seconds'] is not None]
        gap = max(gaps) if gaps else 0.0
        return {'observed_max_success_seconds': duration,
                'observed_max_progress_gap_seconds': gap,
                'execution_timeout_seconds': math.ceil(duration * self.multiplier),
                'stall_timeout_seconds': math.ceil(gap * self.multiplier)}
