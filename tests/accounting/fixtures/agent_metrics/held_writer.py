"""Hold a real ordinary reserved writer transaction; no fabricated read failure."""
import json
import os
from pathlib import Path
import select
import sys

from poise.infrastructure.sqlite.accounting_database import AccountingDatabase


def main():
    request = json.loads(sys.stdin.readline())
    database = AccountingDatabase(Path(request["database"]), Path(request["lock"]),
                                  request["wait"], request["poll"])
    with database.transaction() as connection:
        assert connection.in_transaction
        print(json.dumps({"pid": os.getpid(), "database": str(database.path),
                          "boundary": "actual-reserved-writer-before-commit",
                          "in_transaction": connection.in_transaction}), flush=True)
        if not select.select([sys.stdin], [], [], 30)[0]:
            raise RuntimeError("F16_OWN_WRITER_RELEASE_NOT_RECEIVED")
        assert sys.stdin.readline() == "release\n"
    print(json.dumps({"pid": os.getpid(), "boundary": "actual-writer-committed"}), flush=True)


if __name__ == "__main__":
    main()
