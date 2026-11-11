"""What every app in Orbit looks like.

An app adapter answers four questions — how is it doing (`status`), what can I do to it (`actions`),
what can I change (`settings`), and what did it say lately (`logs`) — so the hub can show every app
the same way. Adding an app = one small subclass registered in `apps/__init__.py`, or a "custom" app
added from the hub's settings page with no code at all.

Settings fields are plain dicts the web app draws generically:
    {"key", "label", "type": bool|int|choice|text|words|secret|note, "value", "help"?,
     "min"?, "max"?, "options"?: [[value, label]], "scope": "app"|"hub"}
`scope: "app"` lives in the app itself (changed through its own API); `"hub"` is stored by Orbit.
"""
import re

from .. import systemd
from ..net import HttpError
from ..runner import open_argv, terminal_argv

STATES = ("ok", "warn", "down", "off", "unknown")


class BadRequest(Exception):
    pass


def field(key, label, type_, value, scope="app", **extra):
    f = {"key": key, "label": label, "type": type_, "value": value, "scope": scope}
    f.update(extra)
    return f


def coerce(f, v):
    """Check one submitted value against its field. Raises BadRequest with a readable message."""
    t, label = f["type"], f["label"]
    if t == "bool":
        if not isinstance(v, bool):
            raise BadRequest(f"{label}: expected on/off")
        return v
    if t == "int":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
            raise BadRequest(f"{label}: expected a whole number")
        v = int(v)
        if "min" in f and v < f["min"] or "max" in f and v > f["max"]:
            raise BadRequest(f"{label}: must be between {f.get('min')} and {f.get('max')}")
        return v
    if t == "choice":
        allowed = [o[0] for o in f.get("options", [])]
        if v not in allowed:
            raise BadRequest(f"{label}: must be one of {', '.join(map(str, allowed))}")
        return v
    if t in ("text", "secret"):
        if not isinstance(v, str) or len(v) > f.get("maxLen", 300):
            raise BadRequest(f"{label}: expected text")
        v = v.strip()
        if v and f.get("pattern") and not re.fullmatch(f["pattern"], v):
            raise BadRequest(f"{label}: {f.get('patternHelp') or 'that doesn’t look right'}")
        return v
    if t == "words":
        if isinstance(v, str):
            v = [w for w in (x.strip() for x in v.replace("\n", ",").split(",")) if w]
        if not isinstance(v, list) or not all(isinstance(w, str) for w in v):
            raise BadRequest(f"{label}: expected a list of words")
        if len(v) > f.get("maxItems", 50) or any(len(w) > 80 for w in v):
            raise BadRequest(f"{label}: too many or too long")
        return v
    raise BadRequest(f"{label}: can't be changed here")


class App:
    id = ""
    name = ""
    blurb = ""
    glyph = "?"           # one or two characters shown in the side bar tile
    color = "#888888"     # tile colour (themes may tone it)
    repo = ""             # "owner/name" on GitHub, for release checks
    homepage = ""
    units = ()            # (unit, user?) pairs this app runs as
    icon = ""             # file in static/apps/ (svg or png); the glyph is used when empty

    def __init__(self, ctx):
        self.ctx = ctx    # .runner .config .releases .dry_run .jobs

    # ---- hub-side settings -------------------------------------------------------------
    @property
    def hub(self):
        return self.ctx.config.app(self.id)

    def hub_fields(self):
        return []

    # ---- the four questions ------------------------------------------------------------
    def status(self):
        raise NotImplementedError

    def settings(self):
        """[{"title", "fields": [...]}]"""
        return [{"title": "connection", "fields": self.hub_fields()}] if self.hub_fields() else []

    def apply_app_settings(self, patch):
        raise BadRequest("this app has no settings that can be changed from here")

    def actions(self):
        return []

    def term_command(self, action, args):
        """For an action with "term": True — the command it runs in Orbit's terminal:
        {"argv": [...], "title": str, "cleanup": [files to delete afterwards], "mask": [secrets to hide]}."""
        return None

    def ask_value(self, action, args):
        """The value typed for an action with "ask" (checked against its pattern), or BadRequest."""
        a = next((x for x in self.actions() if x["id"] == action), None)
        spec = (a or {}).get("ask")
        if not spec:
            return None
        v = (args or {}).get("value")
        if not isinstance(v, str) or not v.strip() or len(v) > 400:
            raise BadRequest(f"{spec['label']}: please fill it in")
        v = v.strip()
        if spec.get("pattern") and not re.fullmatch(spec["pattern"], v):
            raise BadRequest(f"{spec['label']}: {spec.get('patternHelp') or 'that doesn’t look right'}")
        return v

    def run_action(self, action, args):
        raise BadRequest("unknown action")

    def logs(self):
        for unit, user in self.units[:1]:
            return systemd.journal(self.ctx.runner, unit, user)
        return ""

    # ---- shared helpers ----------------------------------------------------------------
    def apply_settings(self, patch):
        if not isinstance(patch, dict) or not patch:
            raise BadRequest("nothing to change")
        fields = {f["key"]: f for g in self.settings() for f in g["fields"]}
        app_patch, hub_patch = {}, {}
        for k, v in patch.items():
            f = fields.get(k)
            if not f or f["type"] == "note" or f.get("readonly"):
                raise BadRequest(f"unknown setting: {k}")
            (app_patch if f["scope"] == "app" else hub_patch)[k] = coerce(f, v)
        if hub_patch:
            self.apply_hub_settings(hub_patch)
        if app_patch:
            self.apply_app_settings(app_patch)
        return {"ok": True, "dryRun": self.ctx.dry_run and bool(app_patch)}

    def apply_hub_settings(self, patch):
        self.ctx.config.update_app(self.id, patch)

    def unit_states(self):
        return [systemd.show(self.ctx.runner, u, user) for u, user in self.units]

    def unit_actions(self):
        """Start/stop/restart/autostart buttons for the main *user* unit."""
        out = []
        for unit, user in self.units[:1]:
            if not user:
                continue
            st = systemd.show(self.ctx.runner, unit, True)
            if st.get("state") == "missing":
                continue
            running = st.get("state") in ("running", "starting")
            out.append({"id": "svc:restart" if running else "svc:start", "label": "restart" if running else "start",
                        "group": "service"})
            if running:
                out.append({"id": "svc:stop", "label": "stop", "group": "service", "confirm": f"Stop {self.name}?"})
            on = st.get("enabled") == "enabled"
            out.append({"id": "svc:disable" if on else "svc:enable", "group": "service",
                        "label": "start at login: on" if on else "start at login: off", "toggle": on})
        return out

    def run_unit_action(self, action):
        verb = action.split(":", 1)[1]
        unit, user = self.units[0]
        ok, msg = systemd.act(self.ctx.runner, unit, verb, user)
        if not ok:
            raise BadRequest(msg)
        return {"ok": True, "message": f"{verb}: {unit}" + (" (dry run)" if self.ctx.dry_run else "")}

    def open_url(self, url):
        if not (url.startswith("http://") or url.startswith("https://")):
            raise BadRequest("not a web address")
        if not self.ctx.runner.open_url(url):
            raise BadRequest("couldn't open a browser")
        return {"ok": True, "message": "opened " + url}

    def open_terminal(self, command, title=None):
        argv = terminal_argv(command, title or self.name)
        if not argv:
            raise BadRequest("no terminal program found; run: " + " ".join(command))
        if not self.ctx.runner.launch(argv):
            raise BadRequest("couldn't open a terminal; run: " + " ".join(command))
        return {"ok": True, "message": "opened a terminal: " + " ".join(command)}

    def release_info(self, current, force=False):
        """{'latest', 'url', 'available', 'error'} or None when checks are off / not yet run."""
        if not self.repo:
            return None
        rel = self.ctx.releases.latest(self.repo, force=True) if force else (
            self.ctx.releases.latest(self.repo) if self.ctx.config.get("checkUpdates") else self.ctx.releases.cached(self.repo))
        if not rel:
            return None
        if not rel.get("ok"):
            return {"error": rel.get("error")}
        r = rel.get("release") or {}
        from ..net import is_newer
        return {"latest": r.get("tag"), "url": r.get("url"), "available": is_newer(r.get("tag"), current)}

    @staticmethod
    def http_error(e):
        return str(e) if isinstance(e, HttpError) else repr(e)

    def describe(self):
        return {"id": self.id, "name": self.name, "blurb": self.blurb, "glyph": self.glyph,
                "color": self.color, "homepage": self.homepage, "custom": False,
                "icon": f"/apps/{self.icon}" if self.icon else ""}
