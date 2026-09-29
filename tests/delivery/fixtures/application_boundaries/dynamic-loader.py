import importlib.util as loader
from pathlib import Path
spec = loader.spec_from_file_location("neighbor", Path(__file__).resolve().parents[1] / "sequencing" / "shell.py")
