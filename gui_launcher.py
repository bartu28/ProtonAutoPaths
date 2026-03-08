"""Entry point that opens the GUI directly (no console window)."""

import sys
from pathlib import Path

# Ensure the bundled/source directory is on the path
sys.path.insert(0, str(Path(__file__).parent))

from main import run_gui

run_gui()
