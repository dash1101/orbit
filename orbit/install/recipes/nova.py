"""Nova's server, on Linux with systemd (it isn't made for Windows or macOS).

Mirrors Nova's own get.sh: read the release's SHA256SUMS, check its ed25519 signature against Nova's
release key (pinned here, not fetched), download the package, check its checksum, then install it as
the administrator — the .deb on Debian/Ubuntu (so apt keeps track of it), the one-file installer
elsewhere. The password is asked for by the desktop's own window (pkexec); Orbit never sees it.
Removing is offered when apt installed it: `apt remove nova-server` keeps settings, `purge` doesn't.
"""
import re
import subprocess
from pathlib import Path

from ...net import HttpError, get_json
from ..engine import Failed, Step
from .base import Recipe, need, option

# packaging/release-key.pub in the Nova repository
RELEASE_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAGk70hAkKUc/oE8yLsTv49yeIkSJkCeuDG0IqxnF69WI=
-----END PUBLIC KEY-----
"""
VERSION_FILE = Path("/usr/lib/nova-api/VERSION")
PORT = 8495


class NovaRecipe(Recipe):
    id = "nova"
    name = "Nova"
    repo = "dash1101/Nova"
    blurb = "Secure remote for your Linux home server"
    glyph = "NV"
    color = "#5ec4b6"
    PLATFORMS = {
        "linux": ("full", ""),
        "darwin": ("none", "Nova's server isn't available on macOS yet; it runs on Linux. From a Mac you can still "
                           "use Nova web in your browser, or the Nova app on Android, to reach a Nova server."),
        "windows": ("none", "Nova's server isn't available on Windows yet; it runs on Linux. From Windows you can "
                            "still use Nova web in your browser, or the Nova app on Android, to reach a Nova server."),
    }
    release_key = RELEASE_KEY
    version_file = VERSION_FILE
    port = PORT
    start_tries = 30

    def support(self):
        sup = super().support()
        if sup["level"] != "none" and not self.host.has_systemd:
            return {"level": "none", "note": "Nova runs as a systemd service, and this Linux doesn't use systemd."}
        return sup

    # ---- what's here --------------------------------------------------------------------
    def _dpkg_version(self):
        dq = self.host.which("dpkg-query")
        if not dq:
            return ""
        rc, out = self.host.probe([dq, "-W", "-f=${Status}|${Version}", "nova-server"])
        if rc == 0 and "install ok installed" in out:
            return out.rsplit("|", 1)[-1].strip()
        return ""

    def detect(self):
        if self.host.system != "linux":
            return {"installed": False, "version": "", "where": "", "how": ""}
        deb = self._dpkg_version()
        try:
            v = self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            v = ""
        if not v and not deb:
            return {"installed": False, "version": "", "where": "", "how": ""}
        return {"installed": True, "version": v or deb, "where": "/usr/lib/nova-api",
                "how": "Debian package" if deb else "Nova's installer"}

    def can_uninstall(self):
        if self._dpkg_version():
            return True, ""
        return False, ("Nova was installed with its one-file installer, so there's no package to remove. "
                       "Run `sudo nova help uninstall` in a terminal for the steps.")

    def needs_admin(self, op):
        return {"window": "in the system's password window", "terminal": "in a terminal window (sudo)"}.get(
            self._admin_way(), "the system will ask")

    def _admin_way(self):
        if self.host.which("pkexec") and self.host.has_display:
            return "window"
        from ...runner import terminal_argv
        if self.host.which("sudo") and terminal_argv(["true"]):
            return "terminal"
        return ""

    def _package(self, sums_names):
        """(name in SHA256SUMS, file name on GitHub)."""
        if self.host.debian_like:
            for n in sums_names:
                if re.fullmatch(r"nova-server_.*_all\.deb", n):
                    return n, n.replace("~", ".")
        return "nova-installer.run", "nova-installer.run"

    # ---- what the window shows -----------------------------------------------------------
    def requirements(self, op):
        way = self._admin_way()
        out = [need("Linux with systemd", True, detail=self.host.distro),
               need("A way to ask for the administrator password", way,
                    "Install pkexec (polkit), or run the installer from a desktop session with a terminal app.",
                    detail={"window": "the system's password window", "terminal": "sudo in a terminal"}.get(way, ""))]
        if op == "install":
            out.append(need("Release signature check (openssl)", self.host.which("openssl"),
                            "Without openssl, Orbit checks the download's checksum only.", blocking=False))
            if not self.host.debian_like:
                out.append(need("Debian or Ubuntu (apt keeps Nova updated)", False,
                                "Other distributions use Nova's one-file installer; it installs what Nova needs "
                                "with dnf, pacman or zypper.", blocking=False))
        return out

    def options(self, op):
        if op == "uninstall":
            return [option("purge", "Also delete Nova's settings and paired devices", "bool", False,
                           "Leave this off to keep them for a later reinstall. Your containers, files and backups "
                           "are never touched either way.")]
        return []

    def assets(self, rel):
        _sum, gh = self._package(list(rel["assets"]))
        return [gh, "SHA256SUMS", "SHA256SUMS.sig"]

    def summary(self, op, opts, rel):
        if op == "uninstall":
            return [f"Remove the Nova server with apt ({'purge' if opts.get('purge') else 'remove'} nova-server)",
                    "Delete its settings and paired devices too" if opts.get("purge")
                    else "Keep its settings and paired devices",
                    "Your containers, files and backups stay as they are",
                    "Used Cloudflare auto-setup? Run `sudo nova cloudflare undo` first"]
        v = rel["tag"] if rel else "the latest release"
        pkg = "the Debian package (apt keeps it updated)" if self.host.debian_like else "Nova's one-file installer"
        inst = self.installed()
        return [f"Download Nova {v} from GitHub: {pkg}",
                "Check the release's signature with Nova's release key, then the download's checksum",
                "Install it as the administrator (system-wide, as a service)",
                "Start Nova's services" + ("" if inst["installed"] else "; then you set it up and pair your phone")]

    def steps(self, op, opts):
        if op == "uninstall":
            return [Step("remove", "Remove Nova", self._remove, 3), Step("check", "Make sure it's gone", self._gone, 1)]
        return [
            Step("find", "Find the latest release", self._find, 0.5),
            Step("signature", "Check the release's signature", self._signature, 1),
            Step("download", "Download", self._download, 3),
            Step("verify", "Check the download", self._verify, 0.5),
            Step("install", "Install as administrator", self._install, 4),
            Step("start", "Check that Nova started", self._started, 1),
        ]

    # ---- install --------------------------------------------------------------------------
    def _find(self, s):
        s.vars["rel"] = rel = s.release(self.repo)
        s.asset(rel, "SHA256SUMS", self.repo)
        s.detail(rel["tag"])

    def _signature(self, s):
        rel = s.vars["rel"]
        s.vars["sums"] = sums = s.fetch_text(rel, "SHA256SUMS", self.repo)
        sig = s.fetch_text(rel, "SHA256SUMS.sig", self.repo) if "SHA256SUMS.sig" in rel["assets"] else ""
        sums_names = [ln.split(None, 1)[1].strip().lstrip("*") for ln in sums.splitlines() if len(ln.split(None, 1)) == 2]
        s.vars["pkg"] = self._package(sums_names) if not s.dry_run else self._package(
            [n.replace(".alpha", "~alpha") for n in rel["assets"]])
        if s.dry_run:
            s.detail("Nova's release key (pinned in Orbit)")
            s.pause(0.3)
            return
        if not sig:
            raise Failed("This Nova release isn't signed, so Orbit won't install it.")
        openssl = self.host.which("openssl")
        if not openssl:
            s.log("openssl isn't installed, so only the download's checksum is checked.")
            s.detail("skipped: no openssl here (the checksum is still checked)")
            return
        import base64
        try:
            raw = base64.b64decode(sig.strip(), validate=False)
        except ValueError:
            raise Failed("The release's signature file is damaged.") from None
        (s.tmp / "SHA256SUMS").write_text(sums, encoding="utf-8")
        (s.tmp / "sig.bin").write_bytes(raw)
        (s.tmp / "release-key.pub").write_text(self.release_key, encoding="utf-8")
        p = subprocess.run([openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(s.tmp / "release-key.pub"),
                            "-rawin", "-in", str(s.tmp / "SHA256SUMS"), "-sigfile", str(s.tmp / "sig.bin")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if p.returncode == 0:
            s.log("Release signature checks out (Nova's release key).")
            s.detail("signed with Nova's release key")
            return
        h = subprocess.run([openssl, "pkeyutl", "-help"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if "rawin" not in (h.stdout + h.stderr):
            s.log("This openssl can't check ed25519 signatures; only the download's checksum is checked.")
            s.detail("skipped: this openssl is too old (the checksum is still checked)")
            return
        raise Failed("The release's signature doesn't match Nova's release key, so it won't be installed.",
                     "Don't install this download. Check the Nova repository for news.")

    def _download(self, s):
        _sum_name, gh_name = s.vars["pkg"]
        s.vars["file"] = s.download(s.vars["rel"], gh_name, self.repo, dest_name=gh_name)

    def _verify(self, s):
        sum_name, _gh = s.vars["pkg"]
        s.verify(s.vars["file"], s.vars["sums"], sum_name)

    def _installed_version(self):
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _install(self, s):
        f = s.vars["file"]
        target = s.vars["rel"]["tag"].lstrip("vV").replace("-", "~")
        before = self._installed_version()

        def done():
            v = self._installed_version()
            return bool(v) and (v == target or v != before)

        if str(f).endswith(".deb"):
            argv = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", f]
        else:
            argv = ["sh", f]
        s.admin(argv, "install Nova", wait_for=done,
                hint="The log below shows what the installer said.")

    def _started(self, s):
        if s.dry_run:
            s.pause(0.4)
            return
        s.detail("waiting for nova-api")
        for _ in range(self.start_tries):
            try:
                if (get_json(f"https://127.0.0.1:{self.port}/api/v1/ping", timeout=1, insecure=True) or {}).get("ok"):
                    return
            except HttpError:
                pass
            s.sleep(1)
        s.vars["slow"] = True
        s.log("Nova hasn't answered on this computer yet. It starts answering once it's set up.")

    # ---- uninstall ------------------------------------------------------------------------
    def _remove(self, s):
        verb = "purge" if s.vars["opts"].get("purge") else "remove"
        s.admin(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", verb, "-y", "nova-server"], "remove Nova",
                wait_for=lambda: not self._dpkg_version())

    def _gone(self, s):
        self.forget()
        if not s.dry_run and self._dpkg_version():
            raise Failed("apt still lists nova-server as installed.", "Run `sudo apt remove nova-server` in a terminal.")

    # ---- done -----------------------------------------------------------------------------
    def finish(self, sess, op, opts):
        if op == "uninstall":
            return {"headline": "Nova is removed.",
                    "body": "Your containers, files and backups are untouched." +
                            ("" if opts.get("purge") else " Its settings are kept for a reinstall."),
                    "next": []}
        first = not (sess.vars.get("before") or {}).get("installed")
        nxt = []
        if first:
            nxt.append({"id": "setup", "label": "set up nova", "kind": "terminal", "argv": ["sudo", "nova", "setup"],
                        "primary": True})
            nxt.append({"id": "pair", "label": "pair a phone", "kind": "terminal", "argv": ["sudo", "nova", "add"]})
        nxt.append({"id": "app", "label": "open in orbit", "kind": "app", "app": "nova", "primary": not first})
        body = ("Next, set it up (a few questions in a terminal), then pair your phone with the Nova app."
                if first else "Nova's services were restarted on the new version.")
        return {"headline": "Nova is installed." if first else "Nova is updated.", "body": body, "next": nxt}
