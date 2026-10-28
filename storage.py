"""Small JSON file store for bot settings that must survive restarts.

Used for 24/7 radio, song-request settings and call safety (blocklists,
reports). Files live in DATA_DIR (default: ./data next to the bot), which
panel hosts like bot-hosting.net keep between restarts. Writes go to a temp
file first and are then renamed, so a crash mid-write can't corrupt the data.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent / "data"))
_lock = threading.Lock()


class JsonStore:
    def __init__(self, name: str) -> None:
        self.path = DATA_DIR / f"{name}.json"
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            print(f"[Storage] Could not read {self.path.name}, starting empty: {error}", flush=True)
            return {}

    def _save(self) -> None:
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            os.replace(temp, self.path)

    def get(self, key: str | int, default: Any = None) -> Any:
        return self._data.get(str(key), default)

    def set(self, key: str | int, value: Any) -> None:
        self._data[str(key)] = value
        self._save()

    def delete(self, key: str | int) -> None:
        if self._data.pop(str(key), None) is not None:
            self._save()

    def items(self):
        return list(self._data.items())
