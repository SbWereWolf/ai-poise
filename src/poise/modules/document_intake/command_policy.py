"""Strict known command-policy leaves; retain unknown configuration verbatim."""
import math
import re
from .command import CommandFailure


class CommandPolicy:
    @staticmethod
    def parse(raw):
        def require(path, predicate):
            value = raw
            for key in path.split('.'):
                if not isinstance(value, dict) or key not in value:
                    raise CommandFailure('invalid_policy', field=path)
                value = value[key]
            if not predicate(value):
                raise CommandFailure('invalid_policy', field=path)
            return value
        text = lambda v: isinstance(v, str) and bool(v.strip())
        integer = lambda v: type(v) is int and v > 0
        boolean = lambda v: type(v) is bool
        strings = lambda v: isinstance(v, list) and all(text(x) for x in v) and len(v) == len(set(v))
        require('schema', lambda v: v == 'poise-command-intake-policy-1')
        require('version', text)
        for group in ('classifier', 'registry'):
            require(group + '.mode', lambda v: v in ('audit', 'enforce'))
        for group, fields in {
            'classifier.complete_reads': ('max_files_per_call', 'small_bundle_max_files', 'small_bundle_max_bytes', 'small_bundle_max_lines'),
            'classifier.bounded_reads': ('max_files_per_call', 'max_lines_per_file', 'max_total_lines', 'max_bytes_per_file', 'max_total_bytes'),
            'classifier.parser': ('max_nested_shell_depth', 'max_reader_invocations_per_call', 'max_reader_arguments_per_invocation'),
            'registry.versioning': ('hash_max_bytes',),
            'registry.same_generation': ('min_new_lines',),
            'registry.post_generation': ('max_lines_per_file', 'max_total_lines'),
            'registry.post_result': ('minimum_response_bytes',),
            'context': ('max_documents', 'max_ranges_per_document', 'max_pending_records', 'max_bytes'),
            'feedback': ('max_bytes', 'max_lines'),
        }.items():
            for field in fields:
                require(group + '.' + field, integer)
        require('registry.same_generation.min_new_fraction_percent', lambda v: type(v) is int and 0 <= v <= 100)
        for field in ('deny_globs', 'deny_reader_loops', 'deny_find_exec_readers', 'deny_xargs_readers',
                      'deny_nested_shell_readers', 'deny_obvious_interpreter_bypass', 'deny_command_substitutions', 'deny_eval_readers'):
            require('classifier.unknown_expansion.' + field, boolean)
        require('classifier.unknown_expansion.unclassified_behavior', lambda v: v in ('allow', 'deny', 'allow_with_audit'))
        for field in ('complete', 'range_capable', 'conservative', 'nested_shells',
                      'interpreter_bypass_patterns', 'interpreter_metadata_patterns'):
            values = require('classifier.readers.' + field, strings)
            if field.endswith('_patterns'):
                try:
                    for expression in values:
                        re.compile(expression)
                except re.error as exc:
                    raise CommandFailure('invalid_policy', field='classifier.readers.' + field) from exc
        for group, fields in {
            'registry.same_generation': ('deny_exact_repeats', 'deny_fully_covered_ranges'),
            'registry.post_generation': ('allow_targeted_reread', 'deny_complete_reread'),
            'registry.post_result': ('unknown_exit_code_is_success',),
            'audit': ('record_allowed_readers',),
        }.items():
            for field in fields:
                require(group + '.' + field, boolean)
        require('registry.post_generation.unknown_repeat_behavior', lambda v: v in ('deny', 'allow', 'allow_with_audit'))
        require('registry.post_result.output_field', text)
        for field in ('stream_fields', 'exit_fields'):
            require('registry.post_result.' + field, lambda v: strings(v) and bool(v))
        require('registry.retention.max_pending_age_seconds', lambda v: type(v) in (int, float) and math.isfinite(v) and v > 0)
        for group in ('classifier.bounded_reads', 'registry.post_generation'):
            values = raw
            for part in group.split('.'):
                values = values[part]
            for unit in ('lines', 'bytes'):
                key = 'max_' + unit + '_per_file'
                if key in values and values[key] > values['max_total_' + unit]:
                    raise CommandFailure('invalid_policy', field=group + '.' + key)
        return raw
