from ...modules.foundation.errors import PoiseError


class SqliteWorkPacketRepository:
    """Current delivery identities; verified results and evidence live elsewhere."""

    def __init__(self, connection):
        self.db = connection

    def current(self, task_id: str, stage: str, iteration: int) -> str | None:
        row = self.db.execute(
            "SELECT digest FROM work_packets "
            "WHERE task_id=? AND stage=? AND iteration=?",
            (task_id, stage, iteration),
        ).fetchone()
        return None if row is None else row[0]

    def remember(
        self, task_id: str, stage: str, iteration: int, digest: str,
    ) -> None:
        current = self.current(task_id, stage, iteration)
        if current is not None and current != digest:
            raise PoiseError("Verified packet cannot be replaced")
        self.db.execute(
            "INSERT OR IGNORE INTO work_packets VALUES(?,?,?,?)",
            (task_id, stage, iteration, digest),
        )

    def invalidate(self, task_id: str) -> None:
        self.db.execute("DELETE FROM work_packets WHERE task_id=?", (task_id,))
