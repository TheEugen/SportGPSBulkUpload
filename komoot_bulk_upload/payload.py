"""Prepare an activity file for upload to komoot.

komoot's upload endpoint accepts the raw file body for GPX and TCX (the format
is selected by the `data_type` query param) — no conversion is needed once the
request omits the Content-Type header (see api.py). FIT is recognized but not
yet supported (see TASKS.md task 14).
"""

import os


class UnsupportedFormat(Exception):
    """Raised for a file komoot can't (yet) accept."""


def upload_payload(path):
    """Return (raw_bytes, data_type) for a file, or raise UnsupportedFormat."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".gpx", ".tcx"):
        with open(path, "rb") as f:
            return f.read(), ext.lstrip(".")
    if ext == ".fit":
        raise UnsupportedFormat(
            "FIT upload is not supported yet (see TASKS.md task 14). "
            "Use the GPX or TCX export instead.")
    raise UnsupportedFormat("Unsupported file type: {}".format(ext or "<none>"))
