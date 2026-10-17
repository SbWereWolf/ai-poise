"""Only an OS fault-arrangement specimen, never an ERP maintenance action."""
import argparse
from pathlib import Path
import os

parser = argparse.ArgumentParser()
parser.add_argument('source')
parser.add_argument('target')
parser.add_argument('--remove-old', action='store_true')
arguments = parser.parse_args()
if arguments.remove_old:
    Path(arguments.target).unlink(missing_ok=True)
os.replace(arguments.source, arguments.target)
