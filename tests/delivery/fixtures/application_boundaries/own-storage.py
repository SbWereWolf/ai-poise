import json
import sqlite3
from pathlib import Path
CONFIG = Path(__file__).resolve().parent / "config" / "requirements.json"
config = json.loads(CONFIG.read_text(encoding="utf-8"))
state = (CONFIG.parent / config["paths"]["state"]).resolve()
database = state / config["paths"]["database"]
sqlite3.connect(database)
raise RuntimeError("Inspected source must never execute")
