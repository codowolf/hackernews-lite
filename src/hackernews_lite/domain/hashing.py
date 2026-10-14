"""Content hashes show whether a stage's output is still valid, so unchanged input is skipped."""

import hashlib


def content_hash(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x1f")  # separator, so ("ab", "c") and ("a", "bc") differ
    return digest.hexdigest()[:16]
