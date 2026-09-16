"""One bounded reader, using explicit file and receipt storage ports."""
from ..modules.source_reader.domain import parse_read, text
from ..modules.foundation.errors import PoiseError


class SourceReader:
    def __init__(self, files, receipts, limits):
        self.files, self.receipts, self.limits = files, receipts, limits

    @staticmethod
    def _context(state):
        return {key: state[key] for key in ('generation', 'reason', 'event_id')}

    def context(self):
        with self.receipts.transaction() as state:
            return self._context(state)

    def reset(self, reason, event_id):
        if reason not in ('startup', 'resume', 'compact', 'clear', 'manual'):
            raise PoiseError('Unsupported context reset reason')
        text(event_id, 'event_id')
        with self.receipts.transaction() as state:
            if state['event_id'] != event_id:
                state.update(generation=state['generation']+1, reason=reason,
                             event_id=event_id, receipts=[])
            elif state['reason'] != reason:
                raise PoiseError('Context event identity conflict')
            return self._context(state)

    def read(self, request):
        req = parse_read(request, self.limits)
        with self.receipts.transaction() as state:
            if req['generation'] != state['generation']:
                raise PoiseError('Context generation changed; obtain context and reread')
            ledger = {r['receipt_id']: r for r in state['receipts']}
            if any(key not in ledger for key in req['acknowledged']):
                raise PoiseError('Unknown or expired acknowledged receipt')
            for key in req['acknowledged']:
                ledger[key]['acknowledged'] = True
            output, size, updates = [], 0, []
            # Validate/read all inputs before committing any receipt or acknowledgement.
            for item in req['reads']:
                value = self.files.read(req['cwd'], item, self.limits)
                receipt = self.files.receipt(value, state['generation'])
                known = ledger.get(receipt['receipt_id'])
                skip = bool(known and known['acknowledged'] and not item['reread'])
                status = 'unchanged' if skip else ('reread' if item['reread'] else 'read')
                result = {**receipt, 'status': status, 'reason': item['reason']}
                if not skip:
                    size += len(value['text'].encode('utf-8'))
                    if size > self.limits.max_output_bytes:
                        raise PoiseError('Batch output exceeds configured byte bound')
                    result['text'] = value['text']
                output.append(result)
                updates.append({**receipt, 'acknowledged': bool(known and known['acknowledged']),
                                'status': status, 'reason': item['reason']})
            for receipt in updates:
                # Latest observation wins; eviction permits rereading, never false suppression.
                ledger.pop(receipt['receipt_id'], None)
                ledger[receipt['receipt_id']] = receipt
            state['receipts'] = list(ledger.values())[-self.limits.max_receipts:]
            return {'schema': 'source-read-result-1', **self._context(state),
                    'items': output, 'text_bytes': size,
                    'coverage': 'explicit_ranges_only; acknowledgement is caller supplied'}
