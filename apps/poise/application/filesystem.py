"""One common file-policy decision, composed before actual target effects."""
from pathlib import Path

from domain.filesystem import PolicyIssue, decide, parse_policy, parse_request, parse_writes, rejected
from domain.generic import Problem, invalid


def raise_rejected(result):
    if result['status'] == 'rejected':
        raise Problem('path_policy_rejected', result['error'], 'correct:paths', result=result)


class FilePolicy:
    def __init__(self, raw_policy, files):
        self.files = files
        self.roots = files.roots(parse_policy(raw_policy))

    def evaluate(self, request):
        try:
            base, specifications = parse_request(request)
            base = self.files.base(base)
            effects = self.files.effects(specifications, base)
            return decide(self.roots, effects)
        except PolicyIssue as issue:
            return rejected(issue)

    def check(self, request):
        result = self.evaluate(request)
        raise_rejected(result)
        if result['decision'] == 'deny':
            raise Problem('path_denied', {}, 'configure:paths', result=result)
        # These are the same canonical destinations, without another resolution.
        return result, tuple(Path(item['path']) for item in result['effects'])


def configured_policy(raw_config, files):
    if 'paths' not in raw_config:
        raise Problem('paths_not_configured', {}, 'configure:paths', 'unavailable')
    try:
        return FilePolicy(raw_config['paths'], files)
    except PolicyIssue as issue:
        raise_rejected(rejected(issue))


def write_files(value, policy, files):
    base, items = parse_writes(value)
    decision, paths = policy.check({'schema': 'poise-fs-request/v1', 'base': base,
                                   'effects': [{'operation': 'write', 'path': path}
                                               for path, _ in items]})
    for index, path in enumerate(paths):
        if not files.file_or_missing(path):
            invalid('/input/writes/' + str(index) + '/path')
    result = {'schema': 'poise-write-result/v1', 'status': 'written',
              'policy': decision, 'items': []}
    for index, (path, (_, data)) in enumerate(zip(paths, items, strict=True)):
        try:
            count = files.write(path, data)
        except OSError as error:
            result['status'] = 'failed'
            raise Problem('file_write_failed', {'index': index, 'path': str(path), 'errno': error.errno},
                          'inspect:files', 'unavailable', result) from None
        result['items'].append({'index': index, 'path': str(path), 'bytes_written': count})
    return result
