"""Nova: secure remote for a Linux home server (system services; its API is signed by a paired phone).

Orbit can't — and shouldn't — use Nova's API: every request except /ping must be signed by a paired device,
and risky ones are approved on the phone. So here Nova gets: reachability and version (public /ping),
the state of its systemd units, release checks, and doors into Nova web and the `sudo nova …` commands.
"""
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
# Oneshot jobs behind the timers: a failed last run is worth showing.
JOBS = (("nova-update-check.service", "update check"), ("nova-backups.service", "backups"),
        ("nova-alerts.service", "alerts"), ("nova-labs.service", "labs"))


class Nova(App):
    id = "nova"
    icon = "nova.svg"
    name = "Nova"
    blurb = "Secure remote for your Linux home server"
    glyph = "NV"
    color = "#5ec4b6"
    repo = "dash1101/Nova"
    homepage = "https://github.com/dash1101/Nova"
    units = tuple((u, False) for u, _ in UNITS)

    def __init__(self, ctx, local_hosts=None):
        super().__init__(ctx)
        self._local_hosts = local_hosts
        self._found = None

    def hub_fields(self):
        return [field("url", "Nova web address", "text", self.hub.get("url") or "", scope="hub",
                      help="Leave empty to find Nova on this computer. Example: https://myserver:8495")]

    def _candidates(self):
        if self._local_hosts is not None:
            return self._local_hosts
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

    def status(self):
        names = dict(UNITS)
        units = []
        for u, label in UNITS:
            st = systemd.show(self.ctx.runner, u, user=False)
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
        f.extend(self.remote_facts())
        if not ping:
            s.update(state="down", headline="Nova isn't answering" + (f" ({', '.join(core_bad)} not running)" if core_bad else ""))
        elif core_bad:
            s.update(state="warn", headline="Answering, but " + ", ".join(core_bad) + " isn't running")
        elif failed_jobs:
            s.update(state="warn", headline=f"Running · last {failed_jobs[0]} run failed")
        else:
            s.update(state="ok", headline="Running")
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
        return [
            {"title": "nova", "fields": [field("_note", "", "note",
                "Nova's own settings live in Nova web and the Nova app; risky changes are approved on your phone, "
                "so they aren't changed from here. Use “open nova web” or “sudo nova setup”.")]},
            {"title": "hub", "fields": self.hub_fields()},
        ]

    def actions(self):
        t = lambda id_, label, group, changes=False, **kw: dict(id=id_, label=label, group=group, term=True,
                                                               changes=changes, **kw)
        return [
            {"id": "open", "label": "open nova web", "group": "open", "primary": True},
            {"id": "releases", "label": "releases", "group": "open"},
            t("term:status", "status", "check"),
            t("term:doctor", "doctor", "check"),
            t("term:log", "recent actions", "check"),
            t("term:add", "pair a phone", "phones & browsers", True),
            t("term:add-viewer", "pair a view-only device", "phones & browsers", True),
            t("term:devices", "paired devices", "phones & browsers"),
            t("term:approve", "approve a browser", "phones & browsers", True,
              ask={"label": "Code shown in the browser", "pattern": r"[A-Za-z0-9 -]{4,24}",
                   "patternHelp": "letters and numbers, as shown in the browser"}),
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
        ]

    TERM = {
        "status": ["sudo", "nova", "status"],
        "doctor": ["sudo", "nova", "doctor"],
        "log": ["sudo", "nova", "log"],
        "add": ["sudo", "nova", "add"],
        "add-viewer": ["sudo", "nova", "add", "--viewer"],
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
        if action == "open":
            addr = self.address()
            if not addr:
                raise BadRequest("Nova isn't reachable; set its address in settings")
            return self.open_url(addr + "/")
        if action == "releases":
            return self.open_url(self.homepage + "/releases")
        raise BadRequest("unknown action")

    def logs(self):
        out = systemd.journal(self.ctx.runner, "nova-api.service", user=False)
        if "No entries" in out:
            return out + "\n(Nova runs as a system service; your account needs the 'adm' or 'systemd-journal' group to read its log.)"
        return out
