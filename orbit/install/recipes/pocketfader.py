"""Pocketfader on Linux, Windows and macOS.

* **Linux** — the same layout as Pocketfader's own install.sh: its own Python environment in
  ~/.local/share/pocketfader (from the release's Python package), `pocketfader` and `pocketfader-gui`
  in ~/.local/bin, an apps-menu entry, and a systemd user service that starts at login.
* **Windows** — the release's standalone programs (no Python needed) in
  %LOCALAPPDATA%\\Programs\\Pocketfader, a Start-menu entry for the window, and the release's own
  install-exe.ps1 to start it at logon (a per-user scheduled task; no administrator rights).
* **macOS** — the Python package in ~/Library/Application Support/Pocketfader and a LaunchAgent.
  Pocketfader can't change the Mac's volume or control other players yet, so it runs with the
  YouTube Music remote only; the installer says so up front.
"""
import json
from pathlib import Path

from ..engine import SKIP, Failed, Step
from .base import Recipe, need, option

PORT = 8777
LABEL = "app.pocketfader.server"   # macOS LaunchAgent


class PocketfaderRecipe(Recipe):
    id = "pocketfader"
    name = "Pocketfader"
    repo = "dash1101/pocketfader"
    blurb = "Control this computer's volume and music from your phone"
    glyph = "PF"
    color = "#a78bfa"
    port = PORT
    wait_seconds = 30
    PLATFORMS = {
        "linux": ("full", ""),
        "windows": ("full", ""),
        "darwin": ("limited", "On a Mac, Pocketfader runs its YouTube Music remote. Changing the Mac's volume and "
                              "controlling other players aren't available on macOS yet."),
    }

    # ---- where it lives -----------------------------------------------------------------
    @property
    def sysname(self):
        return self.host.system

    def app_dir(self):
        h = self.host
        if h.system == "windows":
            return h.local_app_data / "Programs" / "Pocketfader"
        if h.system == "darwin":
            return h.local_app_data / "Pocketfader"
        return h.local_app_data / "pocketfader"

    def venv(self):
        return self.app_dir() / "venv"

    def venv_bin(self, name):
        return self.venv() / "bin" / name

    def settings_dir(self):
        if self.host.system == "windows":
            return self.host.config_home / "pocketfader"
        return Path(self.host.env.get("XDG_CONFIG_HOME") or self.host.home / ".config") / "pocketfader"

    def unit_path(self):
        return self.host.config_home / "systemd" / "user" / "pocketfader.service"

    def plist_path(self):
        return self.host.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def desktop_path(self):
        return self.host.local_app_data / "applications" / "pocketfader.desktop"

    def icon_path(self):
        return self.host.local_app_data / "icons" / "hicolor" / "scalable" / "apps" / "pocketfader.svg"

    def shortcut_path(self):
        return self.host.start_menu / "Pocketfader.lnk"

    def wheel_name(self, rel):
        for n in rel["assets"]:
            if n.startswith("pocketfader-") and n.endswith("-py3-none-any.whl"):
                return n
        raise Failed(f"The {rel['tag']} release doesn't include Pocketfader's Python package.")

    def zip_name(self, rel):
        for n in rel["assets"]:
            if n.startswith("pocketfader-") and n.endswith("-windows-x64.zip"):
                return n
        raise Failed(f"The {rel['tag']} release doesn't include the Windows program.")

    # ---- what's here --------------------------------------------------------------------
    def detect(self):
        if self.sysname == "windows":
            exe = self.app_dir() / "pocketfader.exe"
            if not exe.exists():
                return {"installed": False, "version": "", "where": "", "how": ""}
            try:
                v = json.loads((self.app_dir() / "orbit-install.json").read_text(encoding="utf-8")).get("version", "")
            except (OSError, ValueError):
                v = ""
            return {"installed": True, "version": v, "where": str(self.app_dir()), "how": "standalone program"}
        if self.venv_bin("pocketfader").exists():
            v = ""
            for info in sorted(self.venv().glob("lib/python*/site-packages/pocketfader-*.dist-info")):
                v = info.name[len("pocketfader-"):-len(".dist-info")]
            return {"installed": True, "version": v, "where": str(self.app_dir()), "how": "its own Python environment"}
        other = self.host.which("pocketfader")
        if other:
            return {"installed": True, "version": "", "where": other, "how": "installed another way"}
        return {"installed": False, "version": "", "where": "", "how": ""}

    def can_uninstall(self):
        inst = self.installed()
        if inst["installed"] and inst["how"] == "installed another way":
            return False, (f"Pocketfader at {inst['where']} was installed another way (with pip, or run from its "
                           "source folder), so Orbit leaves it alone. Remove it the way it was installed.")
        return True, ""

    def _existing_lan(self):
        try:
            if self.sysname == "linux":
                return "--lan" in self.unit_path().read_text(encoding="utf-8")
            if self.sysname == "darwin":
                return "<string>--lan</string>" in self.plist_path().read_text(encoding="utf-8")
        except OSError:
            pass
        return False

    def _python(self):
        """(python argv, version) for a Python 3.9+ with venv, or (None, why)."""
        for name in ("python3", "python"):
            exe = self.host.which(name)
            if not exe:
                continue
            rc, out = self.host.probe([exe, "-c", "import sys, venv, ensurepip; print('%d.%d' % sys.version_info[:2])"])
            if rc == 0:
                v = out.strip().splitlines()[-1] if out.strip() else ""
                try:
                    if tuple(int(x) for x in v.split(".")) >= (3, 9):
                        return exe, v
                    return None, f"Python {v} is too old"
                except ValueError:
                    pass
            else:
                return None, "venv missing"
        return None, "not found"

    # ---- what the window shows -----------------------------------------------------------
    def requirements(self, op):
        if op == "uninstall":
            return []
        h, out = self.host, []
        if h.system == "windows":
            return [need("Windows 10 or 11 (64-bit)", h.arch == "amd64",
                         "The Windows program is built for 64-bit Intel/AMD processors.")]
        py, v = self._python()
        if h.system == "linux":
            out.append(need("Python 3.9 or newer with venv", py, {
                "venv missing": "Install python3-venv (Debian/Ubuntu: sudo apt install python3-venv).",
                "not found": "Install Python 3 from your distribution (sudo apt install python3 python3-venv).",
            }.get(v, f"{v}; install a newer Python 3."), detail=f"Python {v}" if py else ""))
            out.append(need("pactl (sound control)", h.which("pactl"),
                            "Install pulseaudio-utils; it works with PipeWire too "
                            "(Debian/Ubuntu: sudo apt install pulseaudio-utils)."))
            out.append(need("busctl (control other music players)", h.which("busctl"),
                            "Part of systemd. Without it Pocketfader only controls volume and YouTube Music.",
                            blocking=False))
            out.append(need("systemd (start at login)", h.has_systemd,
                            "Without systemd Pocketfader installs, but you start it yourself with `pocketfader`.",
                            blocking=False))
        else:
            out.append(need("Python 3.9 or newer", py,
                            "Install Python 3 from python.org (or run `xcode-select --install`).",
                            detail=f"Python {v}" if py else ""))
        out.append(need("Tailscale (reach it from your phone anywhere)", h.which("tailscale") or self._tailscale_app(),
                        "Optional. Without it, turn on “home Wi-Fi” below, or use Pocketfader on this computer only.",
                        blocking=False))
        return out

    def _tailscale_app(self):
        if self.host.system == "darwin":
            return Path("/Applications/Tailscale.app").exists()
        if self.host.system == "windows":
            pf = Path(self.host.env.get("ProgramFiles") or "C:/Program Files")
            return (pf / "Tailscale" / "tailscale.exe").exists()
        return False

    def options(self, op):
        h = self.host
        if op == "uninstall":
            return [option("purge", "Also delete Pocketfader's settings", "bool", False,
                           f"Your settings are in {self.settings_dir()}. Leave this off to keep them.")]
        opts = []
        if h.system != "linux" or h.has_systemd:
            opts.append(option("autostart", "Start when I log in", "bool", True,
                               "Keeps the remote available without opening anything."))
        opts.append(option("lan", "Also reach it on my home Wi-Fi", "bool", self._existing_lan(),
                           "Lets phones on the same network connect. It then asks for an access token, which "
                           "Pocketfader shows you. Not needed with Tailscale."))
        if h.system != "windows":
            opts.append(option("ytdlp", "Let this computer fetch songs for the phone", "bool", True,
                               "Installs yt-dlp into Pocketfader's own environment, used when the phone can't "
                               "load a song by itself."))
        return opts

    def assets(self, rel):
        try:
            return [self.zip_name(rel)] if self.sysname == "windows" else [self.wheel_name(rel)]
        except Failed:
            return []

    def summary(self, op, opts, rel):
        h = self.host
        if op == "uninstall":
            lines = ["Stop Pocketfader and stop it starting at login", f"Remove {self.app_dir()}"]
            if h.system == "linux":
                lines.append("Remove its apps-menu entry and the pocketfader commands")
            if h.system == "windows":
                lines.append("Remove its Start-menu entry")
            lines.append("Delete your settings too" if opts.get("purge") else f"Keep your settings ({self.settings_dir()})")
            return lines
        v = rel["tag"] if rel else "the latest release"
        lines = [f"Download Pocketfader {v} from GitHub and check its checksum"]
        if h.system == "windows":
            lines.append(f"Put the program in {self.app_dir()} and add Pocketfader to the Start menu")
        else:
            lines.append(f"Install it with its own Python in {self.app_dir()}")
            if opts.get("ytdlp"):
                lines.append("Add yt-dlp so this computer can fetch songs for the phone (from PyPI)")
            if h.system == "linux":
                lines.append("Add Pocketfader to your apps menu")
        if opts.get("autostart", h.system == "linux" and h.has_systemd):
            lines.append("Start it now and whenever you log in")
        elif h.system == "linux" and not h.has_systemd:
            lines.append("You start it yourself with `pocketfader` (no systemd here)")
        else:
            lines.append("Start it now (not at login)")
        lines.append("Listen on this computer, Tailscale and your home Wi-Fi (with an access token)"
                     if opts.get("lan") else "Listen on this computer and Tailscale only")
        lines.append("No administrator password needed")
        return lines

    def steps(self, op, opts):
        sysname = self.sysname
        if op == "uninstall":
            if sysname == "windows":
                return [Step("stop", "Stop Pocketfader", self._win_stop, 1),
                        Step("task", "Stop it starting at logon", self._win_untask, 1),
                        Step("files", "Remove the program", self._remove_files, 1)]
            return [Step("stop", "Stop Pocketfader and its start-at-login", self._unix_unservice, 1),
                    Step("files", "Remove the program", self._remove_files, 1)]
        common = [Step("find", "Find the latest release", self._find, 0.5)]
        if sysname == "windows":
            return common + [
                Step("download", "Download", self._download, 5),
                Step("verify", "Check the download", self._verify, 0.5),
                Step("unpack", "Unpack", self._unpack, 1),
                Step("stop", "Stop the running copy", self._win_stop, 0.5),
                Step("copy", "Install the program", self._win_copy, 1),
                Step("shortcut", "Add it to the Start menu", self._win_shortcut, 0.5),
                Step("task", "Start at logon" if opts.get("autostart") else "Turn off start at logon",
                     self._win_task, 1),
                Step("start", "Start Pocketfader", self._win_start, 1.5),
            ]
        steps = common + [
            Step("download", "Download", self._download, 1.5),
            Step("verify", "Check the download", self._verify, 0.5),
            Step("venv", "Set up its own Python", self._venv, 2),
            Step("pip", "Install Pocketfader", self._pip, 2),
        ]
        if opts.get("ytdlp"):
            steps.append(Step("ytdlp", "Add yt-dlp", self._ytdlp, 3))
        if sysname == "linux":
            steps.append(Step("menu", "Add it to your apps menu", self._menu, 0.5))
            if self.host.has_systemd:
                steps.append(Step("service", "Start it now and at login" if opts.get("autostart") else "Start it now",
                                  self._systemd, 1))
                steps.append(Step("check", "Check that it answers", self._wait, 1))
        else:
            steps.append(Step("agent", "Start it now and at login" if opts.get("autostart") else "Start it now",
                              self._launchd, 1))
            steps.append(Step("check", "Check that it answers", self._wait, 1))
        return steps

    # ---- shared steps ---------------------------------------------------------------------
    def _find(self, s):
        s.vars["rel"] = rel = s.release(self.repo)
        s.vars["asset"] = self.zip_name(rel) if self.sysname == "windows" else self.wheel_name(rel)
        s.asset(rel, "SHA256SUMS.txt", self.repo)
        s.detail(f"{rel['tag']} · {s.vars['asset']}")

    def _download(self, s):
        s.vars["file"] = s.download(s.vars["rel"], s.vars["asset"], self.repo)

    def _verify(self, s):
        sums = s.fetch_text(s.vars["rel"], "SHA256SUMS.txt", self.repo)
        s.verify(s.vars["file"], sums, s.vars["asset"])

    def _args(self, s):
        args = []
        if self.sysname == "darwin":
            args += ["--audio", "none", "--media", "none"]
        if s.vars["opts"].get("lan"):
            args.append("--lan")
        return args

    def _wait(self, s):
        if not s.wait_http(f"http://127.0.0.1:{self.port}/api/info", seconds=self.wait_seconds):
            s.vars["slow"] = True
            s.log("Pocketfader hasn't answered yet; it may still be starting. Its log is in Orbit.")

    def _remove_files(self, s):
        h = self.host
        paths = [self.app_dir()]
        if h.system == "linux":
            paths += [h.bin_dir / "pocketfader", h.bin_dir / "pocketfader-gui", self.desktop_path(), self.icon_path()]
        if h.system == "windows":
            paths.append(self.shortcut_path())
        if s.vars["opts"].get("purge"):
            paths.append(self.settings_dir())
        s.remove(*paths)

    # ---- Linux and macOS ------------------------------------------------------------------
    def _venv(self, s):
        py, why = self._python()
        if not py:
            raise Failed(f"Python isn't ready ({why}).", "See “what this computer needs” and try again.")
        if (self.venv() / "bin" / "python").exists():
            s.detail("already there")
            return SKIP
        s.run([py, "-m", "venv", "--system-site-packages", self.venv()],
              fail="Couldn't create Pocketfader's Python environment.",
              hint="On Debian/Ubuntu install python3-venv first.")

    def _pip(self, s):
        s.detail(s.vars["asset"])
        s.run([self.venv_bin("python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
               "--no-input", "--no-deps", "--force-reinstall", s.vars["file"]],
              fail="Couldn't install Pocketfader into its Python environment.")

    def _ytdlp(self, s):
        s.detail("from PyPI")
        try:
            s.run([self.venv_bin("python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                   "--no-input", "--upgrade", "yt-dlp"])
        except Failed as e:
            s.log(f"Note: couldn't add yt-dlp ({e}); the phone will load songs on its own only.")
            s.detail("skipped: couldn't reach PyPI")
            return SKIP

    def _menu(self, s):
        h = self.host
        s.symlink(self.venv_bin("pocketfader"), h.bin_dir / "pocketfader")
        s.symlink(self.venv_bin("pocketfader-gui"), h.bin_dir / "pocketfader-gui")
        icon = None if s.dry_run else next(iter(self.venv().glob("lib/python*/site-packages/pocketfader/static/icon.svg")), None)
        if icon:
            s.write(self.icon_path(), icon.read_text(encoding="utf-8"))
        s.write(self.desktop_path(), "\n".join([
            "[Desktop Entry]", "Type=Application", "Name=Pocketfader",
            "Comment=Phone remote for your computer's volume and music",
            f"Exec={h.bin_dir / 'pocketfader-gui'}", "Icon=pocketfader", "Terminal=false",
            "Categories=AudioVideo;Audio;Utility;", ""]))

    def _systemd(self, s):
        sc = self.host.tool("systemctl")
        exec_start = " ".join([str(self.venv_bin("pocketfader"))] + self._args(s))
        s.write(self.unit_path(), "\n".join([
            "[Unit]", "Description=Pocketfader - phone remote for volume and music",
            "After=network-online.target sound.target", "", "[Service]", f"ExecStart={exec_start}",
            "Restart=on-failure", "RestartSec=3", "", "[Install]", "WantedBy=default.target", ""]))
        s.run([sc, "--user", "daemon-reload"])
        s.run([sc, "--user", "enable" if s.vars["opts"].get("autostart") else "disable", "pocketfader.service"])
        s.run([sc, "--user", "restart", "pocketfader.service"],
              fail="Pocketfader's service didn't start.", hint="Open Pocketfader in Orbit to read its log.")

    def _launchd(self, s):
        args = [str(self.venv_bin("pocketfader"))] + self._args(s)
        log = self.host.home / "Library" / "Logs" / "pocketfader.log"
        s.write(self.plist_path(), plist(LABEL, args, s.vars["opts"].get("autostart", True), log))
        lc, dom = self.host.tool("launchctl"), f"gui/{self.host.uid}"
        s.run([lc, "bootout", dom, self.plist_path()], ok=range(0, 256), quiet=True)
        s.run([lc, "bootstrap", dom, self.plist_path()], fail="macOS didn't start Pocketfader.")
        s.run([lc, "kickstart", f"{dom}/{LABEL}"], ok=range(0, 256), quiet=True)   # now, even when not at login

    def _unix_unservice(self, s):
        if self.sysname == "linux":
            if not self.host.has_systemd:
                return SKIP
            sc = self.host.tool("systemctl")
            s.run([sc, "--user", "disable", "--now", "pocketfader.service"], ok=range(0, 256))
            s.remove(self.unit_path())
            s.run([sc, "--user", "daemon-reload"], ok=range(0, 256))
        else:
            s.run([self.host.tool("launchctl"), "bootout", f"gui/{self.host.uid}", self.plist_path()],
                  ok=range(0, 256), quiet=True)
            s.remove(self.plist_path())

    # ---- Windows ------------------------------------------------------------------------
    def _ps(self, s, script, **kw):
        return s.run([self.host.tool("powershell"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-Command", script], **kw)

    def _unpack(self, s):
        d = s.extract(s.vars["file"])
        s.vars["src"] = s.find(d, "pocketfader.exe").parent

    def _win_stop(self, s):
        s.run([self.host.tool("schtasks"), "/End", "/TN", "Pocketfader"], ok=range(0, 256), quiet=True)
        for exe in ("pocketfader.exe", "pocketfader-gui.exe"):
            s.run([self.host.tool("taskkill"), "/IM", exe, "/F"], ok=range(0, 256), quiet=True)
        s.sleep(0.5)

    def _win_copy(self, s):
        s.copy_tree(s.vars["src"], self.app_dir())
        s.write(self.app_dir() / "orbit-install.json", json.dumps({"version": s.vars["rel"]["tag"].lstrip("v")}))

    def _win_shortcut(self, s):
        target = self.app_dir() / "pocketfader-gui.exe"
        self._ps(s, "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_str(self.shortcut_path()) + ");"
                    f"$s.TargetPath={_ps_str(target)};$s.WorkingDirectory={_ps_str(self.app_dir())};"
                    "$s.Description='Phone remote for your computer''s volume and music';$s.Save()",
                 fail="Couldn't add Pocketfader to the Start menu.")

    def _win_task(self, s):
        script = self.app_dir() / "install-exe.ps1"
        ps = self.host.tool("powershell")
        if s.vars["opts"].get("autostart"):
            s.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script]
                  + (["-Lan"] if s.vars["opts"].get("lan") else []),
                  fail="Couldn't set Pocketfader to start at logon.")
        else:
            s.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-Uninstall"],
                  ok=range(0, 256))

    def _win_start(self, s):
        if s.vars["opts"].get("autostart"):
            self._ps(s, "Start-ScheduledTask -TaskName Pocketfader", fail="Couldn't start Pocketfader.")
        else:
            args = " ".join(self._args(s)) or "--port 8777"
            self._ps(s, f"Start-Process -WindowStyle Hidden -FilePath {_ps_str(self.app_dir() / 'pocketfader.exe')} "
                        f"-ArgumentList {_ps_str(args)} -WorkingDirectory {_ps_str(self.app_dir())}",
                     fail="Couldn't start Pocketfader.")
        self._wait(s)

    def _win_untask(self, s):
        script = self.app_dir() / "install-exe.ps1"
        if script.exists() or s.dry_run:
            s.run([self.host.tool("powershell"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", script, "-Uninstall"], ok=range(0, 256))
        else:
            s.run([self.host.tool("schtasks"), "/Delete", "/TN", "Pocketfader", "/F"], ok=range(0, 256))

    # ---- done -----------------------------------------------------------------------------
    def finish(self, sess, op, opts):
        if op == "uninstall":
            return {"headline": "Pocketfader is removed.",
                    "body": "" if opts.get("purge") else f"Your settings are still in {self.settings_dir()}.",
                    "next": []}
        h = self.host
        nxt = [{"id": "app", "label": "open in orbit", "kind": "app", "app": "pocketfader", "primary": True},
               {"id": "web", "label": "open pocketfader", "kind": "url", "url": f"http://127.0.0.1:{PORT}/"}]
        if h.system == "linux" and h.has_systemd is False:
            body = "Start it with `pocketfader` in a terminal; it prints the address to open on your phone."
            head = "Pocketfader is installed."
        else:
            body = ("On your phone, open this computer's address on port 8777 (Pocketfader's window shows it, "
                    "with a QR code), then add it to your home screen.")
            head = "Pocketfader is installed and running."
        if sess.vars.get("slow"):
            head = "Pocketfader is installed."
            body = "It hasn't answered yet; give it a few seconds, or read its log in Orbit. " + body
        if h.system == "darwin":
            body += " On a Mac it runs the YouTube Music remote only."
        return {"headline": head, "body": body, "next": nxt}


def plist(label, args, run_at_load, log):
    def esc(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    items = "".join(f"\n        <string>{esc(a)}</string>" for a in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{esc(label)}</string>
    <key>ProgramArguments</key>
    <array>{items}
    </array>
    <key>RunAtLoad</key>
    <{'true' if run_at_load else 'false'}/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{esc(log)}</string>
    <key>StandardErrorPath</key>
    <string>{esc(log)}</string>
</dict>
</plist>
"""


def _ps_str(x):
    """A PowerShell single-quoted string literal."""
    return "'" + str(x).replace("'", "''") + "'"

