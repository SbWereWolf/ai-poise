from pathlib import Path as P
import os as operating
from sqlite3 import connect as database
foreign = P(__file__).resolve().parents[1] / "sequencing" / "state" / "target.sqlite"
own = P(__file__).resolve().parent / "state" / "owned.sqlite"
