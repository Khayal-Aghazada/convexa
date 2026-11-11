"""Convexa — local file converter. Double-click to start (runs in the tray)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convexa.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
