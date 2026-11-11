"""An app added from the hub with no code: a systemd unit and/or a health address and/or a web page."""
import re
from urllib.parse import urlsplit

from .. import net, systemd
from .base import App, BadRequest, field

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def validate(spec):
    """Check a custom app definition from the hub's settings page. Returns a clean dict."""
    if not isinstance(spec, dict):
        raise BadRequest("expected an object")
    name = str(spec.get("name") or "").strip()[:40]
    if not name:
        raise BadRequest("give it a name")
    app_id = str(spec.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:40]
    if not ID_RE.match(app_id):
        raise BadRequest("the id may only use a-z, 0-9 and dashes")
    unit = str(spec.get("unit") or "").strip()
    if unit and not unit.rsplit(".", 1)[-1] in ("service", "socket", "timer", "path", "target"):
        unit += ".service"
    if unit and not systemd.valid_unit(unit):
        raise BadRequest("that isn't a systemd unit name")
    scope = spec.get("scope") or "user"
    if scope not in ("user", "system"):
        raise BadRequest("scope is user or system")
    out = {"id": app_id, "name": name, "unit": unit, "scope": scope}
    for k in ("healthUrl", "openUrl"):
        v = str(spec.get(k) or "").strip()
        if v and urlsplit(v).scheme not in ("http", "https"):
            raise BadRequest(f"{k} must start with http:// or https://")
        out[k] = v
    if not (unit or out["healthUrl"] or out["openUrl"]):
        raise BadRequest("give it a service, a health address or a web page")
    return out


class CustomApp(App):
    glyph = "··"
    color = "#9aa0a6"

    def __init__(self, ctx, spec):
        super().__init__(ctx)
        self.spec = spec
        self.id = spec["id"]
        self.name = spec["name"]
        self.glyph = (spec["name"][:2] or "··").upper()
        self.blurb = "Added in Orbit"
        self.units = ((spec["unit"], spec["scope"] == "user"),) if spec.get("unit") else ()

    def describe(self):
        d = super().describe()
        d["custom"] = True
        return d

    def status(self):
        units = self.unit_states()
        s = {"units": units, "facts": []}
        unit = units[0] if units else None
        health = None
        if self.spec.get("healthUrl"):
            try:
                net.request("GET", self.spec["healthUrl"], timeout=2, raw=True)
                health = True
                s["facts"].append(["health", "answering"])
            except net.HttpError as e:
                health = False
                s["facts"].append(["health", str(e) or "no answer", "warn"])
        if unit and unit.get("state") == "failed":
            s.update(state="down", headline="The service failed — see the log")
        elif unit and unit.get("state") == "missing":
            s.update(state="unknown", headline=f"No unit called {unit['unit']}")
        elif health is False:
            s.update(state="down", headline="Not answering")
        elif unit and unit.get("state") == "stopped":
            s.update(state="off", headline="Not running")
        elif unit or health:
            s.update(state="ok", headline="Running")
        else:
            s.update(state="unknown", headline="Nothing to check — add a service or a health address")
        return s

    def settings(self):
        sp = self.spec
        return [{"title": "definition", "fields": [
            field("name", "Name", "text", sp["name"], scope="hub", maxLen=40),
            field("unit", "systemd unit", "text", sp.get("unit", ""), scope="hub"),
            field("scope", "Unit type", "choice", sp.get("scope", "user"), scope="hub",
                  options=[["user", "user (systemctl --user)"], ["system", "system (read-only here)"]]),
            field("healthUrl", "Health address", "text", sp.get("healthUrl", ""), scope="hub",
                  help="Any page that answers when the app is up."),
            field("openUrl", "Web page", "text", sp.get("openUrl", ""), scope="hub"),
        ]}]

    def apply_hub_settings(self, patch):
        spec = validate(dict(self.spec, **patch, id=self.id))
        custom = [spec if c.get("id") == self.id else c for c in self.ctx.config.get("custom")]
        self.ctx.config.update({"custom": custom})
        self.__init__(self.ctx, spec)

    def actions(self):
        acts = [{"id": "open", "label": "open", "group": "open", "primary": True}] if self.spec.get("openUrl") else []
        return acts + self.unit_actions() + [{"id": "remove", "label": "remove from orbit", "group": "orbit",
                                               "danger": True, "confirm": f"Remove {self.name} from Orbit? (The app itself is not touched.)"}]

    def run_action(self, action, args):
        if action == "open" and self.spec.get("openUrl"):
            return self.open_url(self.spec["openUrl"])
        if action.startswith("svc:") and self.units:
            return self.run_unit_action(action)
        if action == "remove":
            self.ctx.config.update({"custom": [c for c in self.ctx.config.get("custom") if c.get("id") != self.id]})
            return {"ok": True, "message": f"removed {self.name}", "removed": True}
        raise BadRequest("unknown action")
