"""Orbit's own settings: ~/.config/orbit/config.json (or $XDG_CONFIG_HOME/orbit; %APPDATA%\\Orbit on Windows)."""
import copy
import json
import os
import threading
from pathlib import Path

DEFAULTS = {
    "theme": "tui",
    "refreshSec": 3,
    "checkUpdates": True,     # ask GitHub (at most every 12 hours) whether a newer release exists
    "apps": {},               # per-app hub settings: {"<id>": {"enabled": true, ...}}
    "custom": [],             # apps added from the hub: [{"id","name","unit","scope","healthUrl","openUrl"}]
    "order": [],              # app ids in the order they appear in the side bar
    "themes": [],             # custom themes: [{"id": "custom-…", "label", "tokens": {name: "#rrggbb", "radius": 0-14}}]
}


def config_dir():
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Orbit"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "orbit"


class Config:
    def __init__(self, path=None):
        self.path = Path(path) if path else config_dir() / "config.json"
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        data = copy.deepcopy(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k in DEFAULTS and isinstance(v, type(DEFAULTS[k]))})
        except (OSError, ValueError):
            pass
        return data

    def get(self, key=None):
        with self._lock:
            return copy.deepcopy(self._data if key is None else self._data.get(key))

    def update(self, patch):
        with self._lock:
            self._data.update(copy.deepcopy(patch))
            self._save()
            return copy.deepcopy(self._data)

    def app(self, app_id):
        with self._lock:
            return copy.deepcopy(self._data["apps"].get(app_id, {}))

    def update_app(self, app_id, patch):
        with self._lock:
            cur = self._data["apps"].setdefault(app_id, {})
            cur.update(copy.deepcopy(patch))
            self._save()
            return copy.deepcopy(cur)

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)  # atomic: a crash can't leave half a file
        except OSError:
            pass
