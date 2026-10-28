"""Coordinate existing Session, bounded sources and one atomic reader update."""
import uuid

from application.sessions import missing_session
from domain.generic import Problem
from domain.reader import ReaderState, parse_context, parse_reset, parse_read, parse_next


class ReaderOperations:
    def __init__(self, sessions, repository, files, limits, receipt_factory=None):
        self.sessions, self.repository = sessions, repository
        self.files, self.limits = files, limits
        self.receipt_factory = receipt_factory or (lambda: uuid.uuid4().hex)

    def _session(self):
        caller = self.sessions.caller(create=False)
        return caller, self.sessions.observed(caller)

    def _current_session(self, unit, caller):
        session = unit.sessions.observe(caller.key)
        if session is None:
            missing_session()
        return session

    def context(self, value):
        parse_context(value)
        caller, observed = self._session()
        existing = self.repository.observe(observed.id)
        if existing is not None:
            return existing.context()
        with self.repository.transaction() as unit:
            session = self._current_session(unit, caller)
            state = unit.load(session.id)
            if state is None:
                state = ReaderState.initial()
                unit.save(session.id, state)
            return state.context()

    def reset(self, value):
        request = parse_reset(value)
        caller, _ = self._session()
        with self.repository.transaction() as unit:
            session = self._current_session(unit, caller)
            previous = unit.load(session.id)
            state = (previous or ReaderState.initial()).reset(request['reason'], request['event_id'])
            if previous is None or state.data != previous.data:
                unit.save(session.id, state)
            return state.context()

    def read(self, value):
        return self._batch(parse_read(value, self.limits), following=False)

    def next(self, value):
        return self._batch(parse_next(value, self.limits), following=True)

    def _state(self, state, request, following):
        if state is None:
            raise Problem('reader_context_missing', {}, 'reader:context', 'unavailable')
        cursors = [item['cursor'] for item in request['reads']] if following else []
        state.check_generation(request['generation'], cursors)
        state.check_acknowledged(request['acknowledged'])
        return state

    def _batch(self, request, *, following):
        caller, session = self._session()
        self._state(self.repository.observe(session.id), request, following)
        cwd = None if following else self.files.cwd(request['cwd'])
        portions = []
        # External filesystem I/O precedes the short SQLite write transaction.
        for index, item in enumerate(request['reads']):
            field = '/input/reads/' + str(index)
            if following:
                cursor = item['cursor']
                path = self.files.path(cursor['path'])
                unit, start, count = cursor['unit'], cursor['next'], item['count']
                version, start_field = cursor['version'], field + '/cursor/next'
            else:
                path = self.files.path(item['path'], cwd)
                selection = item['range']
                unit, start, count = selection['unit'], selection['start'], selection['count']
                version, start_field = None, field + '/range/start'
            document = self.files.read(path, self.limits, version)
            portions.append(document.select(unit, start, count, start_field))
        with self.repository.transaction() as unit:
            current = self._current_session(unit, caller)
            state = self._state(unit.load(current.id), request, following)
            candidate, response = state.complete(portions, request['reads'], request['acknowledged'],
                                                  self.limits, self.receipt_factory)
            if candidate.data != state.data:
                unit.save(current.id, candidate)
            return response
