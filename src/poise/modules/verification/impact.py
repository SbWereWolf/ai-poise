"""Pure impact of AI-poise package declarations; no command or filesystem owner."""
from ..foundation.paths import matches_component_path


def matches_member(path: str, pattern: str) -> bool:
    """Match C016 glob segments, including zero directories for **/.

    Unlike fnmatch on an entire path, a single * cannot cross a directory.
    Deleted inputs are matched without enumerating the current filesystem.
    """
    return matches_component_path(path, pattern)


def select_package_impact(packages: list[dict], changed_paths: list[str]) -> dict:
    """Direct owners plus their explicitly declared one-hop integration checks.

    This is not recursive test-dependency inference. A boundary that itself has
    boundaries does not pull in its whole component graph.
    """
    reasons = []
    direct = set()
    for path in sorted(set(changed_paths)):
        for package in sorted(packages, key=lambda p: p['id']):
            for kind, patterns in package['members'].items():
                for pattern in patterns:
                    if matches_member(path, pattern):
                        direct.add(package['id'])
                        reasons.append({'path': path, 'package': package['id'], 'owner': package['owner'],
                                        'kind': kind, 'pattern': pattern})
    integration_reasons = sorted(
        ({'from_package': package['id'], 'package': boundary}
         for package in packages if package['id'] in direct
         for boundary in package['integration_boundaries'] if boundary not in direct),
        key=lambda r: (r['from_package'], r['package']),
    )
    integration = {r['package'] for r in integration_reasons}
    return {
        'schema': 'ai-poise-package-impact-1',
        'direct_packages': sorted(direct), 'integration_packages': sorted(integration),
        'suggested_packages': sorted(direct | integration),
        'unmapped_paths': sorted(set(changed_paths) - {r['path'] for r in reasons}),
        'reasons': sorted(reasons, key=lambda r: (r['path'], r['package'], r['kind'], r['pattern'])),
        'integration_reasons': integration_reasons,
    }
