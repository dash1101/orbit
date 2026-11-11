"""Nova: secure remote for a home server (system services; its API is signed by a paired phone).

Orbit can't — and shouldn't — use Nova's API: every request except /ping must be signed by a paired device,
and risky ones are approved on the phone. So here Nova gets: reachability and version (public /ping),
the state of its systemd units, release checks, and doors into Nova web and the `sudo nova …` commands.

How the *server* is doing (what needs attention, containers, drives, the last backup) comes from the file Nova's
own monitor writes for dashboards, which anyone on this computer may read. It is only read, and only when Nova
runs on this computer.

On Windows (a preview of Nova there) the server is three Task Scheduler tasks, its files are only readable by the
administrator, and the `nova` command needs an administrator window: Orbit shows whether Nova answers and its
version, and opens an administrator PowerShell window for each command (Windows asks for permission).
"""
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .. import net
from .. import systemd
from .base import App, BadRequest, field

PORT = 8495
UNITS = (
    ("nova-api.service", "api"),
    ("nova-gate.service", "access-code gate"),
    ("nova-helper.socket", "root helper"),
    ("nova-alerts.timer", "alerts"),
    ("nova-backups.timer", "backups"),
    ("nova-labs.timer", "labs"),
    ("nova-update-check.timer", "update check"),
)
# Parts of Nova that are only there when you set them up: listed when present, never counted as missing.
OPTIONAL_UNITS = (
    ("nova-brain.service", "assistant"),
    ("nova-ear.service", "assistant: voice"),
    ("nova-rgb-anim.service", "lighting"),
)
# What Nova's monitor last saw (nova_alerts.py writes it for dashboards, world-readable).
STATUS_FILE = Path("/var/lib/nova-alerts/www/status.json")
NOVA_GUI = "/usr/sbin/nova-gui"           # the Nova Server window (Nova 0.9.16 and newer); asks for the password itself
STALE_AFTER = 15 * 60          # the monitor runs every few seconds to minutes; older than this, it has stopped
_LEAD = re.compile(r"^[^\w/~]+\s*", re.UNICODE)          # the status emoji in front of a title

# Oneshot jobs behind the timers: a failed last run is worth showing.
JOBS = (("nova-update-check.service", "update check"), ("nova-backups.service", "backups"),
        ("nova-alerts.service", "alerts"), ("nova-labs.service", "labs"))


class Nova(App):
    id = "nova"
    icon = "nova.svg"
    name = "Nova"
    blurb = "Secure remote for your home server"
    glyph = "NV"
    color = "#5ec4b6"
    repo = "dash1101/Nova"
    homepage = "https://github.com/dash1101/Nova"
    units = tuple((u, False) for u, _ in UNITS)
    status_file = STATUS_FILE

    def __init__(self, ctx, local_hosts=None):
        super().__init__(ctx)
        self._local_hosts = local_hosts
        self._found = None

    # ---- Windows ------------------------------------------------------------------------
    @staticmethod
    def _windows():
        return sys.platform == "win32"

    @staticmethod
    def win_dir():
        return os.path.join(os.environ.get("ProgramFiles") or "C:\\Program Files", "Nova")

    def win_version(self):
        try:
            with open(os.path.join(self.win_dir(), "lib", "VERSION"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    WIN_TERM = {   # what the Windows server's `nova` command can do (docs/WINDOWS.md in Nova)
        "status": ["status"], "doctor": ["doctor"], "log": ["log"], "add": ["add"], "add-viewer": ["add", "--viewer"],
        "add-remote": ["add", "--remote"], "approvals": ["approve"], "links": ["link"], "plugins": ["plugin", "list"],
        "devices": ["devices"],
    }

    def win_admin_window(self, title, words):
        """An administrator PowerShell window running `nova <words>` (or a script), left open to read.
        The command travels base64-encoded (-EncodedCommand), so nothing in it can be misread as more commands."""
        q = lambda s: "'" + str(s).replace("'", "''") + "'"
        if words and words[0] == "@script":
            body = words[1]
        else:
            body = "& " + q(os.path.join(self.win_dir(), "nova.cmd")) + "".join(" " + q(w) for w in words)
        inner = f"$host.UI.RawUI.WindowTitle = {q(title)}; {body}"
        enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
        outer = ("Start-Process powershell.exe -Verb RunAs -ArgumentList "
                 f"'-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{enc}'")
        if not self.ctx.runner.launch(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", outer]):
            raise BadRequest("couldn't start PowerShell")
        return {"ok": True, "message": f"opened an administrator window: {title} (Windows asks for permission)"}

    def hub_fields(self):
        return [field("url", "Nova web address", "text", self.hub.get("url") or "", scope="hub",
                      help="Leave empty to find Nova on this computer. Example: https://myserver:8495")]

    def _candidates(self):
        if self._local_hosts is not None:
            return self._local_hosts
        import sys
        if not sys.platform.startswith("linux"):
            return ["127.0.0.1"]
        rc, out = self.ctx.runner.read(["hostname", "-I"], timeout=2)
        hosts = ["127.0.0.1"] + [h for h in out.split() if rc == 0 and ":" not in h]
        return hosts

    def address(self):
        url = (self.hub.get("url") or "").rstrip("/")
        if url and urlsplit(url).scheme in ("http", "https"):
            return url
        if self._found and net.port_open(urlsplit(self._found).hostname, PORT):
            return self._found
        for h in self._candidates():
            if net.port_open(h, PORT):
                self._found = f"https://{h}:{PORT}"
                return self._found
        self._found = None
        return None

    def health(self):
        """What Nova's monitor last wrote about the server, or None (not this computer, not readable, not Nova's).
        {"level", "headline", "issues": [title…], "metrics": {…}, "age": seconds or None, "stale": bool}"""
        try:
            d = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict) or d.get("level") not in ("ok", "info", "warning", "critical"):
            return None
        age = None
        try:
            age = max(0, time.time() - datetime.fromisoformat(str(d.get("updated"))).timestamp())
        except (TypeError, ValueError):
            pass
        clean = lambda t: _LEAD.sub("", str(t or "")).strip()[:120]
        issues = [clean(a.get("title")) for a in d.get("active") or []
                  if isinstance(a, dict) and a.get("level") in ("warning", "critical") and clean(a.get("title"))]
        m = d.get("metrics") if isinstance(d.get("metrics"), dict) else {}
        return {"level": d["level"], "headline": clean(d.get("headline")), "issues": issues, "metrics": m,
                "age": age, "stale": age is None or age > STALE_AFTER}

    @staticmethod
    def _ago(seconds):
        s = int(seconds)
        return f"{s // 86400}d ago" if s >= 86400 else f"{s // 3600}h ago" if s >= 3600 else f"{max(1, s // 60)} min ago"

    def health_facts(self, hl):
        """Rows for the status card: what needs attention first, then the server at a glance."""
        m, out = hl["metrics"], []
        if hl["stale"]:
            out.append(["monitor", "last checked " + (self._ago(hl["age"]) if hl["age"] is not None else "a while ago")
                        + ": Nova's alerts aren't running", "warn"])
            return out
        for i, t in enumerate(hl["issues"][:4]):
            out.append(["needs attention" if i == 0 else "", t, "warn"])
        if len(hl["issues"]) > 4:
            out.append(["", f"and {len(hl['issues']) - 4} more (open Nova)", "muted"])
        text = lambda k: m.get(k) if isinstance(m.get(k), str) and m.get(k).strip() else None
        for key, label in (("containers", "containers"), ("websites", "websites")):
            if text(key) and not text(key).startswith("0/0"):     # "0/0 up": none set up, nothing to say
                out.append([label, text(key)])
        drives = [d for d in m.get("storage") or [] if isinstance(d, dict) and isinstance(d.get("pct"), (int, float))]
        if drives:
            d = max(drives, key=lambda x: x["pct"])
            free = d.get("free")
            left = f" · {free / 1e9:.0f} GB free" if isinstance(free, (int, float)) else ""
            more = f" (fullest of {len(drives)})" if len(drives) > 1 else ""
            out.append(["storage", f"{d.get('name') or d.get('mount') or 'drive'} {d['pct']:.0f}%{left}{more}",
                        "warn" if d["pct"] >= 90 else None])
        if text("data_backup"):
            out.append(["last backup", text("data_backup")])
        if text("uptime"):
            out.append(["up for", text("uptime")])
        return out

    def status(self):
        if self._windows():
            return self.win_status()
        names = dict(UNITS)
        units = []
        for u, label in UNITS:
            st = systemd.show(self.ctx.runner, u, user=False)
            st["label"] = label
            units.append(st)
        for u, label in OPTIONAL_UNITS:
            st = systemd.show(self.ctx.runner, u, user=False)
            if st.get("state") not in ("missing", None):
                st["label"] = label
                units.append(st)
        installed = any(u.get("state") not in ("missing", None) for u in units)
        s = {"units": units, "facts": []}
        failed_jobs = []
        for u, label in JOBS:
            st = systemd.show(self.ctx.runner, u, user=False)
            if st.get("state") == "failed":
                failed_jobs.append(label)
        addr = self.address()
        ping = None
        if addr:
            try:
                ping = net.get_json(addr + "/api/v1/ping", timeout=2, insecure=True)
            except net.HttpError as e:
                s["detail"] = str(e)
        f = s["facts"]
        if addr:
            f.append(["web", addr])
        if not installed and not ping:
            s.update(state="off", headline="Not installed on this computer" + ("" if self.hub.get("url") else
                                                                                  " — set its address in settings"))
            return s
        core = [u for u in units if u["unit"] in ("nova-api.service", "nova-helper.socket")]
        core_bad = [names[u["unit"]] for u in core if u.get("state") not in ("running", "missing")]
        if ping and ping.get("ok"):
            s["version"] = ping.get("api", "")
            f.append(["api level", f"{ping.get('api_level')} (oldest served {ping.get('min_level')})"])
        if failed_jobs:
            f.append(["last run failed", ", ".join(failed_jobs), "warn"])
        hl = self.health() if installed else None
        if hl:
            f.extend(self.health_facts(hl))
        f.extend(self.remote_facts())
        if not ping:
            s.update(state="down", headline="Nova isn't answering" + (f" ({', '.join(core_bad)} not running)" if core_bad else ""))
        elif core_bad:
            s.update(state="warn", headline="Answering, but " + ", ".join(core_bad) + " isn't running")
        elif failed_jobs:
            s.update(state="warn", headline=f"Running · the last {failed_jobs[0]} failed")
        elif hl and hl["stale"]:
            s.update(state="warn", headline="Running, but its alerts have stopped checking the server")
        elif hl and hl["issues"]:
            n = len(hl["issues"])
            s.update(state="warn", headline=f"Running · {n} need{'s' if n == 1 else ''} attention: {hl['issues'][0]}")
        elif hl:
            s.update(state="ok", headline="Running · all systems normal")
        else:
            s.update(state="ok", headline="Running")
        rel = self.release_info(s.get("version", ""))
        if rel:
            s["update"] = rel
        return s

    def win_status(self):
        version = self.win_version()
        s = {"units": [], "facts": []}
        addr = self.address()
        ping = None
        if addr:
            try:
                ping = net.get_json(addr + "/api/v1/ping", timeout=2, insecure=True)
            except net.HttpError as e:
                s["detail"] = str(e)
        f = s["facts"]
        if not version and not ping:
            s.update(state="off", headline="Not installed on this computer" + ("" if self.hub.get("url") else
                                                                                  " — set its address in settings"))
            return s
        if addr:
            f.append(["web", addr])
        if version:
            f.append(["installed", f"{self.win_dir()} (a preview on Windows)"])
        if ping and ping.get("ok"):
            s["version"] = ping.get("api", "") or version
            f.append(["api level", f"{ping.get('api_level')} (oldest served {ping.get('min_level')})"])
            s.update(state="ok", headline="Running")
        else:
            s["version"] = version
            s.update(state="down", headline="Nova isn't answering",
                     detail="Its three tasks are in Task Scheduler (folder \\Nova\\); its logs are in C:\\ProgramData\\Nova\\log.")
        if self.ctx.runner.available("tailscale"):
            f.extend(x for x in self.remote_facts() if x[0] == "tailscale")
        rel = self.release_info(s.get("version", ""))
        if rel:
            s["update"] = rel
        return s

    def remote_facts(self):
        """Away-from-home: Tailscale and the Cloudflare tunnel, read without root."""
        out = []
        r = self.ctx.runner
        if r.available("tailscale"):
            rc, js = r.read(["tailscale", "status", "--json", "--peers=false"], timeout=3)
            try:
                import json
                st = json.loads(js) if rc == 0 else {}
            except ValueError:
                st = {}
            state = st.get("BackendState", "")
            ips = [ip for ip in (st.get("Self") or {}).get("TailscaleIPs") or [] if ":" not in ip]
            name = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
            if state == "Running":
                out.append(["tailscale", "connected · " + (name or (ips[0] if ips else ""))])
            else:
                out.append(["tailscale", {"NeedsLogin": "not signed in", "Stopped": "turned off"}.get(state, state or "not running"),
                            "warn"])
        else:
            out.append(["tailscale", "not installed", "muted"])
        cf = systemd.show(r, "cloudflared.service", user=False)
        if cf.get("state") not in ("missing", None):
            out.append(["cloudflare tunnel", cf["state"], None if cf["state"] == "running" else "warn"])
        return out

    def settings(self):
        how = "“nova server” in actions" if self._windows() else "“sudo nova setup”"
        return [
            {"title": "nova", "fields": [field("_note", "", "note",
                "Nova's own settings live in Nova web and the Nova app; risky changes are approved on your phone, "
                f"so they aren't changed from here. Use “open nova web” or {how}.")]},
            {"title": "connection", "fields": self.hub_fields()},
        ]

    def win_actions(self):
        w = lambda id_, label, group, **kw: dict(id="win:" + id_, label=label, group=group, **kw)
        acts = [{"id": "open", "label": "open nova web", "group": "open", "primary": True}]
        if self.win_version():
            acts.append({"id": "gui", "label": "nova server", "group": "open"})
        acts.append({"id": "releases", "label": "releases", "group": "open"})
        if not self.win_version():
            return acts
        approve = next(a for a in self.actions(linux=True) if a["id"] == "term:approve")
        remove = next(a for a in self.actions(linux=True) if a["id"] == "term:remove")
        acts += [
            w("status", "status", "check"), w("doctor", "doctor", "check"), w("log", "recent actions", "check"),
            w("journal", "api log", "check"),
            w("add", "pair a phone", "phones & browsers"), w("add-viewer", "pair a view-only device", "phones & browsers"),
            w("add-remote", "pair a phone that's away from home", "phones & browsers"),
            w("devices", "paired devices", "phones & browsers"), w("approvals", "waiting for approval", "phones & browsers"),
            w("approve", "approve a browser", "phones & browsers", ask=approve["ask"]),
            w("remove", "remove a device", "phones & browsers", danger=True, ask=remove["ask"]),
            w("links", "linked servers", "more"), w("plugins", "plugins", "more"),
        ]
        return acts

    def actions(self, linux=False):
        if self._windows() and not linux:
            return self.win_actions()
        t = lambda id_, label, group, changes=False, **kw: dict(id=id_, label=label, group=group, term=True,
                                                               changes=changes, **kw)
        gui = [{"id": "gui", "label": "nova server", "group": "open"}] if os.path.exists(NOVA_GUI) else []
        return [
            {"id": "open", "label": "open nova web", "group": "open", "primary": True},
            *gui,
            {"id": "releases", "label": "releases", "group": "open"},
            t("term:status", "status", "check"),
            t("term:doctor", "doctor", "check"),
            t("term:log", "recent actions", "check"),
            t("term:journal", "api log", "check"),
            t("term:add", "pair a phone", "phones & browsers", True),
            t("term:add-viewer", "pair a view-only device", "phones & browsers", True),
            t("term:add-remote", "pair a phone that's away from home", "phones & browsers", True),
            t("term:devices", "paired devices", "phones & browsers"),
            t("term:approvals", "waiting for approval", "phones & browsers"),
            t("term:approve", "approve a browser", "phones & browsers", True,
              ask={"label": "Code shown in the browser", "pattern": r"[A-Za-z0-9 -]{4,24}",
                   "patternHelp": "letters and numbers, as shown in the browser"}),
            t("term:remove", "remove a device", "phones & browsers", True, danger=True,
              ask={"label": "Name or ID of the device (see paired devices)", "pattern": r"[\w.'(][\w .'()-]{0,59}",
                   "patternHelp": "its name or ID, as listed in paired devices",
                   "help": "It can never connect again; pair it anew to bring it back."}),
            t("term:tailscale-status", "tailscale status", "away from home"),
            t("term:tailscale-up", "connect to tailscale", "away from home", True,
              confirm="Sign this computer in to Tailscale? A sign-in link appears in the terminal."),
            t("term:cf-token", "cloudflare api token", "away from home", True,
              ask={"label": "Cloudflare API token", "secret": True, "pattern": r"[A-Za-z0-9_-]{30,200}",
                   "patternHelp": "that doesn't look like a Cloudflare API token",
                   "help": "Used by Nova's Cloudflare auto-setup (Nova web → Labs). Create it in Cloudflare → My "
                           "Profile → API Tokens with: Account · Cloudflare Tunnel · Edit, Account · Access: Apps and "
                           "Policies · Edit, Zone · DNS · Edit. Saved root-only on this server."}),
            t("term:cf-list", "what nova changed in cloudflare", "away from home"),
            t("term:cf-undo", "undo nova's cloudflare changes", "away from home", True, danger=True),
            t("term:setup", "run setup again", "away from home", True),
            t("term:update-check", "check for update", "maintenance"),
            t("term:update", "update nova", "maintenance", True, confirm="Install the newest Nova release?"),
            t("term:backup-now", "back up now", "maintenance", True),
            t("term:backup-list", "list backups", "maintenance"),
            t("term:restart", "restart api", "maintenance", True, confirm="Restart Nova's API? Phones reconnect by themselves."),
            t("term:links", "linked servers", "more"),
            t("term:plugins", "plugins", "more"),
        ]

    TERM = {
        "status": ["sudo", "nova", "status"],
        "doctor": ["sudo", "nova", "doctor"],
        "log": ["sudo", "nova", "log"],
        "journal": ["sudo", "journalctl", "-u", "nova-api.service", "-n", "300", "--no-pager"],
        "add": ["sudo", "nova", "add"],
        "add-viewer": ["sudo", "nova", "add", "--viewer"],
        "add-remote": ["sudo", "nova", "add", "--remote"],
        "approvals": ["sudo", "nova", "approve"],
        "links": ["sudo", "nova", "link"],
        "plugins": ["sudo", "nova", "plugin", "list"],
        "devices": ["sudo", "nova", "devices"],
        "tailscale-status": ["tailscale", "status"],
        "tailscale-up": ["sudo", "tailscale", "up"],
        "cf-list": ["sudo", "nova", "cloudflare", "list"],
        "cf-undo": ["sudo", "nova", "cloudflare", "undo"],
        "setup": ["sudo", "nova", "setup"],
        "update-check": ["sudo", "nova", "update", "--check"],
        "update": ["sudo", "nova", "update"],
        "backup-now": ["sudo", "nova", "backup", "now"],
        "backup-list": ["sudo", "nova", "backup", "list"],
        "restart": ["sudo", "systemctl", "restart", "nova-api.service"],
    }
    # Saves the Cloudflare API token the way Nova's own root helper does (cfsync.save_token), reading it from a
    # private file so it never appears on a command line.
    CF_SAVE = ("import sys; sys.path.insert(0, '/usr/lib/nova-api'); import cfsync; "
               "cfsync.save_token(open(sys.argv[1]).read().strip()); "
               "print('Saved. Nova\'s Cloudflare auto-setup can use it now (Nova web, Labs).')")

    def term_command(self, action, args):
        key = action[5:] if action.startswith("term:") else ""
        if key == "approve":
            code = self.ask_value(action, args)
            return {"argv": ["sudo", "nova", "approve", code.replace(" ", "")], "title": "Nova · approve a browser"}
        if key == "remove":
            who = self.ask_value(action, args)
            return {"argv": ["sudo", "nova", "remove", who], "title": "Nova · remove a device"}
        if key == "cf-token":
            import os
            import tempfile
            tok = self.ask_value(action, args)
            if not os.path.exists("/usr/lib/nova-api/cfsync.py"):
                raise BadRequest("This Nova doesn't have Cloudflare auto-setup; update Nova first.")
            fd, path = tempfile.mkstemp(prefix="orbit-cf-")
            with os.fdopen(fd, "w") as f:
                f.write(tok)
            return {"argv": ["sudo", "/usr/bin/python3", "-c", self.CF_SAVE, path], "title": "Nova · Cloudflare token",
                    "cleanup": [path], "mask": [tok],
                    "shown": "sudo python3 (Nova's cfsync.save_token) — save the Cloudflare API token, root-only"}
        if key in self.TERM:
            if key.startswith("tailscale") and not self.ctx.runner.available("tailscale"):
                raise BadRequest("Tailscale isn't installed. Get it from tailscale.com/download, or run Nova's "
                                 "setup again and choose Tailscale.")
            label = next((a["label"] for a in self.actions() if a["id"] == action), key)
            return {"argv": self.TERM[key], "title": f"Nova · {label}"}
        return None

    def run_action(self, action, args):
        if action == "gui" and not self._windows():
            if not os.path.exists(NOVA_GUI) or not self.ctx.runner.launch([NOVA_GUI]):
                raise BadRequest("Nova Server's window isn't installed (it comes with Nova 0.9.16 and newer)")
            return {"ok": True, "message": "opened Nova Server (it asks for your password)"}
        if action == "gui" and self._windows():
            lnk = os.path.join(os.environ.get("ProgramData") or "C:\\ProgramData", "Microsoft", "Windows", "Start Menu",
                               "Programs", "Nova Server.lnk")
            if not os.path.exists(lnk) or not self.ctx.runner.open_file(lnk):
                raise BadRequest("Nova Server isn't in the Start menu; reinstall Nova to bring it back")
            return {"ok": True, "message": "opened Nova Server"}
        if action.startswith("win:") and self._windows():
            key = action[4:]
            label = next((a["label"] for a in self.win_actions() if a["id"] == action), key)
            if key == "approve":
                return self.win_admin_window("Nova · approve a browser",
                                             ["approve", self.ask_value(action, args).replace(" ", "")])
            if key == "remove":
                return self.win_admin_window("Nova · remove a device", ["remove", self.ask_value(action, args)])
            if key == "journal":
                log = os.path.join(os.environ.get("ProgramData") or "C:\\ProgramData", "Nova", "log", "api.log")
                return self.win_admin_window("Nova · api log",
                                             ["@script", "Get-Content -Tail 300 '" + log.replace("'", "''") + "'"])
            if key in self.WIN_TERM:
                return self.win_admin_window(f"Nova · {label}", self.WIN_TERM[key])
            raise BadRequest("unknown action")
        if action == "open":
            addr = self.address()
            if not addr:
                raise BadRequest("Nova isn't reachable; set its address in settings")
            return self.open_url(addr + "/")
        if action == "releases":
            return self.open_url(self.homepage + "/releases")
        raise BadRequest("unknown action")

    def logs(self):
        if self._windows():
            return ("(Nova's logs are in C:\\ProgramData\\Nova\\log, which only the administrator can read.\n"
                    " Use “api log” under actions to open the newest lines in an administrator window.)")
        out = systemd.journal(self.ctx.runner, "nova-api.service", user=False)
        if not out.strip() or "No entries" in out:
            return ("(Nothing here that your account may read. Nova runs as a system service, and its log is only "
                    "readable by the administrator or members of the 'adm' or 'systemd-journal' groups.\n"
                    " Use “api log” under actions to read it with sudo.)")
        return out
