"""Orbit's local server: the web app plus a small JSON API over the app adapters.

Security: it listens on 127.0.0.1 only and has no login, like YTM Beacon. To stop web pages from
using it: the Host header must be a loopback name (no DNS rebinding), any Origin must be Orbit's own,
and POSTs must be JSON (a plain HTML form can't send that).
"""
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from . import apps as apps_mod
from .apps.base import BadRequest
from .apps.custom import validate as validate_custom
from . import desktop_entry
from . import net
from .config import Config
from .github_login import Login, LoginError
from .install import recipes as recipes_mod
from .install.engine import Failed, Installer, Sources
from .install.host import Host
from .net import Releases
from .runner import Runner, open_argv, terminal_argv
from .term import Terminals

STATIC = Path(__file__).parent / "static"
THEMES = [
    {"id": "tui", "label": "dusk"},
    {"id": "phosphor", "label": "phosphor"},
    {"id": "amber", "label": "amber"},
    {"id": "paper", "label": "paper"},
    {"id": "frost", "label": "frost"},
    {"id": "ember", "label": "ember"},
    {"id": "ocean", "label": "ocean"},
    {"id": "lilac", "label": "lilac"},
    {"id": "mono", "label": "mono"},
]
THEME_IDS = {t["id"] for t in THEMES}
ORBIT_REPO = "dash1101/orbit"
ORBIT_RELEASES = f"https://github.com/{ORBIT_REPO}/releases"
LINKS = {"project": f"https://github.com/{ORBIT_REPO}", "releases": ORBIT_RELEASES,
         "issues": f"https://github.com/{ORBIT_REPO}/issues"}
# What a custom theme may set (the same tokens the built-in ones use).
THEME_COLORS = ("bg", "raise", "line", "line-hi", "text", "muted", "legend", "accent", "accent-ink",
                "ok", "warn", "down", "off", "sel")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_THEME_ID = re.compile(r"^custom-[a-z0-9]{1,24}$")


def _valid_theme(t):
    if not isinstance(t, dict) or set(t) - {"id", "label", "tokens"}:
        raise BadRequest("a theme is {id, label, tokens}")
    if not isinstance(t.get("id"), str) or not _THEME_ID.match(t["id"]):
        raise BadRequest("a custom theme's id is custom-<letters and digits>")
    label = t.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 40:
        raise BadRequest("give the theme a name (up to 40 characters)")
    tok = t.get("tokens")
    if not isinstance(tok, dict) or set(tok) - set(THEME_COLORS) - {"radius", "glow"}:
        raise BadRequest("unknown theme token")
    for k in THEME_COLORS:
        if not isinstance(tok.get(k), str) or not _HEX.match(tok[k]):
            raise BadRequest(f"{k}: a colour like #1c1c1e")
    r = tok.get("radius", 0)
    if isinstance(r, bool) or not isinstance(r, int) or not 0 <= r <= 14:
        raise BadRequest("corner radius is 0–14")
    if not isinstance(tok.get("glow", False), bool):
        raise BadRequest("glow is on or off")
    return {"id": t["id"], "label": label.strip(), "tokens": {**{k: tok[k].lower() for k in THEME_COLORS},
                                                              "radius": r, "glow": bool(tok.get("glow", False))}}


class Jobs:
    """Slow actions (an updater) run in the background; the web app follows them by id."""

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, title, fn):
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "title": title, "state": "running", "output": "", "started": time.time()}
        with self._lock:
            self._jobs[jid] = job
            for old in sorted(self._jobs.values(), key=lambda j: j["started"])[:-20]:
                self._jobs.pop(old["id"], None)

        def work():
            try:
                rc, out = fn()
                job.update(state="done" if rc == 0 else "failed", output=(out or "")[-8000:])
            except Exception as e:  # report, never crash the server
                job.update(state="failed", output=str(e))
            job["ended"] = time.time()

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "job": jid, "message": title + "…"}

    def get(self, jid):
        with self._lock:
            j = self._jobs.get(jid)
            return dict(j) if j else None


class Ctx:
    def __init__(self, config, runner, releases, jobs):
        self.config, self.runner, self.releases, self.jobs = config, runner, releases, jobs

    @property
    def dry_run(self):
        return self.runner.dry_run


class Hub:
    CACHE_S = 1.2

    def __init__(self, config=None, runner=None, releases=None, app_classes=None, host=None, sources=None,
                 recipes=None):
        self.ctx = Ctx(config or Config(), runner or Runner(), releases or Releases(), Jobs())
        self._app_classes = app_classes
        self._lock = threading.Lock()
        self._cache = {}
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="orbit-status")
        self.github = Login(self.ctx.config)
        net.set_login_provider(self.github.token)
        self.terminals = Terminals()
        self.last_seen = time.time()   # when a page last asked for anything
        self.host = host or Host()
        sources = sources or Sources.from_env()
        self.installer = Installer(self.host, sources,
                                   recipes if recipes is not None else recipes_mod.load(self.host, sources),
                                   dry_run=self.ctx.dry_run, spawn=self.ctx.runner.spawn, on_change=self._installed)
        self.reload()

    def run_action(self, a, action, args, cols=100, rows=30):
        """One app action. Terminal actions start in Orbit's own terminal (unless they'd change something in a dry run)."""
        spec = next((x for x in a.actions() if x["id"] == action), None)
        if not spec:
            raise BadRequest("that action isn't available right now")
        if not spec.get("term"):
            return a.run_action(action, args)
        cmd = a.term_command(action, args)
        if not cmd:
            raise BadRequest("unknown action")
        shown = cmd.get("shown") or " ".join(cmd["argv"])
        for secret in cmd.get("mask", ()):
            shown = shown.replace(secret, "••••••")
        if self.ctx.dry_run and spec.get("changes"):
            for path in cmd.get("cleanup", ()):
                try:
                    import os
                    os.unlink(path)
                except OSError:
                    pass
            return {"ok": True, "message": f"dry run: would run {shown}"}
        try:
            s = self.terminals.start(cmd["argv"], cmd.get("title") or spec["label"], cols=cols, rows=rows,
                                     cleanup=cmd.get("cleanup", ()), mask=cmd.get("mask", ()), shown=shown)
        except (OSError, RuntimeError) as e:
            raise BadRequest(f"couldn't start {cmd['argv'][0]}: {e}") from None
        return {"ok": True, "term": s.id, "title": s.title, "message": "running " + shown}

    def _installed(self, app_id):
        net.forget_ports()
        self._cache.pop(app_id, None)
        a = self.apps.get(app_id)
        if a and hasattr(a, "_found"):
            a._found = None

    def reload(self):
        with self._lock:
            if self._app_classes is not None:
                self.apps = {c.id: c(self.ctx) for c in self._app_classes}
            else:
                self.apps = apps_mod.load(self.ctx)
            self._cache.clear()

    def app(self, app_id):
        a = self.apps.get(app_id)
        if not a:
            raise KeyError(app_id)
        return a

    def enabled(self, app_id):
        return self.ctx.config.app(app_id).get("enabled", True)

    def _status(self, a):
        hit = self._cache.get(a.id)
        if hit and time.time() - hit[0] < self.CACHE_S:
            return hit[1]
        na = self.unavailable(a.id)
        if na:   # it can't run on this system: nothing to look at (and nothing to run to look)
            s = {"state": "off", "headline": na, "facts": [], "units": [], "checked": time.time()}
            self._cache[a.id] = (time.time(), s)
            return s
        try:
            s = a.status()
        except Exception as e:  # a broken adapter must not take the hub down
            s = {"state": "unknown", "headline": f"Orbit couldn't read this app: {e}", "facts": [], "units": []}
        s.setdefault("state", "unknown")
        s.setdefault("facts", [])
        if s["state"] in ("down", "unknown") and not s.get("version") and self._not_installed(a.id):
            # an app that was never installed isn't broken: grey, with the install button, not red
            s.update(state="off", headline="Not installed", notInstalled=True)
        s["checked"] = time.time()
        self._cache[a.id] = (time.time(), s)
        return s

    def _not_installed(self, app_id):
        r = self.installer.recipes.get(app_id)
        if not r:
            return False
        try:
            return not r.installed()["installed"]
        except Exception:
            return False

    def overview(self):
        ids = [i for i in self.apps if self.enabled(i)]
        futures = {i: self._pool.submit(self._status, self.apps[i]) for i in ids}
        out = []
        for i in self.apps:
            d = self.apps[i].describe()
            d["enabled"] = self.enabled(i)
            d["install"] = self.install_brief(i)
            if i in futures:
                try:
                    s = futures[i].result(timeout=8)
                except Exception as e:
                    s = {"state": "unknown", "headline": str(e)}
                d.update(state=s["state"], headline=s.get("headline", ""), version=s.get("version", ""),
                         update=bool((s.get("update") or {}).get("available")), line=s.get("line", ""))
                na = self.unavailable(i)
                if na:
                    d.update(state="off", headline=na, unavailable=True)
                elif (s["state"] in ("ok", "warn") or s.get("running")) and self.apps[i].open_label:
                    d["open"] = self.apps[i].open_label
            else:
                d.update(state="off", headline="hidden (orbit settings → apps)", version="", update=False)
            out.append(d)
        return out

    def detail(self, app_id):
        a = self.app(app_id)
        self._cache.pop(app_id, None)
        d = a.describe()
        d["status"] = self._status(a)
        na = self.unavailable(app_id)
        if na:
            d["status"] = dict(d["status"], state="off", headline=na, unavailable=True,
                               detail=self.installer.recipes[app_id].support()["note"])
        d["actions"] = a.actions()
        d["settings"] = a.settings()
        d["enabled"] = self.enabled(app_id)
        d["install"] = self.install_brief(app_id)
        return d

    def unavailable(self, app_id):
        """"Not available on Windows yet" for an app that doesn't run on this system (and isn't installed anyway)."""
        r = self.installer.recipes.get(app_id)
        if not r or r.support()["level"] != "none" or r.installed()["installed"]:
            return ""
        return f"Not available on {self.host.describe()['systemName']} yet"

    # ---- installer ----------------------------------------------------------------------
    def install_brief(self, app_id):
        r = self.installer.recipes.get(app_id)
        if not r:
            return None
        inst, sup = r.installed(), r.support()
        rel = self.installer.sources.cached(r.repo)
        latest = rel["tag"].lstrip("vV") if rel else ""
        from .net import is_newer
        return {"installed": inst["installed"], "version": inst.get("version", ""), "supported": sup["level"],
                "latest": latest, "busy": self.installer.busy(app_id), "pending": inst.get("pending", ""),
                "update": bool(inst["installed"] and latest and inst.get("version") and not inst.get("pending")
                               and is_newer(latest, inst["version"]))}

    def install_list(self):
        if self.ctx.config.get("checkUpdates"):
            # first look: learn the latest versions in the background (at most every 10 minutes per app)
            for r in self.installer.recipes.values():
                if self.installer.sources.cached(r.repo) is None and r.support()["level"] != "none":
                    self._pool.submit(self._fetch_latest, r)
        out = []
        for r in self.installer.recipes.values():
            d = r.describe()
            if r.id in self.apps:
                d["icon"] = self.apps[r.id].describe().get("icon", "")
            elif getattr(r, "self_app", False):
                d.update(icon="/icon.svg", self=True)
            d.update(self.install_brief(r.id))
            d["installed"] = r.installed()
            out.append(d)
        return {"host": self.host.describe(), "dryRun": self.ctx.dry_run, "apps": out}

    def _fetch_latest(self, r, force=False):
        try:
            self.installer.sources.latest(r.repo, force=force)
        except Failed:
            pass

    def install_refresh(self):
        list(self._pool.map(lambda r: self._fetch_latest(r, True), self.installer.recipes.values()))
        return self.install_list()

    def install_next(self, jid, nid):
        job = self.installer.job(jid)
        if not job:
            raise KeyError(jid)
        item = next((n for n in (job.result or {}).get("next", []) if n.get("id") == nid), None)
        if not item or item.get("kind") not in ("url", "terminal", "shortcut", "program", "restart"):
            raise BadRequest("that isn't available")
        runner = self.ctx.runner
        if item["kind"] == "restart":
            return self.restart()
        if item["kind"] == "program":
            # a window the app itself installed (a recipe's own fixed path, never one from the request)
            if not runner.launch(item["argv"]):
                raise BadRequest("couldn't open it; run: " + " ".join(item["argv"]))
            return {"ok": True, "message": "opened " + item["label"]}
        if item["kind"] == "shortcut":
            # a Start-menu entry the install just made (a recipe's own path, never one from the request)
            path = str(item.get("path") or "")
            if not path.lower().endswith(".lnk") or not runner.open_file(path):
                raise BadRequest("couldn't open it; look for it in the Start menu")
            return {"ok": True, "message": "opened " + path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1][:-4]}
        if item["kind"] == "url":
            url = item["url"]
            if not url.startswith(("http://127.0.0.1:", "https://127.0.0.1:", "http://localhost:")):
                raise BadRequest("not a local address")
            runner.open_url(url)
            return {"ok": True, "message": "opened " + url}
        argv = terminal_argv(item["argv"], job.app_name)
        if not argv:
            raise BadRequest("no terminal program found; run: " + " ".join(item["argv"]))
        runner.launch(argv)
        return {"ok": True, "message": "opened a terminal: " + " ".join(item["argv"])}

    def hub_info(self):
        c = self.ctx.config.get()
        return {"name": "Orbit", "version": __version__, "dryRun": self.ctx.dry_run, "github": self.github.state(),
                "system": self.host.system,
                "orbitUpdate": self.orbit_update(), "menu": desktop_entry.state(),
                "themes": THEMES + [dict(t, custom=True) for t in c.get("themes", [])], "themeTokens": THEME_COLORS,
                "settings": {"theme": c["theme"], "refreshSec": c["refreshSec"], "checkUpdates": c["checkUpdates"]},
                "apps": [dict(a.describe(), enabled=self.enabled(i)) for i, a in self.apps.items()]}

    def set_hub(self, body):
        patch = {}
        if "themes" in body:
            themes = body["themes"]
            if not isinstance(themes, list) or len(themes) > 30:
                raise BadRequest("themes is a list (up to 30)")
            themes = [_valid_theme(t) for t in themes]
            if len({t["id"] for t in themes}) != len(themes):
                raise BadRequest("two themes have the same id")
            patch["themes"] = themes
        if "theme" in body:
            custom = {t["id"] for t in patch.get("themes", self.ctx.config.get("themes"))}
            if body["theme"] not in THEME_IDS | custom:
                raise BadRequest("unknown theme")
            patch["theme"] = body["theme"]
        elif "themes" in patch and self.ctx.config.get("theme") not in THEME_IDS | {t["id"] for t in patch["themes"]}:
            patch["theme"] = "tui"   # the theme in use was deleted
        if "refreshSec" in body:
            v = body["refreshSec"]
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 60:
                raise BadRequest("refresh must be 1–60 seconds")
            patch["refreshSec"] = v
        if "checkUpdates" in body:
            if not isinstance(body["checkUpdates"], bool):
                raise BadRequest("checkUpdates is true or false")
            patch["checkUpdates"] = body["checkUpdates"]
        if "order" in body:
            order = body["order"]
            if not isinstance(order, list) or sorted(order) != sorted(self.apps):
                raise BadRequest("order must list every app once")
            patch["order"] = order
        if "enabled" in body:
            en = body["enabled"]
            if not isinstance(en, dict) or not all(k in self.apps and isinstance(v, bool) for k, v in en.items()):
                raise BadRequest("enabled is {app id: true/false}")
            for k, v in en.items():
                self.ctx.config.update_app(k, {"enabled": v})
        if set(body) - {"theme", "themes", "refreshSec", "checkUpdates", "order", "enabled"}:
            raise BadRequest("unknown hub setting")
        if patch:
            self.ctx.config.update(patch)
        if "order" in patch:
            self.reload()
        return self.hub_info()

    def add_custom(self, spec):
        spec = validate_custom(spec)
        if spec["id"] in self.apps:
            raise BadRequest("an app with that id already exists")
        self.ctx.config.update({"custom": self.ctx.config.get("custom") + [spec]})
        self.reload()
        return {"ok": True, "id": spec["id"]}

    def restart(self):
        """Start Orbit again (after it updated itself): the waiting new version takes this one's place, starts, and
        this one closes its window and ends."""
        import os
        import subprocess
        import sys
        from . import gui                  # everything this Orbit still needs is loaded before its file changes
        from .install.recipes.orbit import running_program, switch, waiting
        if self.ctx.dry_run:
            return {"ok": True, "message": "dry run: Orbit would restart now"}
        exe = running_program()
        if exe and waiting(exe):
            try:
                switch(exe)
            except OSError as e:
                raise BadRequest(f"couldn't switch to the new Orbit ({e}); this one keeps running") from None
        argv = ([str(exe)] if exe else [sys.executable, "-m", "orbit"]) + sys.argv[1:]
        env = dict(os.environ, ORBIT_RESTARTED="1")
        kw = {"creationflags": 0x00000008} if sys.platform == "win32" else {"start_new_session": True}
        try:
            subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **kw)
        except OSError as e:
            raise BadRequest(f"couldn't start Orbit again: {e}; close it and open it yourself") from None

        def leave():
            gui.close()
            os._exit(0)

        threading.Timer(0.8, leave).start()
        return {"ok": True, "message": "restarting Orbit…"}

    def orbit_update(self):
        """A newer Orbit on GitHub? Looked up in the background (with the release checks), never blocking.
        A newer Orbit already downloaded and waiting for a restart comes first."""
        from .install.recipes.orbit import waiting
        if waiting():
            return {"version": waiting(), "url": ORBIT_RELEASES, "here": True, "pending": True}
        if not self.ctx.config.get("checkUpdates"):
            return None
        info = self.ctx.releases.cached(ORBIT_REPO)
        if info is None:
            self._pool.submit(self.ctx.releases.latest, ORBIT_REPO)
            return None
        rel = info.get("release") if info.get("ok") else None
        if not rel or not net.is_newer(rel.get("tag", ""), __version__):
            return None
        r = self.installer.recipes.get("orbit")
        here = bool(r and not any(x["blocking"] for x in r.requirements("install")))
        return {"version": rel["tag"].lstrip("vV"), "url": rel.get("url") or ORBIT_RELEASES, "here": here}

    def github_access(self):
        repos = [r.repo for r in self.installer.recipes.values()] + [ORBIT_REPO]
        return {"access": self.github.access(repos), "state": self.github.state()}

    def check_updates(self):
        for a in self.apps.values():
            if a.repo:
                self._pool.submit(self.ctx.releases.latest, a.repo, True)
        self._cache.clear()
        return {"ok": True, "message": "checking for updates…"}


# --------------------------------------------------------------------------------------------
_APP_RE = re.compile(r"^/api/apps/([a-z0-9][a-z0-9-]{0,39})(/[a-z]+)?$")
_ICON_RE = re.compile(r"^/apps/([a-z0-9][a-z0-9-]{0,39}\.(?:svg|png))$")
_TERM_RE = re.compile(r"^/api/term/([0-9a-f]{12})(/input|/interrupt|/resize|/close)?$")
_INST_RE = re.compile(r"^/api/install/([a-z0-9][a-z0-9-]{0,39})(/plan|/start)?$")
_JOB_RE = re.compile(r"^/api/install/jobs/([0-9a-f]{12})(/cancel|/next)?$")


def _query(path):
    from urllib.parse import parse_qs
    q = path.split("?", 1)[1] if "?" in path else ""
    return {k: v[-1] for k, v in parse_qs(q).items()}


def make_handler(hub, port_ref):
    class Handler(BaseHTTPRequestHandler):
        server_version = "orbit"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        # ---- plumbing ------------------------------------------------------------------
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else (
                body.encode() if isinstance(body, str) else json.dumps(body).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _err(self, code, msg, hint=""):
            self._send(code, {"error": msg, "hint": hint} if hint else {"error": msg})

        def _allowed(self):
            host = self.headers.get("Host", "")
            ok_hosts = {f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"}
            if host not in ok_hosts:
                self._err(403, "forbidden host")
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {"http://" + h for h in ok_hosts}:
                self._err(403, "forbidden origin")
                return False
            return True

        def _body(self):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise BadRequest("send JSON")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 << 10:
                raise BadRequest("too large")
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise BadRequest("invalid JSON") from None

        # ---- routes --------------------------------------------------------------------
        def do_GET(self):
            if not self._allowed():
                return
            hub.last_seen = time.time()
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    html = (STATIC / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Security-Policy",
                                     "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                     "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(html)
                    return
                if path == "/icon.svg":
                    return self._send(200, (STATIC / "icon.svg").read_bytes(), "image/svg+xml")
                m = _ICON_RE.match(path)
                if m and (STATIC / "apps" / m.group(1)).is_file():
                    ctype = "image/svg+xml" if m.group(1).endswith(".svg") else "image/png"
                    return self._send(200, (STATIC / "apps" / m.group(1)).read_bytes(), ctype)
                if path == "/api/hub":
                    return self._send(200, hub.hub_info())
                if path == "/api/github":
                    return self._send(200, hub.github.state())
                if path == "/api/github/access":
                    return self._send(200, hub.github_access())
                if path == "/api/apps":
                    return self._send(200, {"apps": hub.overview(), "dryRun": hub.ctx.dry_run})
                if path == "/api/install":
                    return self._send(200, hub.install_list())
                if path == "/api/term":
                    return self._send(200, {"terminals": hub.terminals.list()})
                m = _TERM_RE.match(path)
                if m and not m.group(2):
                    try:
                        frm = max(0, int(_query(self.path).get("from", "0")))
                    except ValueError:
                        frm = 0
                    return self._send(200, hub.terminals.get(m.group(1)).snapshot(frm))
                m = _JOB_RE.match(path)
                if m and not m.group(2):
                    job = hub.installer.job(m.group(1))
                    if not job:
                        return self._err(404, "no such install")
                    try:
                        frm = max(0, int(_query(self.path).get("from", "0")))
                    except ValueError:
                        frm = 0
                    return self._send(200, job.snapshot(frm))
                m = _INST_RE.match(path)
                if m and not m.group(2):
                    op = _query(self.path).get("op", "install")
                    return self._send(200, hub.installer.recipe(m.group(1)).info(op))
                if path.startswith("/api/jobs/"):
                    j = hub.ctx.jobs.get(path.rsplit("/", 1)[1])
                    return self._send(200, j) if j else self._err(404, "no such job")
                m = _APP_RE.match(path)
                if m:
                    app_id, sub = m.group(1), m.group(2)
                    if sub is None:
                        return self._send(200, hub.detail(app_id))
                    if sub == "/logs":
                        return self._send(200, hub.app(app_id).logs() or "(nothing logged)", "text/plain; charset=utf-8")
                self._err(404, "not found")
            except KeyError:
                self._err(404, "no such app")
            except BadRequest as e:
                self._err(400, str(e))
            except Failed as e:
                self._err(400, str(e), e.hint)
            except Exception as e:   # a bug: answer with what went wrong instead of dropping the connection
                import traceback
                traceback.print_exc()
                try:
                    self._err(500, f"Orbit hit a problem: {type(e).__name__}: {e}")
                except OSError:
                    pass

        def do_POST(self):
            if not self._allowed():
                return
            path = self.path.split("?", 1)[0]
            try:
                body = self._body()
                if path == "/api/hub":
                    return self._send(200, hub.set_hub(body))
                if path == "/api/hub/custom":
                    return self._send(200, hub.add_custom(body))
                if path == "/api/hub/check-updates":
                    hub._pool.submit(hub.ctx.releases.latest, ORBIT_REPO, True)
                    return self._send(200, hub.check_updates())
                if path == "/api/hub/menu":
                    return self._send(200, desktop_entry.add(hub.ctx.runner))
                if path == "/api/hub/restart":
                    return self._send(200, hub.restart())
                if path == "/api/hub/open-link":
                    url = LINKS.get(body.get("which"))
                    if not url:
                        raise BadRequest("which link?")
                    if not hub.ctx.runner.open_url(url):
                        raise BadRequest("couldn't open a browser; the address is " + url)
                    return self._send(200, {"ok": True, "message": "opened " + url})
                if path == "/api/hub/open-release":
                    hub.ctx.runner.open_url((hub.orbit_update() or {}).get("url") or ORBIT_RELEASES)
                    return self._send(200, {"ok": True, "message": "opened Orbit's releases"})
                if path == "/api/github/login":
                    try:
                        return self._send(200, hub.github.start())
                    except LoginError as e:
                        raise BadRequest(str(e)) from None
                if path == "/api/github/open":
                    hub.ctx.runner.open_url("https://github.com/login/device")
                    return self._send(200, {"ok": True, "message": "opened github.com/login/device"})
                if path == "/api/github/cancel":
                    hub.github.cancel()
                    return self._send(200, hub.github.state())
                if path == "/api/github/logout":
                    return self._send(200, hub.github.sign_out())
                m = _TERM_RE.match(path)
                if m and m.group(2):
                    t = hub.terminals.get(m.group(1))
                    if m.group(2) == "/input":
                        text = body.get("text")
                        if not isinstance(text, str) or len(text) > 4096:
                            raise BadRequest("send text")
                        return self._send(200, {"ok": t.write(text)})
                    if m.group(2) == "/interrupt":
                        t.interrupt()
                    elif m.group(2) == "/resize":
                        cols, rows = body.get("cols"), body.get("rows")
                        if not all(isinstance(x, int) and 10 <= x <= 500 for x in (cols, rows)):
                            raise BadRequest("cols and rows are 10–500")
                        t.resize(cols, rows)
                    else:
                        hub.terminals.close(t.id)
                    return self._send(200, {"ok": True})
                if path == "/api/install/refresh":
                    return self._send(200, hub.install_refresh())
                m = _JOB_RE.match(path)
                if m and m.group(2) == "/cancel":
                    return self._send(200, hub.installer.cancel(m.group(1)).snapshot())
                if m and m.group(2) == "/next":
                    nid = body.get("id")
                    if not isinstance(nid, str):
                        raise BadRequest("which next step?")
                    return self._send(200, hub.install_next(m.group(1), nid))
                m = _INST_RE.match(path)
                if m and m.group(2) in ("/plan", "/start"):
                    op = body.get("op", "install")
                    if not isinstance(op, str):
                        raise BadRequest("op is install or uninstall")
                    r = hub.installer.recipe(m.group(1))
                    if m.group(2) == "/plan":
                        return self._send(200, r.info(op, body.get("options")))
                    job = hub.installer.start(m.group(1), op, body.get("options"))
                    return self._send(200, job.snapshot())
                m = _APP_RE.match(path)
                if m and m.group(2) in ("/settings", "/action"):
                    a = hub.app(m.group(1))
                    if m.group(2) == "/settings":
                        res = a.apply_settings(body)
                    else:
                        action = body.get("action")
                        if not isinstance(action, str) or len(action) > 40:
                            raise BadRequest("which action?")
                        args = body.get("args") or {}
                        if not isinstance(args, dict):
                            raise BadRequest("args must be an object")
                        cols, rows = body.get("cols", 100), body.get("rows", 30)
                        if not all(isinstance(x, int) and 10 <= x <= 500 for x in (cols, rows)):
                            cols, rows = 100, 30
                        net.forget_ports()     # it may have started or stopped something: look again
                        res = hub.run_action(a, action, args, cols, rows)
                        if res.get("removed"):
                            hub.reload()
                    hub._cache.pop(a.id, None)
                    return self._send(200, res)
                self._err(404, "not found")
            except KeyError:
                self._err(404, "no such app")
            except BadRequest as e:
                self._err(400, str(e))
            except Failed as e:
                self._err(400, str(e), e.hint)
            except Exception as e:   # a bug: answer with what went wrong instead of dropping the connection
                import traceback
                traceback.print_exc()
                try:
                    self._err(500, f"Orbit hit a problem: {type(e).__name__}: {e}")
                except OSError:
                    pass

    return Handler


def serve(hub, port=8790, host="127.0.0.1"):
    """Create the server (port 0 = any free port). Returns (server, actual port)."""
    port_ref = [port]
    httpd = ThreadingHTTPServer((host, port), make_handler(hub, port_ref))
    httpd.daemon_threads = True
    port_ref[0] = httpd.server_address[1]
    return httpd, port_ref[0]
