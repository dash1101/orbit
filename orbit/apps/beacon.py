"""YTM Beacon: YouTube Music as your Discord status (a user service, loopback API on 41713-41717)."""
import os
import shutil
import time

from .. import net
from .base import App, BadRequest, field

DEFAULT_CLIENT_ID = "1567329407901634670"   # YTM Beacon's own Discord application (public)
PORTS = (41713, 41714, 41715, 41716, 41717)

HEADLINES = {
    "off": ("off", "Hidden from Discord (switched off)"),
    "snoozed": ("off", "Hidden for a while — comes back by itself"),
    "discord-down": ("warn", "Can't reach Discord — is it open?"),
    "extension-missing": ("warn", "The browser extension isn't set up yet"),
    "extension-quiet": ("warn", "The browser extension went quiet — keep a YouTube Music tab open"),
    "idle": ("ok", "Ready · nothing playing"),
    "filtered": ("ok", "Song hidden by your word filter"),
    "paused-hidden": ("ok", "Paused · hidden while paused"),
    "paused-timeout": ("ok", "Paused · hidden after the pause timeout"),
    "showing": ("ok", "Showing on Discord"),
    "waiting": ("ok", "Sending to Discord…"),
}


def headline(s):
    """Same order as Beacon's own dashboard. `s` is its /v1/status."""
    cfg = s.get("config") or {}
    visible = s.get("visible")
    if visible is None:
        visible = bool(cfg.get("enabled", True))
    if visible is False:
        return "snoozed" if (s.get("snoozeSec") or 0) > 0 else "off"
    if (s.get("discord") or {}).get("state") != "connected":
        return "discord-down"
    ext = s.get("extension") or {}
    seen = ext.get("lastSeenAgoSec")
    if seen is None:
        return "extension-quiet" if ext.get("everConnected") else "extension-missing"
    if seen > (ext.get("quietAfterSec") or 90):
        return "extension-quiet"
    if not s.get("nowPlaying"):
        return "idle"
    hidden = s.get("hidden")
    if hidden in ("filtered", "paused-timeout"):
        return hidden
    if hidden == "paused":
        return "paused-hidden"
    return "showing" if s.get("presenceActive") else "waiting"


def _ago(sec):
    if sec is None:
        return "never"
    sec = int(sec)
    return f"{sec} s ago" if sec < 90 else f"{sec // 60} min ago" if sec < 5400 else f"{sec // 3600} h ago"


class Beacon(App):
    id = "ytm-beacon"
    icon = "ytm-beacon.png"
    name = "YTM Beacon"
    blurb = "YouTube Music as your Discord status"
    glyph = "YB"
    color = "#7c8cf8"
    repo = "dash1101/ytm-beacon"
    homepage = "https://github.com/dash1101/ytm-beacon"
    units = (("ytm-beacon.service", True),)

    def __init__(self, ctx, ports=PORTS):
        super().__init__(ctx)
        self.ports = ports
        self._port = None
        self._last = None         # the last /v1/status, so the buttons can match what Beacon is doing

    def _url(self, port, path):
        return f"http://127.0.0.1:{port}{path}"

    def find_port(self):
        # Not installed and not found a moment ago: don't scan five ports on every refresh.
        if self._port is None and getattr(self, "_missed", 0) > time.time() and not self.binary():
            return None
        order = ([self._port] if self._port else []) + [p for p in self.ports if p != self._port]
        for p in order:
            if not net.local_listening("127.0.0.1", p):
                continue
            try:
                if (net.get_json(self._url(p, "/v1/ping"), timeout=0.6) or {}).get("app") == "ytm-beacon":
                    self._port = p
                    return p
            except net.HttpError:
                continue
        self._port = None
        self._missed = time.time() + 30
        return None

    def _call(self, method, path, body=None, timeout=2.0, raw=False):
        port = self.find_port()
        if not port:
            raise BadRequest("YTM Beacon isn't running")
        try:
            return net.request(method, self._url(port, path), body=body, timeout=timeout, raw=raw)
        except net.HttpError as e:
            raise BadRequest("YTM Beacon: " + str(e))

    def binary(self):
        exe = shutil.which("ytm-beacon")
        if exe:
            return exe
        if os.name == "nt":
            p = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
                             "ytm-beacon", "ytm-beacon.exe")
        else:
            p = os.path.expanduser("~/.local/bin/ytm-beacon")
        return p if os.path.exists(p) else None

    # ------------------------------------------------------------------------------------
    def status(self):
        units = self.unit_states()
        unit = units[0] if units else {}
        s = {"units": units, "facts": []}
        try:
            raw = self._call("GET", "/v1/status")
        except BadRequest:
            raw = None
        self._last = raw
        if not raw:
            installed = bool(self.binary())
            s.update(state="off" if installed and unit.get("state") == "stopped" else "down",
                     headline="Not running" if installed else "Not installed")
            return s
        code = headline(raw)
        tone, text = HEADLINES.get(code, ("unknown", code))
        if code == "snoozed":
            text = f"Hidden for {max(1, int(raw.get('snoozeSec') or 0) // 60)} more min"
        disc, ext, now = raw.get("discord") or {}, raw.get("extension") or {}, raw.get("nowPlaying") or {}
        f = s["facts"]
        f.append(["discord", disc.get("state", "?"), None if disc.get("state") == "connected" else "warn"])
        f.append(["extension", "last seen " + _ago(ext.get("lastSeenAgoSec")),
                  None if ext.get("lastSeenAgoSec") is not None else "warn"])
        if now.get("title"):
            f.append(["paused" if now.get("state") == "paused" else "playing",
                      now.get("title") + (f" — {now['artist']}" if now.get("artist") else "")])
        f.append(["on discord", ("showing now" if raw.get("presenceActive") else "visible, nothing to show")
                  if raw.get("visible") else "hidden", None if raw.get("visible") else "muted"])
        f.append(["port", str(raw.get("port", self._port))])
        s["more"] = self.details(raw)
        s.update(state=tone, headline=text, version=raw.get("version", ""), code=code,
                 snoozeSec=int(raw.get("snoozeSec") or 0), visible=bool(raw.get("visible")))
        rel = self.release_info(raw.get("version", ""))
        if rel:
            s["update"] = rel
            raw["_update"] = rel
        return s

    def details(self, raw):
        """Everything else Beacon reports, for the "details" box."""
        disc, ext, now, cfg = (raw.get(k) or {} for k in ("discord", "extension", "nowPlaying", "config"))
        out = []
        if disc.get("user"):
            out.append(["discord user", disc["user"]])
        ep = disc.get("endpoint") or ""
        if ep:
            client = next((n for k, n in (("vesktop", "Vesktop"), ("vencord", "Vencord"), ("webcord", "WebCord"),
                                          ("flatpak/com.discordapp", "Discord (Flatpak)"), ("snap.discord", "Discord (Snap)"))
                           if k in ep.lower()), "Discord")
            out.append(["discord app", client])
            out.append(["connection", ep])
        cid = (cfg.get("clientId") or "").strip()
        out.append(["application id", (cid + "  (your own)") if cid and cid != DEFAULT_CLIENT_ID else
                    (cid or DEFAULT_CLIENT_ID) + "  (YTM Beacon's)"])
        if now.get("title"):
            for k in ("album",):
                if now.get(k):
                    out.append([k, now[k]])
        out.append(["extension", ("connected before" if ext.get("everConnected") else "never connected")
                    + (f" · build {raw['extensionBuild']}" if raw.get("extensionBuild") else "")])
        if ext.get("sources"):
            out.append(["reporting from", ", ".join(_source(x) for x in ext["sources"])])
        if raw.get("snoozeSec"):
            out.append(["hidden for", f"{int(raw['snoozeSec']) // 60} min more"])
        out.append(["version", f"{raw.get('version', '?')} ({raw.get('commit', '?')}) · {raw.get('os', '')}/{raw.get('arch', '')}"])
        out.append(["running for", _dur(raw.get("uptimeSec"))])
        out.append(["process", str(raw.get("pid", "?"))])
        if raw.get("configPath"):
            out.append(["settings file", raw["configPath"]])
        if raw.get("logPath"):
            out.append(["log file", raw["logPath"]])
        for b, d in (raw.get("extensionDirs") or {}).items():
            out.append([f"{b} extension", d])
        return out

    def settings(self):
        try:
            st = self._call("GET", "/v1/status")
        except BadRequest:
            return [{"title": "ytm beacon", "fields": [field("_note", "", "note", "Start YTM Beacon to change its settings.")]}]
        c = st.get("config") or {}
        return [
            {"title": "discord card", "fields": [
                field("enabled", "Show on Discord", "bool", bool(st.get("visible", c.get("enabled"))),
                      help="Master switch. Turning it on also ends a timed hide."),
                field("privateMode", "Private mode", "bool", bool(c.get("privateMode")),
                      help="Only “Listening to music”: no song, artist, art or button."),
                field("statusDisplay", "Name in member list", "choice", c.get("statusDisplay", "name"),
                      options=[["name", "app name"], ["state", "artist"], ["details", "song title"]]),
                field("showAlbumArt", "Album art", "bool", bool(c.get("showAlbumArt"))),
                field("showProgress", "Progress bar", "bool", bool(c.get("showProgress"))),
                field("showStateIcon", "Play/pause badge", "bool", bool(c.get("showStateIcon"))),
                field("showButtons", "“Listen” button", "bool", bool(c.get("showButtons"))),
            ]},
            {"title": "when paused", "fields": [
                field("showPaused", "Keep showing while paused", "bool", bool(c.get("showPaused"))),
                field("pauseTimeoutMinutes", "Hide after (minutes, 0 = never)", "int",
                      int(c.get("pauseTimeoutMinutes") or 0), min=0, max=1440),
            ]},
            {"title": "never show", "fields": [
                field("hideMatching", "Songs containing", "words", list(c.get("hideMatching") or []),
                      help="Comma-separated. A song whose title, artist or album contains one of these is never shown."),
            ]},
            {"title": "discord application", "fields": [
                field("clientId", "Application ID", "text", c.get("clientId") or "", pattern=r"[0-9]{15,25}",
                      patternHelp="a Discord application ID is 15–25 digits", maxLen=25,
                      help="The Discord application your status is shown as (its name is the “Listening to …” "
                           f"line). Empty uses YTM Beacon's own ({DEFAULT_CLIENT_ID}). Create one at "
                           "discord.com/developers/applications. Beacon reconnects to Discord when this changes."),
            ]},
        ]

    def apply_app_settings(self, patch):
        if self.ctx.dry_run:
            return
        patch = dict(patch)
        if "enabled" in patch:
            self._call("POST", "/v1/visibility", {"action": "show" if patch.pop("enabled") else "hide"})
        if patch:
            self._call("POST", "/v1/config", patch)

    def actions(self):
        """The buttons follow what Beacon is doing: "show now" when it's hidden, the hide times when it's showing,
        "update" when a newer release is out (or it's unknown)."""
        raw = self._last
        running = raw is not None
        acts = []
        if running:
            acts.append({"id": "open", "label": "dashboard", "group": "open", "primary": True})
            if raw.get("visible") is False:          # switched off, or hidden for a while
                acts.append({"id": "show", "label": "show now", "group": "discord", "primary": True})
            else:
                acts += [{"id": "hide:30", "label": "hide 30 min", "group": "discord"},
                         {"id": "hide:60", "label": "hide 1 h", "group": "discord"},
                         {"id": "hide:180", "label": "hide 3 h", "group": "discord"}]
        if self.binary():
            rel = (raw or {}).get("_update") or {}
            acts.append({"id": "doctor", "label": "doctor", "group": "maintenance", "term": True})
            acts.append({"id": "update-check", "label": "check for update", "group": "maintenance", "term": True})
            if rel.get("available") or not rel.get("latest"):
                acts.append({"id": "update", "label": f"update to {rel['latest']}" if rel.get("available") else "update",
                             "group": "maintenance", "term": True, "changes": True, "primary": bool(rel.get("available")),
                             "confirm": "Download and install the newest YTM Beacon?"})
            acts.append({"id": "extension", "label": "extension folders", "group": "maintenance", "term": True,
                         "changes": True})
        return acts + self.unit_actions()

    def run_action(self, action, args):
        dry = " (dry run)" if self.ctx.dry_run else ""
        if action == "open":
            port = self.find_port()
            if not port:
                raise BadRequest("YTM Beacon isn't running")
            return self.open_url(self._url(port, "/"))
        if action.startswith("hide:") or action == "show":
            body = {"action": "show"} if action == "show" else {"action": "hide", "minutes": int(action.split(":")[1])}
            if not self.ctx.dry_run:
                self._call("POST", "/v1/visibility", body)
            return {"ok": True, "message": ("shown on Discord" if action == "show" else
                                            f"hidden for {body['minutes']} min") + dry}
        if action.startswith("svc:"):
            return self.run_unit_action(action)
        raise BadRequest("unknown action")

    def term_command(self, action, args):
        argv = {"doctor": ["doctor"], "update-check": ["update", "--check"], "update": ["update"],
                "extension": ["extension"]}.get(action)
        if argv is None:
            return None
        exe = self.binary()
        if not exe:
            raise BadRequest("ytm-beacon isn't installed")
        label = next((a["label"] for a in self.actions() if a["id"] == action), action)
        return {"argv": [exe] + argv, "title": f"YTM Beacon · {label}"}

    def logs(self):
        try:
            return self._call("GET", "/v1/logs", raw=True)
        except BadRequest:
            return super().logs()


def _source(x):
    """One browser tab Beacon hears from: "Brave · playing · 8 s ago"."""
    if not isinstance(x, dict):
        return str(x)
    who = str(x.get("agent") or x.get("browser") or x.get("name") or "a browser")
    bits = [who]
    if x.get("state"):
        bits.append(str(x["state"]))
    if isinstance(x.get("ageSec"), (int, float)):
        bits.append(_ago(x["ageSec"]))
    return " · ".join(bits)


def _dur(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "?"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    return (f"{d} d " if d else "") + (f"{h} h " if h or d else "") + f"{m} min"
