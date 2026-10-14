"""Finding an API key without making you reload it by hand every time.

Order: the environment variable if it is set, then the project's `.env` file (never checked in).
The value is only ever returned to the caller. It is never logged, printed or copied.
"""

import os
from pathlib import Path


def read_secret(name: str, env_file: Path | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if env_file is None:
        return None
    path = env_file.expanduser()
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, raw = line.partition("=")
        if separator and key.strip().removeprefix("export ").strip() == name:
            return raw.strip().strip("\"'") or None
    return None
