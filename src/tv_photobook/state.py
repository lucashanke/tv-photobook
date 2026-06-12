"""Persistent record of which local photos were uploaded to the TV."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

STATE_VERSION = 1


class StateError(Exception):
    pass


@dataclass
class StateEntry:
    sha256: str
    content_id: str
    uploaded_at: str
    # Default keeps state files from before matte support loadable.
    matte: str = "none"


class StateStore:
    """Maps source file names to the TV content they produced.

    TV deletions are only ever issued for content_ids recorded here, so art
    from the Samsung store or other apps is never touched.
    """

    def __init__(self, path: Path, items: dict[str, StateEntry] | None = None) -> None:
        self.path = path
        self.items: dict[str, StateEntry] = items if items is not None else {}

    @classmethod
    def load(cls, path: Path) -> StateStore:
        if not path.exists():
            return cls(path)
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise StateError(f"state file {path} is unreadable or corrupt: {e}") from e
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            raise StateError(f"state file {path} has an unsupported format")
        try:
            items = {name: StateEntry(**entry) for name, entry in raw["items"].items()}
        except (KeyError, TypeError) as e:
            raise StateError(f"state file {path} has an unsupported format") from e
        return cls(path, items)

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "items": {name: asdict(entry) for name, entry in sorted(self.items.items())},
        }
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise
