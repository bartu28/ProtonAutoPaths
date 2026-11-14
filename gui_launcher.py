"""PyInstaller entry point (built without a console window)."""

import sys
from pathlib import Path

# Ensure the bundled/source directory is on the path
sys.path.insert(0, str(Path(__file__).parent))

from main import main

# Go through main() so the exe honours --scan-and-apply (the logon entry).
raise SystemExit(main())
