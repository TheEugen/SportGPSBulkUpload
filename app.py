"""Frozen-executable entry point for PyInstaller.

Running the package with ``python -m komoot_bulk_upload`` is the normal way to
use this tool; PyInstaller needs a plain script as its starting point, so this
just hands off to the package's CLI ``main`` (which also dispatches to ``--gui``).
"""

import sys

from komoot_bulk_upload.cli import main

if __name__ == "__main__":
    sys.exit(main())
