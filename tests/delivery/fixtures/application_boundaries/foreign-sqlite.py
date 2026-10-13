from pathlib import Path
from sqlite3 import connect as database
foreign = Path(__file__).resolve().parents[1] / "sequencing" / "state" / "sequencing.sqlite"
alias = foreign
database(alias)
