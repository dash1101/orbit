"""What an install recipe looks like.

A recipe knows, for one app: which systems it runs on, whether (and which version of) it is
installed here, what this computer still needs, the few choices worth asking about, and the steps to
install, update or remove it. The installer window is drawn entirely from `info()`; `plan()` turns
the same answers into steps for the engine.
"""
import time

from ...apps.base import BadRequest, coerce
from ..engine import Failed, Step, fmt_size

OPS = ("install", "uninstall")


def option(key, label, type_, value, help="", **extra):
    f = {"key": key, "label": label, "type": type_, "value": value, "help": help}
    f.update(extra)
    return f


def need(label, ok, hint="", blocking=True, detail=""):
    """One line of "what this computer needs": ok, or (blocking or not) with how to fix it."""
    return {"label": label, "ok": bool(ok), "hint": "" if ok else hint, "blocking": bool(blocking and not ok),
            "detail": detail}


class Recipe:
    id = ""
    name = ""
    repo = ""
    blurb = ""
    glyph = "?"
    color = "#888888"
    # system -> what installing gives you there: "full", "limited" (with a note) or "none" (with a note)
    PLATFORMS = {}

    def __init__(self, host, sources):
        self.host, self.sources = host, sources
        self._installed = None

    # ---- to override ------------------------------------------------------------------
    def detect(self):
        """{"installed": bool, "version": str, "where": str, "how": str} — looked at, never changed."""
        return {"installed": False, "version": "", "where": "", "how": ""}

    def requirements(self, op):
        return []

    def options(self, op):
        return []

    def assets(self, rel):
        """Release files this install downloads (for the size shown before starting)."""
        return []

    def summary(self, op, opts, rel):
        return []

    def steps(self, op, opts):
        return []

    def finish(self, sess, op, opts):
        return {}

    def can_uninstall(self):
        """(ok, reason) — whether Orbit knows how to remove what is installed here."""
        return True, ""

    # ---- shared -----------------------------------------------------------------------
    def support(self):
        level, note = self.PLATFORMS.get(self.host.system, ("none", f"{self.name} isn't available on this system yet."))
        if level != "none" and self.host.arch not in ("amd64", "arm64"):
            return {"level": "none", "note": f"{self.name} isn't available for this processor ({self.host.arch}) yet."}
        return {"level": level, "note": note}

    def forget(self):
        self._installed = None

    def installed(self):
        hit = self._installed
        if hit and time.time() - hit[0] < 4:
            return hit[1]
        try:
            d = self.detect()
        except Exception as e:  # never let detection break the window
            d = {"installed": False, "version": "", "where": "", "how": "", "error": str(e)}
        self._installed = (time.time(), d)
        return d

    def platforms(self):
        out = {}
        for sysname in ("linux", "darwin", "windows"):
            level, note = self.PLATFORMS.get(sysname, ("none", ""))
            out[sysname] = {"level": level, "note": note}
        return out

    def latest(self, force=False):
        """(release or None, error text, hint)."""
        try:
            return self.sources.latest(self.repo, force=force), "", ""
        except Failed as e:
            return None, str(e), e.hint

    def describe(self):
        inst = self.installed()
        rel = self.sources.cached(self.repo)
        return {"id": self.id, "name": self.name, "blurb": self.blurb, "glyph": self.glyph, "color": self.color,
                "repo": self.repo, "platforms": self.platforms(), "support": self.support(), "installed": inst,
                "latest": _version(rel["tag"]) if rel else ""}

    def _options(self, op, given):
        fields = self.options(op)
        given = given or {}
        if not isinstance(given, dict):
            raise BadRequest("options must be an object")
        known = {f["key"]: f for f in fields}
        if set(given) - set(known):
            raise BadRequest("unknown option: " + ", ".join(sorted(set(given) - set(known))))
        out = {}
        for f in fields:
            out[f["key"]] = coerce(f, given[f["key"]]) if f["key"] in given else f["value"]
            f["value"] = out[f["key"]]
        return fields, out

    def title(self, op, rel):
        inst = self.installed()
        if op == "uninstall":
            return f"Remove {self.name}"
        new = _version(rel["tag"]) if rel else ""
        if not inst["installed"]:
            return f"Install {self.name}"
        if new and inst.get("version") and _newer(new, inst["version"]):
            return f"Update {self.name} to {new}"
        return f"Reinstall {self.name}"

    def info(self, op, given=None):
        """Everything the installer window shows before starting."""
        if op not in OPS:
            raise BadRequest("op is install or uninstall")
        sup, inst = self.support(), self.installed()
        fields, opts = self._options(op, given)
        rel, rel_err, hint = None, "", ""
        if op == "install" and sup["level"] != "none":
            rel, rel_err, hint = self.latest()
        reqs = [] if sup["level"] == "none" else self.requirements(op)
        blocked = ""
        if sup["level"] == "none":
            blocked = sup["note"]
        elif op == "uninstall" and not inst["installed"]:
            blocked = f"{self.name} isn't installed on this computer."
        elif op == "uninstall" and not self.can_uninstall()[0]:
            blocked = self.can_uninstall()[1]
        elif op == "install" and rel_err:
            blocked = rel_err
        elif any(r["blocking"] for r in reqs):
            blocked = "This computer needs a few things first (see above)."
        size = 0
        if rel:
            size = sum(rel["assets"].get(n, {}).get("size", 0) for n in self.assets(rel))
        steps = [] if sup["level"] == "none" or (op == "uninstall" and not inst["installed"]) else self.steps(op, opts)
        return {
            "app": self.id, "name": self.name, "op": op, "title": self.title(op, rel), "host": self.host.describe(),
            "support": sup, "installed": inst, "platforms": self.platforms(),
            "release": ({"version": _version(rel["tag"]), "tag": rel["tag"], "published": rel["published"],
                         "prerelease": rel["prerelease"], "size": size, "sizeText": fmt_size(size) if size else "",
                         "url": rel["url"]} if rel else None),
            "requirements": reqs, "options": fields,
            "summary": [] if sup["level"] == "none" else self.summary(op, opts, rel),
            "steps": [{"id": s.id, "label": s.label} for s in steps],
            "needsAdmin": self.needs_admin(op), "blocked": blocked, "blockedHint": hint if blocked == rel_err else "",
        }

    def needs_admin(self, op):
        return False

    def plan(self, op, given=None):
        info = self.info(op, given)
        if info["blocked"]:
            raise Failed(info["blocked"])
        _fields, opts = self._options(op, given)
        steps = self.steps(op, opts)
        return {"title": info["title"], "steps": steps, "finish": lambda sess: self.finish(sess, op, opts),
                "vars": {"opts": opts, "before": info["installed"]}}


def _version(tag):
    return str(tag or "").lstrip("vV")


def _newer(a, b):
    from ...net import is_newer
    return is_newer(a, b)


def step(id, label, weight=1.0):
    """Decorator-free helper: `step("dl", "Download", 3)(fn)`."""
    return lambda fn: Step(id, label, fn, weight)
