from pathlib import Path
import sqlite3
foreign = Path(__file__).resolve().parents[1] / "sequencing" / "state" / "target.sqlite"
transformed = str(foreign).replace("target", input())
sqlite3.connect(transformed)
