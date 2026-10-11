"""Logical selectors in an immutable execution receipt."""


def receipt_materials(receipt):
    result = {}
    for stream in ('stdout', 'stderr'):
        if stream in receipt:
            result[stream] = {'path': receipt[stream], 'digest': receipt[stream + '_digest']}
    for output in receipt.get('outputs', []):
        if output['status'] == 'captured':
            result['output/' + output['id']] = {key: output[key] for key in ('path', 'digest')}
    for system, spec in receipt.get('systems', {}).items():
        for index, config in enumerate(spec['configs']):
            result[f'configuration/{system}/{index}'] = {
                'path': config['captured_path'], 'digest': config['digest']}
    for key in ('timeout_maintenance', 'acceptance_manifest'):
        if receipt.get(key) is not None:
            result[key] = receipt[key]
    return result
