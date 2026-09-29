from pathlib import Path
import subprocess
peer = Path(__file__).resolve().parents[1] / "sequencing"
argv = [str(peer / "bin" / "sequencing"), "--config", str(peer / "config" / "sequencing.json")]
subprocess.run(argv)
