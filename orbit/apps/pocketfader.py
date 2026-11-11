"""Pocketfader: phone remote for volume, outputs, EQ and YouTube Music (a user service with a local JSON API)."""
from urllib.parse import urlsplit

from .. import net
from .base import App, BadRequest, field

DEFAULT_URL = "http://127.0.0.1:8777"


class Pocketfader(App):
    id = "pocketfader"
    icon = "pocketfader.svg"
    name = "Pocketfader"
    blurb = "Phone remote for volume, outputs, EQ and YouTube Music"
    glyph = "PF"
    color = "#e8698f"
    repo = "dash1101/pocketfader"
    homepage = "https://github.com/dash1101/pocketfader"
    units = (("pocketfader.service", True),)
    open_label = "open web app"

    @property
    def base(self):
        url = (self.hub.get("url") or DEFAULT_URL).rstrip("/")
        return url if urlsplit(url).scheme in ("http", "https") else DEFAULT_URL

    def _headers(self):
        tok = self.hub.get("token") or ""
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def _get(self, path, timeout=1.5):
        return net.get_json(self.base + path, timeout=timeout, headers=self._headers())

    def hub_fields(self):
        h = self.hub
        return [
            field("url", "Address", "text", h.get("url") or DEFAULT_URL, scope="hub",
                  help="Where Pocketfader answers on this computer."),
            field("token", "Access token", "secret", h.get("token") or "", scope="hub",
                  help="Only needed when Pocketfader runs with --lan, --bind or --token."),
        ]

    # ------------------------------------------------------------------------------------
    def status(self):
        units = self.unit_states()
        unit = units[0] if units else {}
        facts = []
        s = {"units": units, "facts": facts}
        try:
            info = self._get("/api/info")
        except net.HttpError as e:
            info = None
            err = str(e)
        if not info:
            if unit.get("state") in ("stopped", "failed"):
                s.update(state="down" if unit["state"] == "failed" else "off",
                         headline="Not running" if unit["state"] == "stopped" else "The service failed — see the log")
            else:
                s.update(state="down", headline="Can't reach Pocketfader at " + self.base, detail=err)
            return s
        s["version"] = info.get("version", "")
        if info.get("auth") and not self.hub.get("token"):
            s.update(state="warn", headline="Running, but it needs an access token (settings → connection)")
            return s
        try:
            st = self._get("/api/state")
        except net.HttpError as e:
            s.update(state="warn", headline="Running, but /api/state failed: " + str(e))
            return s
        vol, media, caps = st.get("volume") or {}, st.get("media") or {}, st.get("capabilities") or {}
        if vol.get("available"):
            facts.append(["output", vol.get("device") or "—"])
            facts.append(["volume", ("muted · " if vol.get("muted") else "") + f"{vol.get('percent', '?')}%"])
        else:
            facts.append(["output", "no output device", "warn"])
        try:
            ytm = self._get("/api/ytm/state")
        except net.HttpError:
            ytm = {}
        now = (ytm.get("now") or {}) if ytm.get("connected") else {}
        title = now.get("title") or media.get("title")
        if title:
            artist = now.get("artist") or media.get("artist") or ""
            playing = now.get("playing") if now else media.get("playing")
            facts.append(["playing" if playing else "paused", f"{title}" + (f" — {artist}" if artist else "")])
        if caps.get("ytm_bridge"):
            facts.append(["ytm extension", "connected" if ytm.get("connected") else "not connected",
                          None if ytm.get("connected") else "muted"])
        facts.append(["eq", (caps.get("engine_label") or "on") if caps.get("eq") else "not available"])
        sleep = (st.get("sleep") or {}).get("remainingSec") or 0
        if sleep:
            facts.append(["sleep timer", f"{int(sleep) // 60} min left"])
        try:
            up = self._get("/api/update")
            s["update"] = {"latest": up.get("latest"), "url": f"https://github.com/{self.repo}/releases",
                           "available": bool(up.get("available"))}
        except net.HttpError:
            pass
        s.update(state="ok", headline=f"Running · {vol.get('device') or 'no output'}")
        if unit.get("state") == "missing":
            facts.append(["service", "not installed as a user service", "muted"])
        return s

    def settings(self):
        groups = []
        try:
            prefs = self._get("/api/prefs")
            groups.append({"title": "pocketfader", "fields": [
                field("pauseOthers", "Pause other players", "bool", bool(prefs.get("pauseOthers")),
                      help="When YouTube Music starts playing, pause every other player on the computer."),
                field("_note", "", "note", "Everything else (outputs, EQ, YouTube Music) is in Pocketfader itself: "
                      "use “open web app”."),
            ]})
        except net.HttpError:
            groups.append({"title": "pocketfader", "fields": [
                field("_note", "", "note", "Start Pocketfader to change its settings.")]})
        groups.append({"title": "connection", "fields": self.hub_fields()})
        return groups

    def apply_app_settings(self, patch):
        if self.ctx.dry_run:
            return
        try:
            net.post_json(self.base + "/api/prefs", patch, timeout=3, headers=self._headers())
        except net.HttpError as e:
            raise BadRequest("Pocketfader refused: " + str(e))

    def actions(self):
        return [
            {"id": "open", "label": "open web app", "group": "open", "primary": True},
            {"id": "gui", "label": "desktop window", "group": "open"},
            {"id": "releases", "label": "releases", "group": "open"},
        ] + self.unit_actions()

    def run_action(self, action, args):
        if action == "open":
            return self.open_url(self.base + "/")
        if action == "gui":
            import os
            import shutil
            local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
            for exe in (shutil.which("pocketfader-gui"),
                        os.path.expanduser("~/.local/share/pocketfader/venv/bin/pocketfader-gui"),
                        os.path.join(local, "Programs", "Pocketfader", "pocketfader-gui.exe"),
                        os.path.expanduser("~/Library/Application Support/Pocketfader/venv/bin/pocketfader-gui")):
                if exe and os.path.exists(exe):
                    if not self.ctx.runner.launch([exe]):
                        raise BadRequest("couldn't open Pocketfader's window")
                    return {"ok": True, "message": "opened Pocketfader's window"}
            raise BadRequest("Pocketfader's window isn't installed")
        if action == "releases":
            return self.open_url(self.homepage + "/releases")
        if action.startswith("svc:"):
            return self.run_unit_action(action)
        raise BadRequest("unknown action")
