"""Windowed (no-console) entry point for the GUI-only PyInstaller build.

The CLI build (``app.py``) keeps a console for credential prompts; this one
launches straight into the tkinter GUI so it can be built with ``--windowed``
and double-clicked without a console window flashing up behind it.
"""

import sys

from komoot_bulk_upload.gui import run

if __name__ == "__main__":
    sys.exit(run())
