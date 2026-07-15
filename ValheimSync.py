"""Punto de entrada de ValheimSync."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from valheim_sync.ui import main

if __name__ == "__main__":
    main()
