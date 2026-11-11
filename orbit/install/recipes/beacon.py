"""YTM Beacon on Linux, macOS and Windows (x86-64 and ARM).

Same as Beacon's own one-line installers: download the release for this system, check it against
the release's checksums.txt, unpack it, and let `ytm-beacon install` do the rest (copy itself into
place, unpack the browser extension, start at login, start now). Removing runs `ytm-beacon uninstall`.
No administrator rights anywhere.
"""
import re
from pathlib import Path

from ...net import HttpError, get_json
from ..engine import Failed, Step
from .base import Recipe, need, option

PORTS = range(41713, 41718)


class BeaconRecipe(Recipe):
    id = "ytm-beacon"
    name = "YTM Beacon"
    repo = "dash1101/ytm-beacon"
    blurb = "Show what you play on YouTube Music in your Discord status"
    glyph = "YB"
    color = "#f472b6"
    PLATFORMS = {
        "linux": ("full", ""),
        "darwin": ("full", ""),
        "windows": ("full", ""),
    }
    ports = PORTS
    wait_tries = 40

    # ---- where it lives -----------------------------------------------------------------
    def exe(self):
        if self.host.system == "windows":
            return self.host.local_app_data / "ytm-beacon" / "ytm-beacon.exe"
        return self.host.bin_dir / "ytm-beacon"

    def archive(self):
        osname = {"linux": "linux", "darwin": "darwin", "windows": "windows"}[self.host.system]
        ext = "zip" if self.host.system == "windows" else "tar.gz"
        return f"ytm-beacon_{osname}_{self.host.arch}.{ext}"

    def detect(self):
        exe = self.exe()
        if not exe.exists():
            other = self.host.which("ytm-beacon")
            if not other:
                return {"installed": False, "version": "", "where": "", "how": ""}
            exe = Path(other)
        rc, out = self.host.probe([exe, "version"])
        m = re.search(r"v?(\d+\.\d+\.\d+[^\s]*)", out or "")
        return {"installed": True, "version": m.group(1) if rc == 0 and m else "", "where": str(exe),
                "how": "its own installer"}

    # ---- what the window shows -----------------------------------------------------------
    def requirements(self, op):
        if op == "uninstall":
            return []
        return [need("Discord desktop app", True, detail="Beacon shows your status through the Discord app; "
                     "it reconnects whenever Discord starts, so it can be installed first.", blocking=False)]

    def options(self, op):
        if op == "uninstall":
            return [option("purge", "Also delete Beacon's settings and logs", "bool", False,
                           "Leave this off to keep your choices if you install it again.")]
        first = not self.installed()["installed"]
        return [option("setup", "Open the browser-extension setup when done", "bool", first,
                       "A page in your browser shows the three clicks that add Beacon's extension.")]

    def assets(self, rel):
        return [self.archive()]

    def summary(self, op, opts, rel):
        if op == "uninstall":
            return [f"Stop YTM Beacon and remove it from {self.exe().parent}",
                    "Stop it starting when you log in",
                    "Delete its settings and logs too" if opts.get("purge") else "Keep its settings",
                    "Remove the extension from your browser yourself afterwards (browsers only allow that by hand)"]
        v = rel["tag"] if rel else "the latest release"
        return [f"Download YTM Beacon {v} for {self.host.describe()['systemName']} from GitHub",
                "Check it against the checksums published with the release",
                f"Install it in {self.exe().parent} (just for you, no administrator password)",
                "Start it now and every time you log in"]

    def steps(self, op, opts):
        if op == "uninstall":
            return [Step("stop", "Stop YTM Beacon and remove it", self._uninstall, 3),
                    Step("check", "Make sure it's gone", self._gone, 1)]
        return [
            Step("find", "Find the latest release", self._find, 0.5),
            Step("download", "Download", self._download, 4),
            Step("verify", "Check the download", self._verify, 0.5),
            Step("unpack", "Unpack", self._unpack, 0.5),
            Step("install", "Install and start at login", self._install, 3),
            Step("start", "Wait for it to start", self._wait, 1),
        ]

    # ---- install ------------------------------------------------------------------------
    def _find(self, s):
        s.vars["rel"] = rel = s.release(self.repo)
        s.asset(rel, self.archive(), self.repo)
        s.asset(rel, "checksums.txt", self.repo)
        s.detail(f"{rel['tag']} · {self.archive()}")

    def _download(self, s):
        s.vars["file"] = s.download(s.vars["rel"], self.archive(), self.repo)

    def _verify(self, s):
        sums = s.fetch_text(s.vars["rel"], "checksums.txt", self.repo)
        s.verify(s.vars["file"], sums, self.archive())

    def _unpack(self, s):
        d = s.extract(s.vars["file"])
        s.vars["exe"] = s.find(d, "ytm-beacon.exe" if self.host.system == "windows" else "ytm-beacon")

    def _install(self, s):
        argv = [s.vars["exe"], "install"] + ([] if s.vars["opts"].get("setup") else ["--no-open"])
        s.detail("copying the program, the extension and start-at-login")
        s.run(argv, fail="Beacon's installer stopped with an error.",
              hint="The log below shows what it said. `ytm-beacon doctor` explains most problems.")

    def _wait(self, s):
        if s.dry_run:
            s.pause(0.5)
            return
        for _ in range(self.wait_tries):
            if self.port():
                return
            s.sleep(0.5)
        s.vars["slow"] = True
        s.log("Beacon hasn't answered yet; it may still be starting.")

    def port(self):
        for p in self.ports:
            try:
                if (get_json(f"http://127.0.0.1:{p}/v1/ping", timeout=0.6) or {}).get("app") == "ytm-beacon":
                    return p
            except HttpError:
                continue
        return None

    # ---- uninstall ----------------------------------------------------------------------
    def _uninstall(self, s):
        exe = self.installed().get("where") or str(self.exe())
        s.run([exe, "uninstall"] + (["--purge"] if s.vars["opts"].get("purge") else []),
              fail="Beacon's uninstaller stopped with an error.")

    def _gone(self, s):
        self.forget()
        if s.dry_run:
            return
        # On Windows a program can't delete itself while it runs: Beacon's uninstaller has it deleted a few
        # seconds after it ends, so give that a moment.
        for _ in range(30):
            if not self.exe().exists():
                return
            s.sleep(0.5)
        raise Failed("YTM Beacon's program is still there.", f"Delete {self.exe()} by hand.")

    def finish(self, sess, op, opts):
        if op == "uninstall":
            return {"headline": "YTM Beacon is removed.",
                    "body": "To finish, remove its extension from your browser's extensions page.",
                    "next": []}
        nxt = [{"id": "app", "label": "open in orbit", "kind": "app", "app": "ytm-beacon", "primary": True}]
        port = None if sess.dry_run else self.port()
        nxt.append({"id": "dash", "label": "open beacon's dashboard", "kind": "url",
                    "url": f"http://127.0.0.1:{port or self.ports[0]}/"})
        before = sess.vars.get("before") or {}
        body = ("Play a song on YouTube Music and look at your Discord profile."
                if before.get("installed") else
                "Add the browser extension (the setup page shows how), then play a song on YouTube Music.")
        if sess.vars.get("slow"):
            body = "It's installed but hasn't answered yet; give it a few seconds. " + body
        return {"headline": "YTM Beacon is installed and running." if not sess.vars.get("slow")
                else "YTM Beacon is installed.", "body": body, "next": nxt}
