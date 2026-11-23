"""For CI only: the standalone Orbit installs its apps on this Windows machine, for real, and takes them off again.

Runs on GitHub's Windows machines (the build workflow), never on a person's PC: through a built orbit.exe, the way its
window does it, it installs each app's newest release (asking Windows for administrator rights the way it would on a PC,
which GitHub's machines grant without a prompt), checks that the app answers and that Orbit's page for it sees it,
installs again over itself (an update), then removes it with its settings and makes sure nothing is left.

    python tools/ci-nova-windows.py --exe orbit.exe [nova,ytm-starcast,ytdl-ui,xmas-tui,rpcortex-studio]
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def say(text):
    print(text, flush=True)


def fail(text):
    say(f"::error::{text}")
    sys.exit(1)


# What each app is installed with, and removed with, through the program (the window's own choices).
APPS = {"nova": ({"firewall": True}, {"purge": True}),
        "ytm-starcast": ({}, {"purge": True}),
        "ytdl-ui": ({}, {"purge": True}),
        "xmas-tui": ({"startup": True}, {"purge": True}),
        "rpcortex-studio": ({}, {"purge": True})}
# installed from Orbit, with no page of their own in it: checked by the version Orbit sees instead
NO_PAGE = {"ytdl-ui", "xmas-tui", "rpcortex-studio"}

WATCH = r"""
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class W {
  public delegate bool F(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(F f, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
}
'@
$log = $args[0]; $ready = $args[1]; $stop = $args[2]   # (inside the callback, $args is its own)
# console windows: the classic console's, and Windows Terminal's (where Windows 11 opens consoles). ConPTY's own
# PseudoConsoleWindow is never shown to anyone (Orbit's terminal tab uses ConPTY), so it doesn't count.
$kinds = @('ConsoleWindowClass', 'CASCADIA_HOSTING_WINDOW_CLASS')
$known = @{}; $title = @{}; $first = $true
while (-not (Test-Path -LiteralPath $stop)) {
  [W]::EnumWindows({ param($h, $l)
    if ([W]::IsWindowVisible($h)) {
      $c = New-Object Text.StringBuilder 256; [void][W]::GetClassName($h, $c, 256)
      if ($kinds -contains $c.ToString()) {
        $t = New-Object Text.StringBuilder 512; [void][W]::GetWindowText($h, $t, 512)
        $k = [string]$h
        if (-not $known.ContainsKey($k)) {
          $known[$k] = $first
          Add-Content -LiteralPath $log -Value ("{0:HH:mm:ss.fff}|{1}|{2}|{3}|{4}" -f (Get-Date), $k, $(if ($first) { 'there before' } else { 'new' }), $c, $t)
          $title[$k] = $t.ToString()
        } elseif ($title[$k] -ne $t.ToString()) {
          $title[$k] = $t.ToString()
          Add-Content -LiteralPath $log -Value ("{0:HH:mm:ss.fff}|{1}|title|{2}|{3}" -f (Get-Date), $k, $c, $t)
        }
      }
    }
    $true }, [IntPtr]::Zero) | Out-Null
  if ($first) { $first = $false; Set-Content -LiteralPath $ready -Value "ready" }
  Start-Sleep -Milliseconds 60
}
"""


def watch_windows(folder):
    """Windows: record every console window that opens on the desktop (a classic console or Windows Terminal, what
    a person would see flash up) from now until the returned function is called, which returns the new ones as
    {window: [lines]} (it opens one on purpose first, to show it sees them, and leaves that one out)."""
    import subprocess
    if os.name != "nt":
        return lambda: {}
    script, log, ready, stop = (folder / n for n in ("watch.ps1", "windows.txt", "ready.txt", "stop.txt"))
    script.write_text(WATCH)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(log), str(ready), str(stop)],
                     creationflags=0x08000000)   # CREATE_NO_WINDOW: the watcher itself shows nothing
    end = time.time() + 90
    while not ready.exists() and time.time() < end:
        time.sleep(0.3)
    if not ready.exists():
        fail("the window watcher didn't start")

    def windows():
        out = {}
        for ln in (log.read_text(errors="replace").splitlines() if log.exists() else []):
            parts = ln.split("|", 4)
            if len(parts) == 5:
                out.setdefault(parts[1], []).append(ln)
        return {w: lines for w, lines in out.items() if "|there before|" not in lines[0]}

    # it sees a console window on this desktop: open one on purpose (or "none seen" would prove nothing)
    subprocess.Popen(["ping", "-n", "6", "127.0.0.1"], creationflags=0x00000010, stdout=subprocess.DEVNULL)  # CREATE_NEW_CONSOLE
    end = time.time() + 20
    while time.time() < end and not windows():
        time.sleep(0.3)
    check = set(windows())
    if not check:
        say("what the watcher saw:\n" + (log.read_text(errors="replace") if log.exists() else "(nothing)"))
        fail("the window watcher doesn't see console windows on this machine")
    say("window watcher: it sees console windows here (the one opened on purpose: " + "; ".join(windows()[w][0] for w in check) + ")")
    time.sleep(7)   # that one closes (ping ends)

    def done():
        stop.write_text("stop")
        time.sleep(1)
        return {w: lines for w, lines in windows().items() if w not in check}
    return done


def through_the_program(exe, apps):
    """Install each app through the standalone program's own API (what its install window calls), check that it
    answers and that Orbit's page for it sees that, then remove it and check it's gone."""
    import json
    import socket
    import subprocess
    import urllib.error
    import urllib.request

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def call(path, body=None, timeout=15):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def until(what, check, seconds=300):
        end = time.time() + seconds
        while time.time() < end:
            try:
                got = check()
                if got:
                    return got
            except (OSError, urllib.error.URLError, ValueError):
                pass
            time.sleep(2)
        fail(f"timed out waiting for {what}")

    def job(app, op, options):
        say(f"\n===== {app}: {op} {options} (through {Path(exe).name})")
        plan = call(f"/api/install/{app}/plan", {"op": op, "options": options})
        say(f"{plan['title']} · {plan['support']['level']} · blocked: {plan['blocked'] or 'no'}")
        if plan["blocked"]:
            fail("blocked: " + plan["blocked"])
        j = call(f"/api/install/{app}/start", {"op": op, "options": options})
        seen = 0
        while True:
            snap = call(f"/api/install/jobs/{j['id']}?from={seen}")
            for line in snap["log"]:
                say("  " + line)
            seen = snap["logNext"]
            if snap["state"] != "running":
                break
            time.sleep(1)
        if snap["state"] != "done":
            fail(f"{app} {op} {snap['state']}: {snap['error']} {snap['hint']}")
        say(f"  result: {snap['result'].get('headline')} {snap['result'].get('body')}")

    def listing(app):
        return next(a for a in call("/api/install")["apps"] if a["id"] == app)

    def installed(app):
        return listing(app)["installed"]["installed"]

    home = Path(tempfile.mkdtemp())
    # Orbit keeps its own settings in a folder of its own here
    env = dict(os.environ, APPDATA=str(home))
    windows = watch_windows(home)
    p = subprocess.Popen([exe, "--no-window", "--port", str(port)], env=env, stdin=subprocess.DEVNULL,
                         stdout=open(home / "orbit.log", "w"), stderr=subprocess.STDOUT)
    # the overview, every 3 s, as Orbit's window asks for it (each app's status checks run then)
    polling = {"on": True}

    def poll():
        while polling["on"]:
            try:
                call("/api/apps")
            except (OSError, urllib.error.URLError, ValueError):
                pass
            time.sleep(3)
    import threading
    threading.Thread(target=poll, daemon=True).start()
    try:
        hub = until("Orbit to start", lambda: call("/api/hub"), 90)
        say(f"Orbit {hub['version']} on port {port}")
        for app in apps:
            install, remove = APPS[app]
            if installed(app):
                fail(f"{app} is already installed here")
            job(app, "install", install)
            if app in NO_PAGE:
                want = listing(app)["latest"]
                found = until(f"Orbit to see {app} {want} installed",
                              lambda: (lambda i: i if i["installed"] and i["version"] == want else None)(listing(app)["installed"]), 60)
                say(f"Orbit sees {app} {found['version']} installed in {found['where']} ({found['how']})")
                job(app, "install", install)   # again, over itself (an update)
                job(app, "uninstall", remove)
                until(f"{app} to be gone", lambda: not installed(app), 120)
                say(f"{app}: installed, installed again over itself and removed through the standalone Orbit.")
                continue
            page = until(f"{app} to answer on its page",
                         lambda: (lambda d: d if d["status"].get("version") and d["status"]["state"] != "down" else None)(
                             call(f"/api/apps/{app}")), 180)
            st = page["status"]
            say(f"Orbit's {app} page: {st['state']} · {st['headline']} · {st.get('version')}")
            say("  " + " · ".join(f"{f[0]}: {f[1]}" for f in st.get("facts", [])))
            if app == "nova" and not {"win:add", "gui"} <= {a["id"] for a in page["actions"]}:
                fail(f"Nova's Windows actions are missing: {[a['id'] for a in page['actions']]}")
            job(app, "uninstall", remove)
            until(f"{app} to be gone", lambda: not installed(app), 120)
            say(f"{app}: installed, answered and removed through the standalone Orbit.")
        say(f"\nAll of {', '.join(apps)}: installed, answered and removed through the standalone Orbit.")
        polling["on"] = False
        seen = windows()
        if seen:
            fail("console windows opened while Orbit installed, polled and removed apps (nothing should show one):\n  "
                 + "\n  ".join(" / ".join(lines) for lines in seen.values()))
        if os.name == "nt":
            say("No console window opened while Orbit installed, polled and removed the apps.")
    finally:
        polling["on"] = False
        p.kill()
        try:
            say("--- Orbit's output:\n" + (home / "orbit.log").read_text(errors="replace")[-2000:])
        except OSError:
            pass


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2 or args[0] != "--exe":
        sys.exit(__doc__)
    through_the_program(args[1], args[2].split(",") if len(args) > 2 else ["nova"])
