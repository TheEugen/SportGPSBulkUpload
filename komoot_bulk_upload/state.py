"""Resumable upload state, keyed by file content hash."""

import hashlib
import json
import os


def file_hash(path):
    """SHA-1 of a file's bytes (identifies a GPX regardless of its path)."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class UploadState:
    """JSON-backed record of which files have already been uploaded."""

    def __init__(self, path):
        self.path = path
        self.data = {"uploads": {}}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (ValueError, OSError):
                self.data = {"uploads": {}}
        self.data.setdefault("uploads", {})

    def is_done(self, file_hash_):
        rec = self.data["uploads"].get(file_hash_)
        return rec is not None and rec.get("status") in ("created", "duplicate")

    def get(self, file_hash_):
        return self.data["uploads"].get(file_hash_)

    def record(self, file_hash_, **fields):
        self.data["uploads"][file_hash_] = fields
        self.save()

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)
