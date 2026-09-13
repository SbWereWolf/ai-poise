from .handoff import SqliteHandoffRepository
from .actions import SqliteActionRepository
from .sprints import SqliteSprintRepository
from .evidence import SqliteEvidenceRepository
from .tasks import SqliteTaskRepository, SqliteExecutionRepository
from .ownership import SqliteOwnershipRepository


class SqliteUnitOfWork:
    """Commit on successful context exit; rollback on any exception."""
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        self._transaction = self.database.transaction()
        connection = self._transaction.__enter__()
        self.handoffs = SqliteHandoffRepository(connection)
        self.actions = SqliteActionRepository(connection)
        self.sprints = SqliteSprintRepository(connection)
        self.tasks = SqliteTaskRepository(connection)
        self.execution = SqliteExecutionRepository(connection)
        self.evidence = SqliteEvidenceRepository(connection)
        self.ownership = SqliteOwnershipRepository(connection)
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._transaction.__exit__(exc_type, exc, traceback)
