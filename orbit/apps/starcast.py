"""YTM Starcast: a YouTube Music companion. A phone remote (volume, outputs, EQ and the YouTube Music tab), the music
as a Discord status, and Refine inside YouTube Music. A user service with a local JSON API on port 8777.

YTM Starcast is what Pocketfader and YTM Beacon became. Until a computer has moved over, this page also works with the
Pocketfader that still runs there: same address, same API, and its service is used for start/stop and the log.
"""
import os
import shutil
import time
from urllib.parse import urlsplit

from .. import net, systemd
from . import discord
from .base import App, BadRequest, field

DEFAULT_URL = "http://127.0.0.1:8777"
UNIT = "ytm-starcast.service"
LEGACY_UNIT = "pocketfader.service"     # Pocketfader, before the computer moved over to YTM Starcast

# YTM Starcast's parts, each with its own switch (`GET/POST /api/parts`): key -> (label, help)
PARTS = (
    ("remote", "YouTube Music remote", "Phones and the desktop window may control the YouTube Music tab."),
    ("discord", "Discord status", "Show what you play on YouTube Music as your Discord status."),
    ("refine", "Refine", "Improvements inside YouTube Music itself (its own options are in the page's ✦ panel)."),
    ("audio", "This computer's audio", "Volume, outputs and the equalizer in YTM Starcast's apps."),
)

HIDE_FOR = (30, 60, 180)


class Starcast(App):
    id = "ytm-starcast"
    icon = "ytm-starcast.svg"
    name = "YTM Starcast"
    blurb = "YouTube Music companion: a phone remote, your Discord status and a better YouTube Music"
    glyph = "S"
    color = "#38d9c4"
    repo = "dash1101/ytm-starcast"
    homepage = "https://github.com/dash1101/ytm-starcast"
    open_label = "open web app"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._found = None      # (until, unit name): which service runs it here (the hub forgets it after an install)
        self._beacon = None     # the last Discord status payload, so the buttons match what it is doing

    @property
    def units(self):
        """ytm-starcast.service, or Pocketfader's service while that is the one this computer has (before the move)."""
        hit = self._found
        if hit and hit[0] > time.time():
            return ((hit[1], True),)
        unit = UNIT
        if systemd.here() and systemd.show(self.ctx.runner, UNIT, True).get("state") == "missing" and \
                systemd.show(self.ctx.runner, LEGACY_UNIT, True).get("state") not in (None, "missing"):
            unit = LEGACY_UNIT
        self._found = (time.time() + 10, unit)
        return ((unit, True),)

    @property
    def base(self):
        url = (self.hub.get("url") or DEFAULT_URL).rstrip("/")
        return url if urlsplit(url).scheme in ("http", "https") else DEFAULT_URL

    def _headers(self):
        tok = self.hub.get("token") or ""
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def _get(self, path, timeout=1.5):
        return net.get_json(self.base + path, timeout=timeout, headers=self._headers())

    def _post(self, path, body, timeout=3):
        return net.post_json(self.base + path, body, timeout=timeout, headers=self._headers())

    def hub_fields(self):
        h = self.hub
        return [
            field("url", "Address", "text", h.get("url") or DEFAULT_URL, scope="hub",
                  help="Where YTM Starcast answers on this computer."),
            field("token", "Access token", "secret", h.get("token") or "", scope="hub",
                  help="Only needed when YTM Starcast runs with --lan, --bind or --token."),
        ]

    def _discord(self):
        """YTM Starcast's Discord status payload, or None (not running, or too old to have one)."""
        try:
            b = self._get("/api/beacon")
        except net.HttpError:
            return None
        return b if isinstance(b, dict) else None

    # ------------------------------------------------------------------------------------
    def status(self):
        units = self.unit_states()
        unit = units[0] if units else {}
        facts = []
        s = {"units": units, "facts": facts}
        err = ""
        try:
            info = self._get("/api/info")
        except net.HttpError as e:
            info = None
            err = str(e)
        if not info:
            self._beacon = None
            if unit.get("state") in ("stopped", "failed"):
                s.update(state="down" if unit["state"] == "failed" else "off",
                         headline="Not running" if unit["state"] == "stopped" else "The service failed: see the log")
            else:
                s.update(state="down", headline="Can't reach YTM Starcast at " + self.base, detail=err)
            return s
        s["version"] = info.get("version", "")
        self._update_info(s)
        if info.get("auth") and not self.hub.get("token"):
            s.update(state="warn", headline="Running, but it needs an access token (settings → connection)")
            return s
        try:
            st = self._get("/api/state")
        except net.HttpError as e:
            s.update(state="warn", headline="Running, but /api/state failed: " + str(e))
            return s
        vol, media = st.get("volume") or {}, st.get("media") or {}
        caps, parts = st.get("capabilities") or {}, st.get("parts") or {}
        if parts.get("audio") is False:
            facts.append(["computer audio", "switched off in YTM Starcast", "muted"])
        elif vol.get("available"):
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
            s["line"] = ("▶ " if playing else "❚❚ ") + title + (f" — {artist}" if artist else "")
        b = self._beacon = self._discord()
        if caps.get("ytm_bridge"):
            if ytm.get("connected"):
                facts.append(["browser extension", "connected"])
            else:
                seen = ((b or {}).get("extension") or {}).get("lastSeenAgoSec")
                facts.append(["browser extension", "not connected" if seen is None else
                              "last seen " + discord._ago(seen), "muted"])
        self._discord_facts(facts, b, parts)
        if parts.get("audio") is not False:
            facts.append(["eq", (caps.get("engine_label") or "on") if caps.get("eq") else "not available"])
        sleep = (st.get("sleep") or {}).get("remainingSec") or 0
        if sleep:
            facts.append(["sleep timer", f"{int(sleep) // 60} min left"])
        if b and b.get("reachable") is not False:
            more = discord.details(b)
            if more:
                s["more"] = more
        device = vol.get("device") if parts.get("audio") is not False and vol.get("available") else ""
        s.update(state="ok", headline="Running" + (f" · {device}" if device else ""))
        if unit.get("state") == "missing":
            facts.append(["service", "not installed as a user service", "muted"])
        elif unit.get("unit") == LEGACY_UNIT:
            facts.append(["service", "still Pocketfader's: update to move it over to YTM Starcast", "muted"])
        return s

    def _update_info(self, s):
        """Whether a newer YTM Starcast exists: Orbit's own release check (it also knows the way from Pocketfader), else
        what the app itself says when Orbit's check is off."""
        rel = self.release_info(s.get("version", ""))
        if rel:
            s["update"] = rel
            return
        try:
            up = self._get("/api/update")
            s["update"] = {"latest": up.get("latest"), "url": f"https://github.com/{self.repo}/releases",
                           "available": bool(up.get("available"))}
        except net.HttpError:
            pass

    @staticmethod
    def _discord_facts(facts, b, parts):
        """Discord status lines. Discord's problems never change the app's own state (the line is coloured instead)."""
        if parts.get("discord") is False or not b or b.get("reachable") is False:
            facts.append(["discord status", "off", "muted"])
            if b and (b.get("beaconApp") or {}).get("running"):
                facts.append(["old ytm beacon", "still running: move over to YTM Starcast", "warn"])
            return
        code, tone, text = discord.describe(b)
        facts.append(["discord status", text, {"warn": "warn", "off": "muted"}.get(tone)])
        disc = b.get("discord") or {}
        if disc.get("state") and code != "beacon-app":
            facts.append(["discord", str(disc["state"]), None if disc["state"] == "connected" else "warn"])

    # ------------------------------------------------------------------------------------
    def settings(self):
        groups = []
        try:
            prefs = self._get("/api/prefs")
        except net.HttpError:
            prefs = None
        if prefs is None:
            groups.append({"title": "ytm starcast", "fields": [
                field("_note", "", "note", "Start YTM Starcast to change its settings.")]})
            groups.append({"title": "connection", "fields": self.hub_fields()})
            return groups
        fields = [field("pauseOthers", "Pause other players", "bool", bool(prefs.get("pauseOthers")),
                        help="When YouTube Music starts playing, pause every other player on the computer.")]
        try:
            parts = (self._get("/api/parts") or {}).get("parts") or {}
        except (net.HttpError, AttributeError):
            parts = {}                                  # an older version: no switches
        for key, label, help_ in PARTS:
            if isinstance(parts.get(key), bool):
                fields.append(field("parts." + key, label, "bool", parts[key], help=help_))
        fields.append(field("_note", "", "note", "Everything else (outputs, EQ, YouTube Music) is in YTM Starcast "
                                                 "itself: use “open web app”."))
        groups.append({"title": "ytm starcast", "fields": fields})
        b = self._discord()
        if b and b.get("reachable") is not False and isinstance(b.get("settings"), dict):
            groups += self._discord_settings(b, master="discord" not in parts)
        groups.append({"title": "connection", "fields": self.hub_fields()})
        return groups

    @staticmethod
    def _discord_settings(b, master):
        """The Discord card's settings: only those the payload has (so Orbit follows what YTM Starcast accepts)."""
        c = b["settings"]
        card = []
        if master:   # with YTM Starcast's parts the "Discord status" switch above is this one
            card.append(field("discord.visible", "Show on Discord", "bool", bool(b.get("visible")),
                              help="Master switch. Turning it on also ends a timed hide."))
        for key, label, type_, extra in (
                ("privateMode", "Private mode", "bool",
                 {"help": "Only “Listening to music”: no song, artist, art or button."}),
                ("statusDisplay", "Name in member list", "choice",
                 {"options": [["name", "app name"], ["state", "artist"], ["details", "song title"]]}),
                ("showAlbumArt", "Album art", "bool", {}),
                ("showProgress", "Progress bar", "bool", {}),
                ("showStateIcon", "Play/pause badge", "bool", {}),
                ("showButtons", "“Listen” button", "bool", {})):
            if key in c:
                value = bool(c[key]) if type_ == "bool" else c[key]
                card.append(field("discord." + key, label, type_, value, **extra))
        paused = []
        if "showPaused" in c:
            paused.append(field("discord.showPaused", "Keep showing while paused", "bool", bool(c["showPaused"])))
        if "pauseTimeoutMinutes" in c:
            paused.append(field("discord.pauseTimeoutMinutes", "Hide after (minutes, 0 = never)", "int",
                                int(c.get("pauseTimeoutMinutes") or 0), min=0, max=1440))
        groups = [g for g in ({"title": "discord card", "fields": card}, {"title": "when paused", "fields": paused})
                  if g["fields"]]
        if "hideMatching" in c:
            groups.append({"title": "never show", "fields": [
                field("discord.hideMatching", "Songs containing", "words", list(c.get("hideMatching") or []),
                      help="Comma-separated. A song whose title, artist or album contains one of these is never shown."),
            ]})
        return groups

    def apply_app_settings(self, patch):
        if self.ctx.dry_run:
            return
        prefs, parts, disc, visible = {}, {}, {}, None
        for k, v in patch.items():
            if k == "discord.visible":
                visible = v
            elif k.startswith("discord."):
                disc[k[len("discord."):]] = v
            elif k.startswith("parts."):
                parts[k[len("parts."):]] = v
            else:
                prefs[k] = v
        try:
            if parts:
                self._post("/api/parts", parts)
            if visible is not None:
                self._post("/api/beacon/visibility", {"action": "show" if visible else "hide"})
            if disc:
                self._post("/api/beacon/settings", disc)
            if prefs:
                self._post("/api/prefs", prefs)
        except net.HttpError as e:
            raise BadRequest("YTM Starcast refused: " + str(e))

    # ------------------------------------------------------------------------------------
    def actions(self):
        acts = [
            {"id": "open", "label": "open web app", "group": "open", "primary": True},
            {"id": "gui", "label": "desktop window", "group": "open"},
            {"id": "releases", "label": "releases", "group": "open"},
        ]
        b = self._beacon
        if b and b.get("reachable") is not False:
            old = b.get("beaconApp") or {}
            if old.get("canMigrate"):
                acts.append({"id": "migrate-beacon", "label": "move over from YTM Beacon", "group": "discord",
                             "primary": True,
                             "confirm": "Move your Discord status over from the old YTM Beacon app? YTM Starcast "
                                        "removes YTM Beacon (its settings are kept for YTM Starcast) and shows your "
                                        "status itself."})
            elif b.get("visible") is False:          # switched off, or hidden for a while
                acts.append({"id": "show", "label": "show now", "group": "discord", "primary": True})
            else:
                acts += [{"id": f"hide:{m}", "label": f"hide {m} min" if m < 60 else f"hide {m // 60} h",
                          "group": "discord"} for m in HIDE_FOR]
        elif self.beacon_binary():
            acts.append({"id": "remove-beacon", "label": "remove YTM Beacon", "group": "maintenance", "term": True,
                         "changes": True,
                         "confirm": "Remove the old YTM Beacon app? YTM Starcast shows your Discord status instead. "
                                    "Its settings are kept, so YTM Starcast can take them over."})
        return acts + self.unit_actions()

    def run_action(self, action, args):
        dry = " (dry run)" if self.ctx.dry_run else ""
        if action == "open":
            return self.open_url(self.base + "/")
        if action == "gui":
            for exe in self.gui_paths():
                if exe and os.path.exists(exe):
                    if not self.ctx.runner.launch([exe]):
                        raise BadRequest("couldn't open YTM Starcast's window")
                    return {"ok": True, "message": "opened YTM Starcast's window"}
            raise BadRequest("YTM Starcast's window isn't installed")
        if action == "releases":
            return self.open_url(self.homepage + "/releases")
        if action == "show" or (action.startswith("hide:") and action[5:] in {str(m) for m in HIDE_FOR}):
            body = {"action": "show"} if action == "show" else {"action": "hide", "minutes": int(action[5:])}
            if not self.ctx.dry_run:
                try:
                    self._post("/api/beacon/visibility", body)
                except net.HttpError as e:
                    raise BadRequest("YTM Starcast refused: " + str(e))
            return {"ok": True, "message": ("shown on Discord" if action == "show" else
                                            f"hidden for {body['minutes']} min") + dry}
        if action == "migrate-beacon":
            if self.ctx.dry_run:
                return {"ok": True, "message": "dry run: would move over from YTM Beacon"}
            try:
                r = self._post("/api/beacon/migrate", {}, timeout=60) or {}
            except net.HttpError as e:
                # YTM Starcast explains each refusal in its reply (already moving, Beacon not where its installer
                # puts it, its settings unreadable, its uninstaller failed, still running): show its words
                raise BadRequest(e.text or {
                    "nothing_to_move": "There's nothing to move over: YTM Beacon isn't installed here.",
                    "uninstall_failed": "YTM Beacon's uninstaller stopped with an error. Remove it with "
                                        "`ytm-beacon uninstall`, then try again.",
                }.get(str(e), "YTM Starcast: " + str(e)))
            self._beacon = None
            net.forget_ports()
            return {"ok": True, "message": r.get("message") or "moved over from YTM Beacon"}
        if action.startswith("svc:"):
            return self.run_unit_action(action)
        raise BadRequest("unknown action")

    def term_command(self, action, args):
        if action != "remove-beacon":
            return None
        exe = self.beacon_binary()
        if not exe:
            raise BadRequest("YTM Beacon isn't installed")
        return {"argv": [exe, "uninstall"], "title": "YTM Beacon · remove"}

    # ---- where things are ------------------------------------------------------------------
    @staticmethod
    def gui_paths():
        """YTM Starcast's desktop window, wherever it was installed: YTM Starcast's places first, then
        Pocketfader's."""
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        out = []
        for cmd, folder, win in (("ytm-starcast-gui", "ytm-starcast", "YTM Starcast"),
                                 ("pocketfader-gui", "pocketfader", "Pocketfader")):
            out += [shutil.which(cmd),
                    os.path.expanduser(f"~/.local/share/{folder}/venv/bin/{cmd}"),
                    os.path.join(local, "Programs", win, f"{cmd}.exe"),
                    os.path.expanduser(f"~/Library/Application Support/{win}/venv/bin/{cmd}")]
        return out

    @staticmethod
    def beacon_binary():
        """The old YTM Beacon program, when it is still on this computer."""
        exe = shutil.which("ytm-beacon")
        if exe:
            return exe
        if os.name == "nt":
            p = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
                             "ytm-beacon", "ytm-beacon.exe")
        else:
            p = os.path.expanduser("~/.local/bin/ytm-beacon")
        return p if os.path.exists(p) else None
