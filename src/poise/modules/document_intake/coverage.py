"""Interval and generation policy over explicit ledger values; no I/O."""


def union_ranges(ranges):
    merged = []
    for first, last in sorted(ranges):
        if type(first) is not int or type(last) is not int or first < 1 or last < first:
            raise ValueError('Invalid line interval')
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(last, merged[-1][1])
        else:
            merged.append([first, last])
    return merged


def subtract_ranges(requested, covered):
    remaining = union_ranges(requested)
    for left, right in union_ranges(covered):
        result = []
        for first, last in remaining:
            if right < first or left > last:
                result.append([first, last])
            else:
                if first < left:
                    result.append([first, left - 1])
                if right < last:
                    result.append([right + 1, last])
        remaining = result
    return remaining


def line_count(ranges):
    return sum(last - first + 1 for first, last in union_ranges(ranges))


def coverage_decision(plans, history, generation, policy):
    """Include earlier plans in the same call without committing their effects."""
    seen = list(history)
    reread_lines = 0
    for plan in plans:
        prior = [row for row in seen if row['path'] == plan['path']
                 and row['version_key'] == plan['version_key']]
        current = [row for row in prior if row['generation'] == generation]
        older = [row for row in prior if row['generation'] < generation]
        same = policy['same_generation']
        requested = plan['line_ranges']
        if plan['coverage_kind'] == 'operation':
            exact = any(row['operation_digest'] == plan['operation_digest'] for row in current)
            if exact and same['deny_exact_repeats']:
                return 'duplicate_document_operation'
        else:
            complete = any(row['coverage_kind'] == 'complete' for row in current)
            if plan['coverage_kind'] == 'complete' and complete and same['deny_exact_repeats']:
                return 'duplicate_complete_document'
            covered = [r for row in current for r in row['line_ranges']]
            novel = [] if complete else subtract_ranges(requested, covered)
            count, new = line_count(requested), line_count(novel)
            if current and new == 0 and same['deny_fully_covered_ranges']:
                return 'duplicate_document_range'
            if count > new and new < same['min_new_lines'] and new * 100 < count * same['min_new_fraction_percent']:
                return 'mostly_redundant_document_range'
        if older and not current:
            post = policy['post_generation']
            kind = plan['coverage_kind']
            if kind == 'operation':
                if post['unknown_repeat_behavior'] == 'deny':
                    return 'unbounded_reread_after_generation'
                if post['unknown_repeat_behavior'] == 'allow_with_audit':
                    return 'audit_unbounded_reread_after_generation'
            elif kind == 'complete' and post['deny_complete_reread']:
                return 'complete_reread_after_generation'
            elif kind == 'lines':
                if not post['allow_targeted_reread']:
                    return 'post_generation_reread_disabled'
                count = line_count(requested)
                if count > post['max_lines_per_file']:
                    return 'post_generation_file_budget_exceeded'
                reread_lines += count
                if reread_lines > post['max_total_lines']:
                    return 'post_generation_total_budget_exceeded'
        seen.append(plan | {'generation': generation})
    return None
