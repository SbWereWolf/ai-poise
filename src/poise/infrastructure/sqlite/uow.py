from .handoff import SqliteHandoffRepository
from .actions import SqliteActionRepository
from .sprints import SqliteSprintRepository
from .evidence import SqliteEvidenceRepository
from .tasks import SqliteTaskRepository, SqliteExecutionRepository
from .ownership import SqliteOwnershipRepository
from .accounting import SqliteAccountingCycles
from .work_packets import SqliteWorkPacketRepository
from .artifacts import SqliteArtifactRepository
from .task_delivery import SqliteTaskDeliveryRepository


class SqliteUnitOfWork:
    """Commit on successful context exit; rollback on any exception."""
    def __init__(self, database, processes):
        self.database = database
        self.processes = processes

    def __enter__(self):
        self._transaction = self.database.transaction()
        connection = self._transaction.__enter__()
        self.handoffs = SqliteHandoffRepository(connection)
        self.sprints = SqliteSprintRepository(connection)
        self.tasks = SqliteTaskRepository(connection)
        self.actions = SqliteActionRepository(connection,self.tasks)
        self.execution = SqliteExecutionRepository(connection)
        self.evidence = SqliteEvidenceRepository(connection)
        self.ownership = SqliteOwnershipRepository(connection,self.processes)
        self.accounting_cycles = SqliteAccountingCycles(connection)
        self.work_packets = SqliteWorkPacketRepository(connection)
        self.artifacts = SqliteArtifactRepository(connection)
        self.task_delivery = SqliteTaskDeliveryRepository(connection)
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._transaction.__exit__(exc_type, exc, traceback)
