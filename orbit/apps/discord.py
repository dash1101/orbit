"""Discord status: what YTM Starcast (or, before the move, Pocketfader relaying YTM Beacon) reports about the music
shown as the Discord status, turned into the words Orbit shows. Pure helpers, no network.

The payload is YTM Starcast's `GET /api/beacon` (the same shape Pocketfader alpha.15 served). Older payloads (YTM
Beacon's own `/v1/status`) work too: every field is optional.
"""

DEFAULT_CLIENT_ID = "1567329407901634670"   # the Discord application the status shows as by default (public)

# headline code -> (tone, text). The tone is only used for the Discord line: it never changes the app's own state.
HEADLINES = {
    "off": ("off", "Off: not shown on Discord"),
    "snoozed": ("off", "Hidden for a while: comes back by itself"),
    "discord-down": ("warn", "Can't reach Discord: is it open?"),
    "no-client-id": ("warn", "No Discord application ID set"),
    "extension-missing": ("warn", "The browser extension isn't set up yet"),
    "extension-quiet": ("warn", "The browser extension went quiet: keep a YouTube Music tab open"),
    "idle": ("ok", "Ready · nothing playing"),
    "filtered": ("ok", "Song hidden by your word filter"),
    "paused-hidden": ("ok", "Paused · hidden while paused"),
    "paused-timeout": ("ok", "Paused · hidden after the pause timeout"),
    "showing": ("ok", "Showing on Discord"),
    "waiting": ("ok", "Sending to Discord…"),
    "ad": ("ok", "An advert is playing: hidden until the music is back"),
    "unreachable": ("off", "Off"),
    "unsupported": ("warn", "YTM Beacon is too old: update it"),
    "beacon-app": ("warn", "The old YTM Beacon app is still running: move over to YTM Starcast"),
}


def headline(s):
    """The payload's own headline when it has one, else worked out in the same order as YTM Beacon's dashboard."""
    if not s:
        return "unreachable"
    if s.get("headline"):
        return str(s["headline"])
    cfg = s.get("config") or s.get("settings") or {}
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


def describe(s):
    """(code, tone, text) for the Discord line."""
    code = headline(s)
    if code == "beacon-running" or ((s or {}).get("beaconApp") or {}).get("running"):
        code = "beacon-app"     # YTM Starcast's own Discord status waits until the old app is gone, whatever else
                                # it says
    tone, text = HEADLINES.get(code, ("ok", code.replace("-", " ")))
    if code == "snoozed":
        text = f"Hidden for {max(1, int((s or {}).get('snoozeSec') or 0) // 60)} more min"
    return code, tone, text


def details(raw):
    """Everything else the payload says, for the "details" box. Only what is there is shown."""
    raw = raw or {}
    disc, ext, now = (raw.get(k) or {} for k in ("discord", "extension", "nowPlaying"))
    cfg = raw.get("settings") or raw.get("config") or {}
    out = []
    if disc.get("user"):
        out.append(["discord user", str(disc["user"])])
    ep = str(disc.get("endpoint") or "")
    if ep:
        client = next((n for k, n in (("vesktop", "Vesktop"), ("vencord", "Vencord"), ("webcord", "WebCord"),
                                      ("flatpak/com.discordapp", "Discord (Flatpak)"), ("snap.discord", "Discord (Snap)"))
                       if k in ep.lower()), "Discord")
        out.append(["discord app", client])
        out.append(["connection", ep])
    if "clientId" in cfg:
        cid = str(cfg.get("clientId") or "").strip()
        out.append(["application id", (cid + "  (your own)") if cid and cid != DEFAULT_CLIENT_ID else
                    (cid or DEFAULT_CLIENT_ID) + "  (the built-in one)"])
    if now.get("title") and now.get("album"):
        out.append(["album", str(now["album"])])
    if ext:
        out.append(["browser extension", ("connected before" if ext.get("everConnected") else "never connected")
                    + (f" · build {raw['extensionBuild']}" if raw.get("extensionBuild") else "")])
    if ext.get("sources"):
        out.append(["reporting from", ", ".join(_source(x) for x in ext["sources"])])
    if raw.get("snoozeSec"):
        out.append(["hidden for", f"{int(raw['snoozeSec']) // 60} min more"])
    if raw.get("detail"):
        out.append(["last discord error", str(raw["detail"])])
    old = raw.get("beaconApp") or {}
    if old.get("running") or old.get("installed"):
        out.append(["old ytm beacon app", ("still running" if old.get("running") else "installed, not running")
                    + (f" · {old['version']}" if old.get("version") else "")])
    if raw.get("uptimeSec") is not None:
        out.append(["running for", _dur(raw.get("uptimeSec"))])
    return out


def _ago(sec):
    if sec is None:
        return "never"
    sec = int(sec)
    return f"{sec} s ago" if sec < 90 else f"{sec // 60} min ago" if sec < 5400 else f"{sec // 3600} h ago"


def _source(x):
    """One browser tab reporting what plays: "Brave · playing · 8 s ago"."""
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
