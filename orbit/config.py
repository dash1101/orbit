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
    "github": {},             # Orbit's own GitHub sign-in: {"token", "login", "since"} (this file is readable by you only)
    "themes": [],             # custom themes: [{"id": "custom-…", "label", "tokens": {name: "#rrggbb", "radius": 0-14}}]
    "window": [],             # the window's last size, [width, height] (Linux's own window; browsers keep their own)
}


# Built-in apps that changed: Pocketfader is now YTM Starcast, and YTM Beacon is part of YTM Starcast.
RENAMED = {"pocketfader": "ytm-starcast"}     # old id -> new id (its address, token and on/off carry over)
RETIRED = ("ytm-beacon",)                     # folded into another app: its tile goes


def migrate(d):
    """Bring settings saved by an older Orbit up to date. Returns True when something changed."""
    changed = False
    apps, order = d["apps"], d["order"]
    for old, new in RENAMED.items():
        if old in apps:
            was = apps.pop(old)
            merged = dict(was) if isinstance(was, dict) else {}
            merged.update(apps.get(new) if isinstance(apps.get(new), dict) else {})    # the new id's own values win
            apps[new] = merged
            changed = True
        if old in order:
            i = order.index(old)
            order[:] = [x for x in order if x != old]
            if new not in order:
                order.insert(i, new)            # the tile keeps its place
            changed = True
    for gone in RETIRED:
        if apps.pop(gone, None) is not None:
            changed = True
        if gone in order:
            order[:] = [x for x in order if x != gone]
            changed = True
    return changed


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
        if migrate(self._data):
            with self._lock:
                self._save()

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
