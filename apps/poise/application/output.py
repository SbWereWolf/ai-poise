"""Independent saved-output coordination; no Session, catalog or command execution."""
from domain.output import minimal_input, range_input, source_view, range_view, representation


class OutputOperations:
    def __init__(self, limits, files):
        self.limits, self.files = limits, files

    def minimal(self, value):
        sources = minimal_input(value, self.limits)
        items = [source_view(source, self.files.size(source, index))
                 for index, source in enumerate(sources)]
        return representation('minimal', items)

    def range(self, value):
        requests = range_input(value, self.limits)
        items = []
        for index, request in enumerate(requests):
            size, raw = self.files.read_range(request['source'], request['start'], request['count'], index)
            items.append(range_view(request['source'], size, request['start'], raw))
        return representation('range', items)
