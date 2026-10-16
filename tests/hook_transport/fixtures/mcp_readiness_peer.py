"""Count one process, then delegate all MCP protocol work to the existing peer."""
import runpy
import sys
from pathlib import Path

counter, state, peer = map(Path, sys.argv[1:4])
with counter.open("a", encoding="utf-8") as stream:
    stream.write("called\n")
sys.argv = [str(peer), state.read_text(encoding="utf-8").strip()]
runpy.run_path(str(peer), run_name="__main__")
