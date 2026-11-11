"""Nova's server: on Linux with systemd, and on Windows (a preview there). Not on macOS.

Mirrors Nova's own get.sh / get.ps1: read the release's SHA256SUMS, check its Ed25519 signature against
Nova's release key (pinned here, not fetched), download the package, check its checksum, then install it
as the administrator.

* Linux: the .deb on Debian/Ubuntu (so apt keeps track of it), the one-file installer elsewhere. The
  password is asked for by the desktop's own window (pkexec); Orbit never sees it. Removing is offered
  when apt installed it: `apt remove nova-server` keeps settings, `purge` doesn't.
* Windows: nova-windows.zip, whose installer (Nova\\windows\\install.ps1) Windows runs after asking for
  permission once. It puts Nova in C:\\Program Files\\Nova, with an uninstall.ps1 that Orbit uses to remove it.
"""
import re
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
WIN_ZIP = "nova-windows.zip"
SETUP_GUI = "/usr/lib/nova-api/setup-desktop"   # Nova's own setup window (asks for the password itself)
NOVA_GUI = "/usr/sbin/nova-gui"                 # the Nova Server window: status, pairing, devices, approvals


def check_signature(sums_text, sig_text, release_key):
    """SHA256SUMS signed with Nova's release key (Ed25519, base64 in SHA256SUMS.sig)? Raises Failed when not."""
    import base64
    import binascii
    from ... import ed25519
    try:
        raw = base64.b64decode(sig_text.strip(), validate=True)
    except (ValueError, binascii.Error):
        raise Failed("The release's signature file is damaged, so it won't be installed.") from None
    if not ed25519.verify(ed25519.key_from_pem(release_key), sums_text.encode("utf-8"), raw):
        raise Failed("The release's signature doesn't match Nova's release key, so it won't be installed.",
                     "Don't install this download. Check the Nova repository for news.")


class NovaRecipe(Recipe):
    id = "nova"
    name = "Nova"
    repo = "dash1101/Nova"
    blurb = "Secure remote for your home server"
    glyph = "NV"
    color = "#5ec4b6"
    PLATFORMS = {
        "linux": ("full", ""),
        "darwin": ("none", "Nova's server isn't available on macOS yet; it runs on Linux. From a Mac you can still "
                           "use Nova web in your browser, or the Nova app on Android, to reach a Nova server."),
        "windows": ("limited", "A preview on Windows 10 and 11 and Windows Server 2019 or newer: pairing, devices, "
                               "approvals, live graphs, alerts, the task manager, power and Docker Desktop work; "
                               "programs and updates, files, the terminal and backups aren't there yet."),
    }
    release_key = RELEASE_KEY
    version_file = VERSION_FILE
    setup_gui, nova_gui = SETUP_GUI, NOVA_GUI
    port = PORT
    plain_port = 8095
    start_tries = 30

    def support(self):
        sup = super().support()
        if sup["level"] != "none" and self.host.system == "linux" and not self.host.has_systemd:
            return {"level": "none", "note": "Nova runs as a systemd service, and this Linux doesn't use systemd."}
        return sup

    @property
    def windows(self):
        return self.host.system == "windows"

    @property
    def win_dir(self):
        """Where Nova's Windows installer puts it."""
        return Path(self.host.env.get("ProgramFiles") or "C:\\Program Files") / "Nova"

    @property
    def win_shortcut(self):
        """The "Nova Server" window in everyone's Start menu."""
        base = Path(self.host.env.get("ProgramData") or "C:\\ProgramData")
        return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Nova Server.lnk"

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
        if self.windows:
            try:
                v = (self.win_dir / "lib" / "VERSION").read_text(encoding="utf-8").strip()
            except OSError:
                return {"installed": False, "version": "", "where": "", "how": ""}
            return {"installed": True, "version": v, "where": str(self.win_dir), "how": "Nova's Windows installer"}
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
        if self.windows:
            if (self.win_dir / "uninstall.ps1").is_file():
                return True, ""
            return False, f"Nova's uninstaller isn't in {self.win_dir}, so Orbit can't remove it."
        if self._dpkg_version():
            return True, ""
        return False, ("Nova was installed with its one-file installer, so there's no package to remove. "
                       "Run `sudo nova help uninstall` in a terminal for the steps.")

    def needs_admin(self, op):
        if self.windows:
            return "Windows asks once"
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
        if self.windows:
            return WIN_ZIP, WIN_ZIP
        if self.host.debian_like:
            for n in sums_names:
                if re.fullmatch(r"nova-server_.*_all\.deb", n):
                    return n, n.replace("~", ".")
        return "nova-installer.run", "nova-installer.run"

    # ---- what the window shows -----------------------------------------------------------
    def requirements(self, op):
        if self.windows:
            ps = self.host.which("powershell")
            return [need("Windows PowerShell (it comes with Windows)", ps,
                         "Orbit runs Nova's installer with PowerShell, and can't find powershell.exe.")]
        way = self._admin_way()
        out = [need("Linux with systemd", True, detail=self.host.distro),
               need("A way to ask for the administrator password", way,
                    "Install pkexec (polkit), or run the installer from a desktop session with a terminal app.",
                    detail={"window": "the system's password window", "terminal": "sudo in a terminal"}.get(way, ""))]
        if op == "install":
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
        if self.windows:
            return [option("firewall", "Let your other devices reach Nova", "bool", True,
                           "Adds two Windows Firewall rules that let in your home network and Tailscale, nothing "
                           "else. Without them only this computer can reach Nova, and a phone can't pair over Wi-Fi.")]
        return []

    def assets(self, rel):
        _sum, gh = self._package(list(rel["assets"]))
        return [gh, "SHA256SUMS", "SHA256SUMS.sig"]

    def summary(self, op, opts, rel):
        if self.windows:
            return self._win_summary(op, opts, rel)
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

    def _win_summary(self, op, opts, rel):
        if op == "uninstall":
            return [f"Stop Nova and remove it with its own uninstaller ({self.win_dir}\\uninstall.ps1)",
                    "Delete its settings and paired devices too" if opts.get("purge")
                    else "Keep its settings and paired devices (in C:\\ProgramData\\Nova) for a reinstall",
                    "Your containers and files stay as they are"]
        v = rel["tag"] if rel else "the latest release"
        first = not self.installed()["installed"]
        out = [f"Download Nova {v} for Windows (nova-windows.zip) from GitHub",
               "Check the release's signature with Nova's release key, then the download's checksum",
               f"Run Nova's installer as the administrator: Nova goes to {self.win_dir}, gets its own Python "
               "environment (Python itself is installed with winget if it's missing) and three background tasks "
               "that start with Windows"]
        if opts.get("firewall", True):
            out.append("Let your home network and Tailscale reach Nova through Windows Firewall")
        out.append("Start Nova" + ("; then pair your phone from Nova Server in the Start menu" if first
                                   else " again, keeping its settings, paired devices and certificate"))
        return out

    def steps(self, op, opts):
        if self.windows:
            if op == "uninstall":
                return [Step("remove", "Remove Nova", self._win_remove, 3),
                        Step("check", "Make sure it's gone", self._win_gone, 0.5)]
            return [
                Step("find", "Find the latest release", self._find, 0.5),
                Step("signature", "Check the release's signature", self._signature, 1),
                Step("download", "Download", self._download, 2),
                Step("verify", "Check the download", self._verify, 0.5),
                Step("unpack", "Unpack", self._win_unpack, 0.5),
                Step("install", "Install as administrator", self._win_install, 6),
                Step("start", "Check that Nova started", self._started, 1),
            ]
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
        check_signature(sums, sig, self.release_key)
        s.log("Release signature checks out (Nova's release key).")
        s.detail("signed with Nova's release key")

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

    # ---- Windows ----------------------------------------------------------------------------
    def _win_unpack(self, s):
        dest = s.extract(s.vars["file"])
        script = Path(dest) / "Nova" / "windows" / "install.ps1"
        if not s.dry_run and not script.is_file():
            found = s.find(dest, "install.ps1")      # the zip's layout changed: look for it
            script = found
        s.vars["script"] = script

    def _win_install(self, s):
        args = [] if s.vars["opts"].get("firewall", True) else ["-NoFirewall"]
        s.admin_windows(s.vars["script"], args, "install Nova",
                        hint="The log below shows what Nova's installer said. Its own logs are in "
                             "C:\\ProgramData\\Nova\\log.")
        self.forget()
        if not s.dry_run and not self.detect()["installed"]:
            raise Failed("Nova's installer finished, but Nova isn't in its folder.",
                         "The log below shows what the installer said.")

    def _win_remove(self, s):
        args = ["-Purge"] if s.vars["opts"].get("purge") else []
        s.admin_windows(self.win_dir / "uninstall.ps1", args, "remove Nova")

    def _win_gone(self, s):
        self.forget()
        if not s.dry_run and (self.win_dir / "lib" / "VERSION").exists():
            raise Failed(f"Some of Nova is still in {self.win_dir}.",
                         "Something was still using it. Restart the computer, then delete that folder.")

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
        s.detail("waiting for Nova to answer")
        urls = [f"https://127.0.0.1:{self.port}/api/v1/ping"]
        if self.windows:     # there the plain port is the one that answers on the computer itself
            urls.insert(0, f"http://127.0.0.1:{self.plain_port}/api/v1/ping")
        for _ in range(self.start_tries):
            for url in urls:
                try:
                    if (get_json(url, timeout=1, insecure=True) or {}).get("ok"):
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
        if self.windows:
            return self._win_finish(sess, op, opts)
        if op == "uninstall":
            return {"headline": "Nova is removed.",
                    "body": "Your containers, files and backups are untouched." +
                            ("" if opts.get("purge") else " Its settings are kept for a reinstall."),
                    "next": []}
        first = not (sess.vars.get("before") or {}).get("installed")
        nxt = []
        if first:
            # Nova 0.9.16 and newer have windows for both; older ones are set up in a terminal
            if Path(self.setup_gui).exists():
                nxt.append({"id": "setup", "label": "set up nova", "kind": "program", "argv": [self.setup_gui],
                            "primary": True})
            else:
                nxt.append({"id": "setup", "label": "set up nova", "kind": "terminal", "argv": ["sudo", "nova", "setup"],
                            "primary": True})
            if Path(self.nova_gui).exists():
                nxt.append({"id": "pair", "label": "pair a phone", "kind": "program", "argv": [self.nova_gui]})
            else:
                nxt.append({"id": "pair", "label": "pair a phone", "kind": "terminal", "argv": ["sudo", "nova", "add"]})
        nxt.append({"id": "app", "label": "open in orbit", "kind": "app", "app": "nova", "primary": not first})
        body = ("Next, set it up (a few questions in a terminal), then pair your phone with the Nova app."
                if first else "Nova's services were restarted on the new version.")
        return {"headline": "Nova is installed." if first else "Nova is updated.", "body": body, "next": nxt}

    def _win_finish(self, sess, op, opts):
        if op == "uninstall":
            return {"headline": "Nova is removed.",
                    "body": "Its tasks, firewall rules and Start menu entry are gone." +
                            (" Its settings and paired devices too." if opts.get("purge") else
                             " Its settings and paired devices are kept in C:\\ProgramData\\Nova for a reinstall."),
                    "next": []}
        first = not (sess.vars.get("before") or {}).get("installed")
        nxt = [{"id": "gui", "label": "open nova server", "kind": "shortcut", "path": str(self.win_shortcut),
                "primary": first},
               {"id": "app", "label": "open in orbit", "kind": "app", "app": "nova", "primary": not first}]
        body = ("Next, pair your phone: open Nova Server, press “Pair a phone or tablet” and scan the code with the "
                "Nova app." if first else "Its settings, paired devices and certificate are kept.")
        if sess.vars.get("slow"):
            body += " Nova hasn't answered yet; give it a minute, or look in C:\\ProgramData\\Nova\\log."
        return {"headline": "Nova is installed." if first else "Nova is updated.", "body": body, "next": nxt}
