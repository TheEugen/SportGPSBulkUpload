"""Plaintext run log, written alongside the JSON state file.

A simple append-only `.txt` mirror of the per-file progress and summary that the
CLI prints / the GUI shows. Each run starts with a timestamped header so several
runs stay readable in one file. Writes are flushed immediately (a crash still
leaves a usable log) and any I/O error is swallowed — logging must never break
an upload.
"""

import os
from datetime import datetime

DEFAULT_LOG_FILE = "komoot_upload_log.txt"


class RunLog:
    """Append timestamped lines to a plaintext log file.

    Pass an empty path to disable logging (every method becomes a no-op).
    """

    def __init__(self, path=DEFAULT_LOG_FILE):
        self.path = path
        self._fh = None
        if not path:
            return
        try:
            self._fh = open(path, "a", encoding="utf-8")
            self._fh.write("\n=== Run {} ===\n".format(
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            self._fh.flush()
        except OSError:
            self._fh = None

    @property
    def active(self):
        return self._fh is not None

    def log(self, line):
        if self._fh is None:
            return
        try:
            self._fh.write("{}  {}\n".format(
                datetime.now().strftime("%H:%M:%S"), line))
            self._fh.flush()
        except OSError:
            pass

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    # Allow `with RunLog(...) as log:` so the file always gets closed.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
