"""YTM Starcast on Linux, Windows and macOS, and the move to it from Pocketfader and YTM Beacon.

* **Linux**: the same layout as YTM Starcast's own install.sh: its own Python environment in
  ~/.local/share/ytm-starcast (from the release's Python package), `ytm-starcast` and `ytm-starcast-gui` in
  ~/.local/bin, an apps-menu entry, and a systemd user service that starts at login.
* **Windows**: the release's standalone programs (no Python needed) in "%LOCALAPPDATA%\\Programs\\YTM Starcast", a
  Start-menu entry for the window, and the release's own install-exe.ps1 to start it at logon (a per-user scheduled
  task; no administrator rights).
* **macOS**: the Python package in "~/Library/Application Support/YTM Starcast" and a LaunchAgent. YTM Starcast can't
  change the Mac's volume or control other players yet, so it runs without them; the installer says so up front.

**The move.** YTM Starcast is what Pocketfader and YTM Beacon became. When this computer still has Pocketfader (Orbit's
layout or its install.sh: the same places under the old name) or the YTM Beacon program, installing YTM Starcast also
moves it over: the new program is put in place first, then the old one is stopped (both use the same port and both
would set the Discord status), YTM Beacon is removed with its own uninstaller (which keeps its settings; an install
option, on unless YTM Starcast is already installed next to it or on a Mac), YTM Starcast is started, and only once it
answers are Pocketfader's old files removed (on Windows with start at logon, the release's install-exe.ps1 already
removes Pocketfader's folder and Start-menu entry while it sets up the logon task). On Linux without systemd, a
Pocketfader its desktop window or its login entry started is stopped as well. Settings are never copied or deleted
here: YTM Starcast takes over Pocketfader's and YTM Beacon's settings itself when it first starts.
"""
import json
import os
import re
import signal
from pathlib import Path

from ..engine import SKIP, Failed, Step
from .base import Recipe, _version, need, option

PORT = 8777
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"   # where a desktop window's "start at login" goes
# The desktop window's id (its GTK application id and window class). The apps-menu entry names it as StartupWMClass,
# so the open window shows with YTM Starcast's name and icon.
WINDOW_ID = "app.novalabs.starcast"


class Names:
    """Where one name of the app lives: YTM Starcast's, or Pocketfader's from before the move."""

    def __init__(self, slug, module, dists, win_dir, task, label, display):
        self.slug = slug          # commands, settings folder, Linux folder, menu entry, icon and service
        self.module = module      # Python import package (its icon is in <module>/static)
        self.dists = dists        # Python distribution name as wheels and dist-info folders spell it (pip writes
                                  # the hyphen as an underscore; both spellings are accepted)
        self.win_dir = win_dir    # folder on Windows and macOS, Start-menu entry (may have a space: always quote it)
        self.task = task          # Windows scheduled task (and a desktop window's start-at-login value)
        self.label = label        # macOS LaunchAgent
        self.display = display

    @property
    def unit(self):
        return f"{self.slug}.service"


NEW = Names("ytm-starcast", "starcast", ("ytm_starcast", "ytm-starcast"), "YTM Starcast", "YTM Starcast",
            "app.novalabs.starcast.server", "YTM Starcast")
OLD = Names("pocketfader", "pocketfader", ("pocketfader",), "Pocketfader", "Pocketfader", "app.pocketfader.server",
            "Pocketfader")
# Options of the server that an update keeps from the install before it (as YTM Starcast's own install.sh does);
# --lan isn't one of them: it follows the installer's "home Wi-Fi" choice.
KEPT = ("--token", "--port", "--bind", "--allow-host", "--audio", "--eq", "--media")


def kept_options(words):
    """The kept options in a server command line (after the program itself)."""
    out, i = [], 1
    while i < len(words):
        w = words[i]
        if w in KEPT and i + 1 < len(words):
            out += [w, words[i + 1]]
            i += 2
            continue
        if any(w.startswith(k + "=") for k in KEPT):
            out.append(w)
        i += 1
    return out


MOVED = "Pocketfader (its old name)"      # how detect() describes a Pocketfader that is still the install here
OTHER_WAY = "installed another way"


class StarcastRecipe(Recipe):
    id = "ytm-starcast"
    name = "YTM Starcast"
    repo = "dash1101/ytm-starcast"
    blurb = "YouTube Music companion: a remote for your phone, your Discord status and a better YouTube Music"
    glyph = "S"
    color = "#38d9c4"
    port = PORT
    wait_seconds = 30
    beacon_wait = 15          # seconds YTM Beacon's program gets to go away after its uninstaller ends
    stop_wait = 5             # seconds a server started without a service manager gets to end once asked
    proc = Path("/proc")      # Linux: where a running program's command line is read before it is stopped
    PLATFORMS = {
        "linux": ("full", ""),
        "windows": ("full", ""),
        "darwin": ("limited", "Experimental: YTM Starcast isn't tested on macOS yet. Its YouTube Music remote and your "
                              "Discord status are meant to work there; changing the Mac's volume and controlling other "
                              "players aren't available on macOS."),
    }

    # ---- where it lives -----------------------------------------------------------------
    def app_dir(self, n=NEW):
        h = self.host
        if h.system == "windows":
            return h.local_app_data / "Programs" / n.win_dir
        if h.system == "darwin":
            return h.local_app_data / n.win_dir
        return h.local_app_data / n.slug

    def venv(self, n=NEW):
        return self.app_dir(n) / "venv"

    def venv_bin(self, name, n=NEW):
        return self.venv(n) / "bin" / name

    def settings_dir(self, n=NEW):
        if self.host.system == "windows":
            return self.host.config_home / n.slug
        return Path(self.host.env.get("XDG_CONFIG_HOME") or self.host.home / ".config") / n.slug

    def unit_path(self, n=NEW):
        return self.host.config_home / "systemd" / "user" / n.unit

    def plist_path(self, n=NEW):
        return self.host.home / "Library" / "LaunchAgents" / f"{n.label}.plist"

    def desktop_path(self, n=NEW):
        return self.host.local_app_data / "applications" / f"{n.slug}.desktop"

    def icon_path(self, n=NEW):
        return self.host.local_app_data / "icons" / "hicolor" / "scalable" / "apps" / f"{n.slug}.svg"

    def shortcut_path(self, n=NEW):
        return self.host.start_menu / f"{n.win_dir}.lnk"

    def links(self, n=NEW):
        return [self.host.bin_dir / n.slug, self.host.bin_dir / f"{n.slug}-gui"]

    def autostart_path(self, n=NEW):
        """Linux without systemd: the login entry that starts it (what its own install.sh writes then)."""
        return self.host.config_home / "autostart" / f"{n.slug}.desktop"

    # YTM Beacon, where its own installer put it
    def beacon_exe(self):
        if self.host.system == "windows":
            return self.host.local_app_data / "ytm-beacon" / "ytm-beacon.exe"
        return self.host.bin_dir / "ytm-beacon"

    def beacon_found(self):
        exe = self.beacon_exe()
        return str(exe) if exe.exists() else (self.host.which("ytm-beacon") or "")

    def beacon_dirs(self):
        """YTM Beacon's settings folder and its data folder (one folder on a Mac; on Windows it also held the program)."""
        return [self.host.config_home / "ytm-beacon", self.host.local_app_data / "ytm-beacon"]

    def wheel_name(self, rel):
        for n in rel["assets"]:
            if n.startswith(tuple(d + "-" for d in NEW.dists)) and n.endswith("-py3-none-any.whl"):
                return n
        raise Failed(f"The {rel['tag']} release doesn't include YTM Starcast's Python package.")

    def zip_name(self, rel):
        for n in rel["assets"]:
            if n.startswith(NEW.slug + "-") and n.endswith("-windows-x64.zip"):
                return n
        raise Failed(f"The {rel['tag']} release doesn't include the Windows program.")

    # ---- what's here --------------------------------------------------------------------
    def _layout(self, n):
        """(folder, version) of `n` installed in the place Orbit (and the app's own installer) puts it, or None."""
        if self.host.system == "windows":
            if not (self.app_dir(n) / f"{n.slug}.exe").exists():
                return None
            try:
                v = json.loads((self.app_dir(n) / "orbit-install.json").read_text(encoding="utf-8")).get("version", "")
            except (OSError, ValueError, AttributeError):
                v = ""
            return str(self.app_dir(n)), str(v or "")
        if not any(self.venv_bin(x, n).exists() for x in (n.slug, NEW.slug)):
            return None
        return str(self.app_dir(n)), self._venv_version(n)

    def _venv_version(self, n):
        """YTM Starcast's version in that environment, else Pocketfader's (a Pocketfader that updated itself has
        both)."""
        for dist in dict.fromkeys(NEW.dists + n.dists):
            found = sorted(self.venv(n).glob(f"lib/python*/site-packages/{dist}-*.dist-info"))
            if found:
                return found[-1].name[len(dist) + 1:-len(".dist-info")]
        return ""

    def detect(self):
        new, old, beacon = self._layout(NEW), self._layout(OLD), self.beacon_found()
        if new:
            d = {"installed": True, "version": new[1], "where": new[0],
                 "how": "standalone program" if self.host.system == "windows" else "its own Python environment"}
        elif old:
            d = {"installed": True, "version": old[1], "where": old[0], "how": MOVED}
        else:
            other = self.host.which(NEW.slug) or self.host.which(OLD.slug)
            d = ({"installed": True, "version": "", "where": other, "how": OTHER_WAY} if other else
                 {"installed": False, "version": "", "where": "", "how": ""})
        d["legacy"] = {"pocketfader": old[0] if old else "", "beacon": beacon}
        # a move is due: Pocketfader is still the install here, or YTM Beacon is and YTM Starcast isn't yet. A YTM
        # Beacon next to an installed YTM Starcast (kept on purpose, or its uninstaller couldn't remove it) is no reason
        # to offer an update: YTM Starcast's page in Orbit removes it on its own, and an install only does when asked.
        d["migrate"] = d["how"] != OTHER_WAY and not new and bool(old or beacon)
        return d

    def can_uninstall(self):
        inst = self.installed()
        if inst["installed"] and inst["how"] == OTHER_WAY:
            return False, (f"YTM Starcast at {inst['where']} was installed another way (with pip, or run from its "
                           "source folder), so Orbit leaves it alone. Remove it the way it was installed.")
        return True, ""

    def title(self, op, rel):
        inst = self.installed()
        if op == "install" and inst["installed"] and inst["how"] == MOVED:
            return f"Move Pocketfader to YTM Starcast {_version(rel['tag']) if rel else ''}".rstrip()
        if op == "install" and not inst["installed"] and (inst.get("legacy") or {}).get("beacon"):
            return "Install YTM Starcast (replaces YTM Beacon)"
        return super().title(op, rel)

    def info(self, op, given=None):
        d = super().info(op, given)
        if d["title"].endswith("(replaces YTM Beacon)") and not any(
                f["key"] == "beacon" and f["value"] for f in d["options"]):
            d["title"] = f"Install {self.name}"         # YTM Beacon is kept
        return d

    def _existing_lan(self):
        """Whether the install here (YTM Starcast's, else Pocketfader's) also listens on the home network."""
        h = self.host
        if h.system == "windows":
            for n in (NEW, OLD):
                rc, out = h.probe([h.tool("schtasks"), "/Query", "/TN", n.task, "/XML"])
                if rc == 0:
                    return "--lan" in (out or "")
            return False
        if h.system == "linux":
            places = [self.unit_path(NEW), self.unit_path(OLD), self.autostart_path(NEW), self.autostart_path(OLD)]
        else:
            places = [self.plist_path(NEW), self.plist_path(OLD)]
        for path in places:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            return ("--lan" in text) if h.system == "linux" else ("<string>--lan</string>" in text)
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

    def _beacon_version(self, exe):
        rc, out = self.host.probe([exe, "version"])
        m = re.search(r"v?(\d+\.\d+\.\d+[^\s]*)", out or "")
        return m.group(1) if rc == 0 and m else ""

    def _old_links(self):
        """Pocketfader's commands in ~/.local/bin, but only links into its own folder (never someone else's file)."""
        return [ln for ln in self.links(OLD) if _links_into(ln, self.app_dir(OLD))]

    def _leftovers(self):
        """What is left of Pocketfader here once YTM Starcast has replaced it (never its settings)."""
        h = self.host
        paths = [self.app_dir(OLD)]
        if h.system == "windows":
            paths.append(self.shortcut_path(OLD))
        elif h.system == "linux":
            paths += self._old_links() + [self.desktop_path(OLD), self.icon_path(OLD), self.autostart_path(OLD)]
        return [p for p in paths if p.exists() or p.is_symlink()]

    # ---- what the window shows -----------------------------------------------------------
    def requirements(self, op):
        if op == "uninstall":
            return []
        h, out = self.host, []
        discord = need("Discord desktop app (for your Discord status)", True, blocking=False,
                       detail="YTM Starcast shows your status through the Discord app and reconnects whenever Discord "
                              "starts, so Discord can be installed later.")
        if h.system == "windows":
            return [need("Windows 10 or 11 (64-bit)", h.arch == "amd64",
                         "The Windows program is built for 64-bit Intel/AMD processors. Windows on ARM isn't "
                         "supported yet."), discord]
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
                            "Part of systemd. Without it YTM Starcast only controls volume and YouTube Music.",
                            blocking=False))
            out.append(need("systemd (start at login)", h.has_systemd,
                            "Without systemd YTM Starcast installs, but you start it yourself with `ytm-starcast`.",
                            blocking=False))
        else:
            out.append(need("Python 3.9 or newer", py,
                            "Install Python 3 from python.org (or run `xcode-select --install`).",
                            detail=f"Python {v}" if py else ""))
        out.append(need("Tailscale (reach it from your phone anywhere)", h.which("tailscale") or self._tailscale_app(),
                        "Optional. Without it, turn on “home Wi-Fi” below, or use YTM Starcast on this computer only.",
                        blocking=False))
        out.append(discord)
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
            return [option("purge", "Also delete YTM Starcast's settings", "bool", False,
                           f"Your settings are in {self.settings_dir()}. This also deletes any that Pocketfader and "
                           "YTM Beacon left. Leave it off to keep them.")]
        opts = []
        if h.system != "linux" or h.has_systemd:
            opts.append(option("autostart", "Start when I log in", "bool", True,
                               "Keeps the remote and your Discord status going without opening anything."))
        opts.append(option("lan", "Also reach it on my home Wi-Fi", "bool", self._existing_lan(),
                           "Lets phones on the same network connect. It then asks for an access token, which "
                           "YTM Starcast shows you. Not needed with Tailscale."))
        if h.system != "windows":
            opts.append(option("ytdlp", "Let this computer fetch songs for the phone", "bool", True,
                               "Installs yt-dlp into YTM Starcast's own environment, used when the phone can't "
                               "load a song by itself."))
        inst = self.installed()
        if (inst.get("legacy") or {}).get("beacon"):
            # On by default when this install is the move (nothing, or Pocketfader, installed yet); off when YTM
            # Starcast is already installed next to YTM Beacon, which was then kept, and on a Mac, where YTM Starcast
            # isn't tested yet.
            on = inst["how"] in ("", MOVED) and h.system != "darwin"
            opts.append(option("beacon", "Remove the old YTM Beacon app", "bool", on,
                               "YTM Starcast shows your Discord status itself. YTM Beacon's own uninstaller removes "
                               "it and keeps its settings for YTM Starcast to take over. Leave this off to keep YTM "
                               "Beacon: YTM Starcast's Discord status then waits while it runs."
                               + (" Off by default on a Mac, where YTM Starcast isn't tested yet."
                                  if h.system == "darwin" else "")))
        return opts

    def assets(self, rel):
        try:
            return [self.zip_name(rel)] if self.host.system == "windows" else [self.wheel_name(rel)]
        except Failed:
            return []

    def summary(self, op, opts, rel):
        h, inst = self.host, self.installed()
        legacy = inst.get("legacy") or {}
        if op == "uninstall":
            lines = ["Stop YTM Starcast and stop it starting at login"]
            for n in (NEW, OLD):
                found = self._layout(n)
                if found:
                    lines.append(f"Remove {found[0]}")
            if h.system == "linux":
                lines.append("Remove its apps-menu entry and the ytm-starcast commands")
            if h.system == "windows":
                lines.append("Remove its Start-menu entry")
            if legacy.get("beacon"):
                lines.append(f"Remove YTM Beacon ({legacy['beacon']}) with its own uninstaller")
            lines.append("Delete your settings too (YTM Starcast's, and any Pocketfader and YTM Beacon left)"
                         if opts.get("purge") else f"Keep your settings ({self._kept_settings()})")
            lines.append("Remove YTM Starcast's browser extension yourself afterwards, if you added it "
                         "(browsers only allow that by hand)")
            return lines
        lines = []
        if legacy.get("pocketfader"):
            if inst.get("how") == MOVED:
                v = f" {inst['version']}" if inst.get("version") else ""
                lines.append(f"Replace Pocketfader{v} (YTM Starcast's old name): stop it and remove its program from "
                             f"{legacy['pocketfader']}. Your settings and paired phones carry over.")
            else:
                lines.append(f"Remove what is left of Pocketfader (YTM Starcast's old name) in {legacy['pocketfader']}")
        if legacy.get("beacon") and opts.get("beacon"):
            v = self._beacon_version(legacy["beacon"])
            lines.append(f"Remove YTM Beacon{' ' + v if v else ''} ({legacy['beacon']}): YTM Starcast shows your "
                         "Discord status itself. Beacon's settings are kept for YTM Starcast to take over. Remove "
                         "Beacon's browser extension yourself afterwards.")
        elif legacy.get("beacon"):
            lines.append(f"Keep YTM Beacon ({legacy['beacon']}): YTM Starcast's Discord status waits while it runs. "
                         "YTM Starcast's page in Orbit can remove it later.")
        v = rel["tag"] if rel else "the latest release"
        lines.append(f"Download YTM Starcast {v} from GitHub and check its checksum")
        if h.system == "windows":
            lines.append(f"Put the program in {self.app_dir()} and add YTM Starcast to the Start menu")
        else:
            lines.append(f"Install it with its own Python in {self.app_dir()}")
            if opts.get("ytdlp"):
                lines.append("Add yt-dlp so this computer can fetch songs for the phone (from PyPI)")
            if h.system == "linux":
                lines.append("Add YTM Starcast to your apps menu")
        if opts.get("autostart", h.system == "linux" and h.has_systemd):
            lines.append("Start it now and whenever you log in")
        elif h.system == "linux" and not h.has_systemd and self.autostart_path(OLD).exists():
            lines.append("Start it when you log in, as Pocketfader did")
        elif h.system == "linux" and not h.has_systemd:
            lines.append("You start it yourself with `ytm-starcast` (no systemd here)")
        else:
            lines.append("Start it now (not at login)")
        lines.append("Listen on this computer, Tailscale and your home Wi-Fi (with an access token)"
                     if opts.get("lan") else "Listen on this computer and Tailscale only")
        lines.append("No administrator password needed")
        return lines

    def _kept_settings(self):
        return next((d for d in (self.settings_dir(NEW), self.settings_dir(OLD)) if d.exists()), self.settings_dir())

    def steps(self, op, opts):
        sysname = self.host.system
        legacy = self.installed().get("legacy") or {}
        # removing YTM Starcast removes YTM Beacon too; installing it does when asked (the "beacon" option)
        beacon = [Step("beacon", "Remove YTM Beacon", lambda s: self._beacon(s, purge=bool(opts.get("purge"))), 2)
                  ] if legacy.get("beacon") and (op == "uninstall" or opts.get("beacon")) else []
        if op == "uninstall":
            if sysname == "windows":
                return [Step("stop", "Stop YTM Starcast", self._win_stop, 1),
                        Step("task", "Stop it starting at logon", self._win_untask, 1)] + beacon + [
                        Step("files", "Remove the program", self._remove_files, 1)]
            return [Step("stop", "Stop YTM Starcast and its start-at-login", self._unix_unservice, 1)] + beacon + [
                    Step("files", "Remove the program", self._remove_files, 1)]
        cleanup = [Step("cleanup", "Remove Pocketfader's old files", self._cleanup, 0.5)] if self._leftovers() else []
        steps = [Step("find", "Find the latest release", self._find, 0.5)]
        if sysname == "windows":
            moved = bool(legacy.get("pocketfader"))
            steps += [
                Step("download", "Download", self._download, 5),
                Step("verify", "Check the download", self._verify, 0.5),
                Step("unpack", "Unpack", self._unpack, 1),
                # also an old Pocketfader in a folder Orbit doesn't know: both use the same port
                Step("stop", "Stop the running copy", lambda s: self._win_stop(s, retire=not moved), 0.5),
                Step("copy", "Install the program", self._win_copy, 1),
                Step("shortcut", "Add it to the Start menu", self._win_shortcut, 0.5),
            ]
            if moved:
                steps.append(Step("retire", "Stop Pocketfader starting at logon (its old name)", self._win_retire, 0.5))
            return steps + beacon + [
                Step("task", "Start at logon" if opts.get("autostart") else "Turn off start at logon",
                     self._win_task, 1),
                Step("start", "Start YTM Starcast", self._win_start, 1.5),
            ] + cleanup
        steps += [
            Step("download", "Download", self._download, 1.5),
            Step("verify", "Check the download", self._verify, 0.5),
            Step("venv", "Set up its own Python", self._venv, 2),
            Step("pip", "Install YTM Starcast", self._pip, 2),
        ]
        if opts.get("ytdlp"):
            steps.append(Step("ytdlp", "Add yt-dlp", self._ytdlp, 3))
        if sysname == "linux":
            # without systemd, Pocketfader ran from its login entry or its desktop window: stopped all the same
            due = (legacy.get("pocketfader") or (self.host.has_systemd and self.unit_path(OLD).exists())
                   or self._running((OLD,)))
        else:
            due = legacy.get("pocketfader") or self.plist_path(OLD).exists()
        retire = [Step("retire", "Stop Pocketfader (its old name)", self._retire, 0.5)] if due else []
        if sysname == "linux":
            steps.append(Step("menu", "Add it to your apps menu", self._menu, 0.5))
            steps += retire
            if not self.host.has_systemd and self.autostart_path(OLD).exists():
                steps.append(Step("login", "Start it when you log in, as Pocketfader did", self._xdg_autostart, 0.5))
            steps += beacon
            if self.host.has_systemd:
                steps.append(Step("service", "Start it now and at login" if opts.get("autostart") else "Start it now",
                                  self._systemd, 1))
                steps.append(Step("check", "Check that it answers", self._wait, 1))
        else:
            steps += retire + beacon
            steps.append(Step("agent", "Start it now and at login" if opts.get("autostart") else "Start it now",
                              self._launchd, 1))
            steps.append(Step("check", "Check that it answers", self._wait, 1))
        return steps + cleanup

    # ---- shared steps ---------------------------------------------------------------------
    def _find(self, s):
        s.vars["rel"] = rel = s.release(self.repo)
        s.vars["asset"] = self.zip_name(rel) if self.host.system == "windows" else self.wheel_name(rel)
        s.asset(rel, "SHA256SUMS.txt", self.repo)
        s.detail(f"{rel['tag']} · {s.vars['asset']}")
        s.vars["kept"] = kept = kept_options(self._existing_command())
        if kept:
            shown = ["••••••" if i and kept[i - 1] == "--token" else ("--token=••••••" if w.startswith("--token=") else w)
                     for i, w in enumerate(kept)]
            s.log("Keeping its options: " + " ".join(shown))

    def _download(self, s):
        s.vars["file"] = s.download(s.vars["rel"], s.vars["asset"], self.repo)

    def _verify(self, s):
        sums = s.fetch_text(s.vars["rel"], "SHA256SUMS.txt", self.repo)
        s.verify(s.vars["file"], sums, s.vars["asset"])

    def _existing_command(self):
        """The server's command line as the install here has it (the service, the login entry or the LaunchAgent; YTM
        Starcast's first, else Pocketfader's), split into words."""
        h = self.host
        if h.system == "linux":
            for path, key in ((self.unit_path(NEW), "ExecStart="), (self.unit_path(OLD), "ExecStart="),
                              (self.autostart_path(NEW), "Exec="), (self.autostart_path(OLD), "Exec=")):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line in lines:
                    if line.startswith(key):
                        return line[len(key):].split()
        elif h.system == "darwin":
            for path in (self.plist_path(NEW), self.plist_path(OLD)):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                arr = text.split("<key>ProgramArguments</key>", 1)[-1].split("</array>", 1)[0]
                return [x.split("</string>", 1)[0].replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                        for x in arr.split("<string>")[1:]]
        return []

    def _args(self, s):
        # what the install before had (read when the install started: the old service may be gone by now)
        args = list(s.vars.get("kept") or [])
        if self.host.system == "darwin":
            for k, v in (("--audio", "none"), ("--media", "none")):
                if not any(a == k or a.startswith(k + "=") for a in args):
                    args += [k, v]
        if s.vars["opts"].get("lan"):
            args.append("--lan")
        return args

    def _wait(self, s):
        if not s.wait_http(f"http://127.0.0.1:{self.port}/api/info", seconds=self.wait_seconds):
            s.vars["slow"] = True
            s.log("YTM Starcast hasn't answered yet; it may still be starting. Its log is in Orbit.")

    def _beacon(self, s, purge=False):
        """YTM Beacon's own uninstaller: it stops Beacon and removes its program and start-at-login, and keeps its
        settings unless asked (YTM Starcast takes them over). Never fails the job: YTM Starcast copes while Beacon is
        still there."""
        exe = self.beacon_found()
        if not exe:
            s.detail("already gone")
            self._beacon_leftovers(s)
            return SKIP
        s.detail("with its own uninstaller")
        try:
            rc, _out = s.run([exe, "uninstall"] + (["--purge"] if purge else []), ok=range(0, 256))
        except Failed as e:             # it couldn't even start (a broken or half-removed program)
            rc = -1
            s.log(f"YTM Beacon's uninstaller didn't run: {e}")
        if rc:
            s.log(f"YTM Beacon's uninstaller stopped with an error (code {rc}).")
        if not s.dry_run:
            # On Windows a program can't delete itself while it runs: Beacon has it deleted a moment after it ends.
            for _ in range(int(self.beacon_wait * 2)):
                if not Path(exe).exists():
                    break
                s.sleep(0.5)
            if Path(exe).exists():
                s.vars["beacon_left"] = exe
                s.log(f"Note: YTM Beacon is still at {exe}. YTM Starcast shows your Discord status once it is gone: "
                      "remove it with `ytm-beacon uninstall`, or from YTM Starcast's page in Orbit.")
                s.detail("still there: see the log")
                return
        s.vars["beacon_removed"] = True
        self._beacon_leftovers(s)

    def _beacon_leftovers(self, s):
        """Start-at-login entries YTM Beacon's uninstaller didn't take with it (its program is gone)."""
        if self.host.system != "linux" or s.dry_run:
            return
        s.remove(self.host.config_home / "autostart" / "ytm-beacon.desktop")     # without systemd it used this
        unit = self.host.config_home / "systemd" / "user" / "ytm-beacon.service"
        if not self.host.has_systemd or not unit.exists():
            return
        sc = self.host.tool("systemctl")
        s.run([sc, "--user", "disable", "--now", "ytm-beacon.service"], ok=range(0, 256), quiet=True)
        s.remove(unit)
        s.run([sc, "--user", "daemon-reload"], ok=range(0, 256), quiet=True)

    def _cleanup(self, s):
        left = self._leftovers()
        if not left:
            # on Windows, the release's install-exe.ps1 removes them while it sets up start at logon
            s.detail("already gone")
            return SKIP
        if s.vars.get("slow"):
            s.log("Pocketfader's old files stay until YTM Starcast answers: the next install or update removes them.")
            s.detail("later: YTM Starcast hasn't answered yet")
            return SKIP
        if s.vars.get("still_running"):
            s.log("Pocketfader's old files stay while it still runs: the next install or update removes them.")
            s.detail("later: Pocketfader is still running")
            return SKIP
        s.remove(*left)

    def _remove_files(self, s):
        h = self.host
        paths = [self.app_dir(NEW), self.app_dir(OLD)]
        if h.system == "linux":
            for n in (NEW, OLD):
                paths += [ln for ln in self.links(n) if _links_into(ln, self.app_dir(n))]
                paths += [self.desktop_path(n), self.icon_path(n), self.autostart_path(n)]
        if h.system == "windows":
            paths += [self.shortcut_path(NEW), self.shortcut_path(OLD),
                      h.local_app_data / NEW.slug / "extension"]    # the browser extension's folder (elsewhere: inside the program's)
        if s.vars["opts"].get("purge"):
            paths += [self.settings_dir(NEW), self.settings_dir(OLD)]
            if not self.beacon_found():     # YTM Beacon itself is gone: what it left behind goes too
                paths += self.beacon_dirs()
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
              fail="Couldn't create YTM Starcast's Python environment.",
              hint="On Debian/Ubuntu install python3-venv first.")

    def _pip(self, s):
        s.detail(s.vars["asset"])
        s.run([self.venv_bin("python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
               "--no-input", "--no-deps", "--force-reinstall", s.vars["file"]],
              fail="Couldn't install YTM Starcast into its Python environment.")

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
        s.symlink(self.venv_bin(NEW.slug), h.bin_dir / NEW.slug)
        s.symlink(self.venv_bin(f"{NEW.slug}-gui"), h.bin_dir / f"{NEW.slug}-gui")
        icon = None if s.dry_run else next(
            iter(self.venv().glob(f"lib/python*/site-packages/{NEW.module}/static/icon.svg")), None)
        if icon:
            s.write(self.icon_path(), icon.read_text(encoding="utf-8"))
        # the same entry as YTM Starcast's own install.sh writes
        s.write(self.desktop_path(), "\n".join([
            "[Desktop Entry]", "Type=Application", f"Name={NEW.display}", "GenericName=YouTube Music companion",
            "Comment=A remote for your phone, your music as your Discord status, and a better YouTube Music",
            f"Exec={h.bin_dir / (NEW.slug + '-gui')}", f"Icon={NEW.slug}", "Terminal=false",
            "Categories=AudioVideo;Audio;Utility;", "Keywords=YouTube Music;Discord;remote;Pocketfader;YTM Beacon;",
            f"StartupWMClass={WINDOW_ID}", ""]))

    def _xdg_autostart(self, s):
        """No systemd here, and Pocketfader started at login through a login entry: YTM Starcast gets one the same
        way."""
        exe = " ".join([str(self.venv_bin(NEW.slug))] + self._args(s))
        s.write(self.autostart_path(), "\n".join([
            "[Desktop Entry]", "Type=Application", f"Name={NEW.display}", f"Exec={exe}", "Terminal=false", ""]))

    def _systemd(self, s):
        sc = self.host.tool("systemctl")
        exec_start = " ".join([str(self.venv_bin(NEW.slug))] + self._args(s))
        s.write(self.unit_path(), "\n".join([
            "[Unit]", f"Description={NEW.display} - YouTube Music companion",
            "After=network-online.target sound.target", "", "[Service]", f"ExecStart={exec_start}",
            "Restart=on-failure", "RestartSec=3", "", "[Install]", "WantedBy=default.target", ""]))
        s.run([sc, "--user", "daemon-reload"])
        s.run([sc, "--user", "enable" if s.vars["opts"].get("autostart") else "disable", NEW.unit])
        s.run([sc, "--user", "restart", NEW.unit],
              fail="YTM Starcast's service didn't start.", hint="Open YTM Starcast in Orbit to read its log.")

    def _launchd(self, s):
        args = [str(self.venv_bin(NEW.slug))] + self._args(s)
        log = self.host.home / "Library" / "Logs" / f"{NEW.slug}.log"
        s.write(self.plist_path(), plist(NEW.label, args, s.vars["opts"].get("autostart", True), log))
        lc, dom = self.host.tool("launchctl"), f"gui/{self.host.uid}"
        s.run([lc, "bootout", dom, self.plist_path()], ok=range(0, 256), quiet=True)
        s.run([lc, "bootstrap", dom, self.plist_path()], fail="macOS didn't start YTM Starcast.")
        s.run([lc, "kickstart", f"{dom}/{NEW.label}"], ok=range(0, 256), quiet=True)   # now, even when not at login

    def _retire(self, s):
        """Stop Pocketfader and its start at login (its files go once YTM Starcast answers)."""
        if self.host.system == "linux":
            if self.host.has_systemd:
                sc = self.host.tool("systemctl")
                s.run([sc, "--user", "disable", "--now", OLD.unit], ok=range(0, 256))
                s.remove(self.unit_path(OLD))
                s.run([sc, "--user", "daemon-reload"], ok=range(0, 256))
            else:
                s.remove(self.autostart_path(OLD))     # its login entry (YTM Starcast gets its own)
            # one its desktop window or its login entry started: no service manager stops that
            if not self._stop_running(s, (OLD,)) and not self.host.has_systemd:
                s.detail("it wasn't running")
        else:
            s.run([self.host.tool("launchctl"), "bootout", f"gui/{self.host.uid}", self.plist_path(OLD)],
                  ok=range(0, 256), quiet=True)
            s.remove(self.plist_path(OLD))

    def _unix_unservice(self, s):
        if self.host.system == "linux":
            if self.host.has_systemd:
                sc = self.host.tool("systemctl")
                for n in (NEW, OLD):
                    s.run([sc, "--user", "disable", "--now", n.unit], ok=range(0, 256))
                    s.remove(self.unit_path(n))
                s.run([sc, "--user", "daemon-reload"], ok=range(0, 256))
            if not self._stop_running(s, (NEW, OLD)) and not self.host.has_systemd:
                return SKIP
        else:
            for n in (NEW, OLD):
                s.run([self.host.tool("launchctl"), "bootout", f"gui/{self.host.uid}", self.plist_path(n)],
                      ok=range(0, 256), quiet=True)
                s.remove(self.plist_path(n))

    # ---- a server no service manager stops (Linux) -----------------------------------------
    def _cmdline(self, pid):
        """Process `pid`'s command line (bytes), or b"" when it has ended or isn't this user's to read."""
        try:
            return (self.proc / str(int(pid)) / "cmdline").read_bytes()
        except (OSError, ValueError):
            return b""

    @staticmethod
    def _is_server(cmdline, names):
        """Is this command line the server of one of `names`: `<program> …`, `python …/<program> …` or
        `python -m <module> …`?"""
        argv = [os.fsdecode(a) for a in cmdline.split(b"\0")] + ["", "", ""]
        return any(n.slug in (os.path.basename(argv[0]), os.path.basename(argv[1]))
                   or (argv[1] == "-m" and argv[2] == n.module) for n in names)

    def _running(self, names):
        """[(pid, pid file or None, command line)] of the servers of `names` running here without a service manager:
        one a desktop window started (it writes `server.pid` in the settings folder), and one a login entry or a
        terminal started from the program in its own folder. A pid file counts only when that process's command line
        is the server's, so one left from before a restart never stops some other program."""
        if self.host.system != "linux" or not self.proc.is_dir():
            return []
        found = {}
        for n in (NEW, OLD):
            pidfile = self.settings_dir(n) / "server.pid"
            try:
                pid = int(re.sub(r"\D", "", pidfile.read_text(encoding="utf-8", errors="replace"))[:9] or 0)
            except OSError:
                continue
            cmd = self._cmdline(pid) if pid > 1 else b""
            if cmd and self._is_server(cmd, names) and pid not in found:
                found[pid] = (pidfile, cmd)
        # the program in its folder (a Pocketfader that updated itself to YTM Starcast also has YTM Starcast's)
        programs = {os.path.realpath(self.venv_bin(x, n)) for n in names for x in (n.slug, NEW.slug)}
        try:
            procs = [d for d in self.proc.iterdir() if d.name.isdigit() and int(d.name) not in found]
        except OSError:
            procs = []
        for d in procs:
            try:
                if d.stat().st_uid != self.host.uid:
                    continue
                cmd = (d / "cmdline").read_bytes()
            except OSError:
                continue
            if any(a and os.path.realpath(os.fsdecode(a)) in programs for a in cmd.split(b"\0")[:2]):
                found[int(d.name)] = (None, cmd)
        found.pop(os.getpid(), None)
        return [(pid, pidfile, cmd) for pid, (pidfile, cmd) in sorted(found.items())]

    def _stop_running(self, s, names):
        """Stop what _running() finds the way the desktop window stops it (SIGTERM), and wait for it to end."""
        found = self._running(names)
        for pid, pidfile, cmd in found:
            what = f"process {pid}" + (f" (started by its desktop window: {pidfile})" if pidfile else "")
            if s.dry_run:
                s.log(f"dry run: would stop {what}")
                continue
            s.log(f"Stopping {what}")
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as e:
                s.log(f"Couldn't stop process {pid}: {e.strerror or e}")
            for _ in range(int(self.stop_wait * 4)):
                if self._cmdline(pid) != cmd:          # ended (a process that has ended shows no command line)
                    break
                s.sleep(0.25)
            if self._cmdline(pid) == cmd:
                s.vars["still_running"] = pid
                s.log(f"Note: process {pid} is still running. Stop it with `kill {pid}`, or restart the computer.")
            elif pidfile:
                s.remove(pidfile)      # so nothing later stops another program that gets the same number
        return found

    # ---- Windows ------------------------------------------------------------------------
    def _ps(self, s, script, **kw):
        return s.run([self.host.tool("powershell"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-Command", script], **kw)

    def _unpack(self, s):
        d = s.extract(s.vars["file"])
        s.vars["src"] = s.find(d, f"{NEW.slug}.exe").parent

    def _win_stop(self, s, retire=False):
        """Stop YTM Starcast, and Pocketfader by every name it had, wherever it was installed."""
        for n in (NEW, OLD):
            s.run([self.host.tool("schtasks"), "/End", "/TN", n.task], ok=range(0, 256), quiet=True)
        for exe in (f"{NEW.slug}.exe", f"{NEW.slug}-gui.exe", "pocketfader.exe", "pocketfader-gui.exe"):
            s.run([self.host.tool("taskkill"), "/IM", exe, "/F"], ok=range(0, 256), quiet=True)
        if retire:
            self._win_retire(s)
        s.sleep(0.5)

    def _win_retire(self, s):
        """Pocketfader no longer starts at logon: its scheduled task and its window's start-at-login value go."""
        script = self.app_dir(OLD) / "install-exe.ps1"
        if script.exists():
            s.run([self.host.tool("powershell"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", script, "-Uninstall"], ok=range(0, 256), quiet=True)
        else:
            s.run([self.host.tool("schtasks"), "/Delete", "/TN", OLD.task, "/F"], ok=range(0, 256), quiet=True)
        s.run([self.host.tool("reg"), "delete", RUN_KEY, "/v", OLD.task, "/f"], ok=range(0, 256), quiet=True)

    def _win_copy(self, s):
        s.copy_tree(s.vars["src"], self.app_dir())
        s.write(self.app_dir() / "orbit-install.json", json.dumps({"version": s.vars["rel"]["tag"].lstrip("v")}))

    def _win_shortcut(self, s):
        target = self.app_dir() / f"{NEW.slug}-gui.exe"
        # the Start menu's folder can be missing (a new or redirected profile): make it first
        self._ps(s, "New-Item -ItemType Directory -Force -Path " + _ps_str(self.shortcut_path().parent) + " | Out-Null;"
                    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_str(self.shortcut_path()) + ");"
                    f"$s.TargetPath={_ps_str(target)};$s.WorkingDirectory={_ps_str(self.app_dir())};"
                    "$s.Description='YouTube Music companion';$s.Save()",
                 fail="Couldn't add YTM Starcast to the Start menu.")

    def _win_task(self, s):
        script = self.app_dir() / "install-exe.ps1"
        if s.vars["opts"].get("autostart"):
            # -Lan is always given, on or off: without it the script keeps the --lan its task had before. Windows
            # PowerShell can't take -Lan:$false through -File, hence -Command; exit 0 keeps -File's exit code (only a
            # script that stops with an error fails the step).
            lan = "-Lan" if s.vars["opts"].get("lan") else "-Lan:$false"
            self._ps(s, f"& {_ps_str(script)} {lan}; exit 0", fail="Couldn't set YTM Starcast to start at logon.")
        else:
            # only the scheduled task: YTM Starcast's install-exe.ps1 -Uninstall removes the whole program as well
            s.run([self.host.tool("schtasks"), "/Delete", "/TN", NEW.task, "/F"], ok=range(0, 256), quiet=True)

    def _win_start(self, s):
        if s.vars["opts"].get("autostart"):
            self._ps(s, f"Start-ScheduledTask -TaskName {_ps_str(NEW.task)}", fail="Couldn't start YTM Starcast.")
        else:
            args = " ".join(self._args(s)) or f"--port {PORT}"
            self._ps(s, f"Start-Process -WindowStyle Hidden -FilePath {_ps_str(self.app_dir() / (NEW.slug + '.exe'))} "
                        f"-ArgumentList {_ps_str(args)} -WorkingDirectory {_ps_str(self.app_dir())}",
                     fail="Couldn't start YTM Starcast.")
        self._wait(s)

    def _win_untask(self, s):
        for n in (NEW, OLD):
            script = self.app_dir(n) / "install-exe.ps1"
            if script.exists():
                s.run([self.host.tool("powershell"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                       "-File", script, "-Uninstall"], ok=range(0, 256))
            else:
                s.run([self.host.tool("schtasks"), "/Delete", "/TN", n.task, "/F"], ok=range(0, 256), quiet=True)
            s.run([self.host.tool("reg"), "delete", RUN_KEY, "/v", n.task, "/f"], ok=range(0, 256), quiet=True)

    # ---- done -----------------------------------------------------------------------------
    def finish(self, sess, op, opts):
        before = sess.vars.get("before") or {}
        if op == "uninstall":
            body = [] if opts.get("purge") else [f"Your settings are kept in {self._kept_settings()}."]
            if sess.vars.get("beacon_removed"):
                body.append("YTM Beacon is removed too: remove its extension from your browser's extensions page.")
            elif sess.vars.get("beacon_left"):
                body.append(f"YTM Beacon is still at {sess.vars['beacon_left']}: remove it with "
                            "`ytm-beacon uninstall`.")
            body.append("If you added YTM Starcast's browser extension, remove it from your browser's extensions page.")
            return {"headline": "YTM Starcast is removed.", "body": " ".join(body), "next": []}
        beacon_note = ""
        if sess.vars.get("beacon_removed"):
            beacon_note = (" YTM Beacon is removed: YTM Starcast shows your Discord status now (turn it on in YTM "
                           "Starcast if it's off). Remove YTM Beacon's extension from your browser's extensions page.")
        elif sess.vars.get("beacon_left"):
            beacon_note = (f" YTM Beacon is still at {sess.vars['beacon_left']}: YTM Starcast shows your Discord "
                           "status once it is gone. Remove it with `ytm-beacon uninstall`.")
        elif (before.get("legacy") or {}).get("beacon") and not opts.get("beacon"):
            beacon_note = (" YTM Beacon is kept: YTM Starcast's Discord status waits while it runs. YTM Starcast's "
                           "page in Orbit can remove it.")
        if sess.vars.get("still_running"):
            pid = sess.vars["still_running"]
            beacon_note += (f" Pocketfader is still running (process {pid}) and keeps YTM Starcast's port: stop it "
                            f"with `kill {pid}`, or restart the computer.")
        h = self.host
        moved = before.get("how") == MOVED
        nxt = [{"id": "app", "label": "open in orbit", "kind": "app", "app": self.id, "primary": True},
               {"id": "web", "label": "open ytm starcast", "kind": "url", "url": f"http://127.0.0.1:{PORT}/"},
               {"id": "extension", "label": "set up the browser extension", "kind": "url",
                "url": f"http://127.0.0.1:{PORT}/#extension"}]
        if h.system == "linux" and h.has_systemd is False:
            body = "Start it with `ytm-starcast` in a terminal; it prints the address to open on your phone."
            head = "YTM Starcast is installed."
        else:
            body = ("On your phone, open this computer's address on port 8777 (YTM Starcast's window shows it, "
                    "with a QR code), then add it to your home screen.")
            head = "YTM Starcast is installed and running."
        if moved:
            head = "Pocketfader is now YTM Starcast."
            body = ("Your settings and paired phones carry over: phones keep the same address. "
                    + body.replace("On your phone", "On a new phone", 1))
        if sess.vars.get("slow"):
            head = "Pocketfader is now YTM Starcast." if moved else "YTM Starcast is installed."
            body = "It hasn't answered yet; give it a few seconds, or read its log in Orbit. " + body
        if h.system == "darwin":
            body += (" YTM Starcast isn't tested on macOS yet; if something doesn't work, its log is in "
                     "~/Library/Logs/ytm-starcast.log.")
        if sess.vars.get("beacon_removed") and not moved and not before.get("installed"):
            beacon_note += (" Then add YTM Starcast's browser extension (ytm-starcast-extension.zip in its release) so "
                            "YTM Starcast hears what YouTube Music plays.")
        return {"headline": head, "body": body + beacon_note, "next": nxt}


def _links_into(link, folder):
    """Is `link` a symbolic link to something inside `folder`?"""
    if not link.is_symlink():
        return False
    try:
        target = Path(os.readlink(link))
    except OSError:
        return False
    if not target.is_absolute():
        target = link.parent / target
    return os.path.normpath(str(target)).startswith(os.path.normpath(str(folder)) + os.sep)


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
