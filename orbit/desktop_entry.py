"""Put the standalone Orbit program in the apps menu (Linux) or the Start menu (Windows).

The downloads run straight from wherever they were unpacked; this adds the launcher pointing at that copy.
(On macOS, dragging Orbit.app to Applications is the way.)
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

STATIC = Path(__file__).parent / "static"


def program():
    """The command that starts this Orbit: the standalone program, or Python running the package."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "orbit"]


def _linux_paths(home=None):
    home = Path(home or Path.home())
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return data / "applications" / "orbit.desktop", data / "icons" / "hicolor" / "scalable" / "apps" / "orbit.svg"


def _windows_link():
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Orbit.lnk"


def state():
    if sys.platform.startswith("linux"):
        entry, _ = _linux_paths()
        return {"supported": True, "where": "your apps menu", "added": entry.exists()}
    if sys.platform == "win32":
        return {"supported": True, "where": "the Start menu", "added": _windows_link().exists()}
    return {"supported": False, "where": "", "added": False}


def add(runner, home=None):
    """Create (or refresh) the launcher. Writes files, so dry-run only records it."""
    cmd = program()
    if sys.platform.startswith("linux"):
        entry, icon = _linux_paths(home)
        if runner.dry_run:
            runner._record("write", [str(entry), str(icon)])
            return {"ok": True, "message": f"dry run: would add Orbit to your apps menu ({entry})"}
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STATIC / "icon.svg", icon)
        entry.parent.mkdir(parents=True, exist_ok=True)
        exe = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        entry.write_text("\n".join([
            "[Desktop Entry]", "Type=Application", "Name=Orbit", "GenericName=App hub",
            "Comment=One place for Pocketfader, Nova and YTM Beacon", f"Exec={exe}", "Icon=orbit",
            "Terminal=false", "Categories=Utility;System;", "StartupWMClass=orbit", ""]), encoding="utf-8")
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(entry.parent)], capture_output=True)
        return {"ok": True, "message": "Orbit is in your apps menu"}
    if sys.platform == "win32":
        link = _windows_link()
        if runner.dry_run:
            runner._record("write", [str(link)])
            return {"ok": True, "message": "dry run: would add Orbit to the Start menu"}
        q = lambda x: "'" + str(x).replace("'", "''") + "'"
        args = " ".join(cmd[1:])
        link.parent.mkdir(parents=True, exist_ok=True)      # a new or redirected profile may not have it yet
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(link)});"
                  f"$s.TargetPath={q(cmd[0])};$s.Arguments={q(args)};$s.WorkingDirectory={q(Path(cmd[0]).parent)};"
                  "$s.Description='One place for Pocketfader, Nova and YTM Beacon';$s.Save()")
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, creationflags=0x08000000)
        if p.returncode != 0:
            return {"ok": False, "message": "couldn't add Orbit to the Start menu: " + (p.stderr.strip()[:200] or "unknown error")}
        return {"ok": True, "message": "Orbit is in the Start menu"}
    return {"ok": False, "message": "on macOS, drag Orbit.app into Applications"}


def follow(old, new):
    """An update moved the program (orbit-v0.3.4-linux-x86_64 → orbit-linux-x86_64): Orbit's own launcher, when it
    starts the old file, now starts the new one. Quietly; a launcher pointing anywhere else is left alone."""
    old, new = str(old), str(new)
    try:
        if sys.platform.startswith("linux"):
            entry, _ = _linux_paths()
            lines = entry.read_text(encoding="utf-8").split("\n")
            exe = f'"{new}"' if " " in new else new
            changed = [f"Exec={exe}" if ln in (f"Exec={old}", f'Exec="{old}"') else ln for ln in lines]
            if changed != lines:
                entry.write_text("\n".join(changed), encoding="utf-8")
        elif sys.platform == "win32":
            link = _windows_link()
            if not link.exists():
                return
            q = lambda x: "'" + str(x).replace("'", "''") + "'"
            script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(link)});"
                      f"if ($s.TargetPath -eq {q(old)}) {{ $s.TargetPath={q(new)};"
                      f"$s.WorkingDirectory={q(Path(new).parent)};$s.Save() }}")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, timeout=30, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        pass
