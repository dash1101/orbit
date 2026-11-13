"""For CI only: Orbit's installer puts Nova on this Windows machine, for real, and takes it off again.

Runs on GitHub's Windows machines (.github/workflows/nova-windows.yml), never on a person's PC: it installs
Nova's newest release (asking Windows for administrator rights the way it would on a PC, which GitHub's machines
grant without a prompt), checks that Nova answers and that Orbit's Nova page sees it, installs again over
itself (an update), then removes it with its settings and makes sure nothing is left.

    python tools/ci-nova-windows.py                   through Orbit's Python code
    python tools/ci-nova-windows.py --exe orbit.exe [nova,ytm-starcast]
                                                      through a built standalone Orbit, the way its window does it
    python tools/ci-nova-windows.py --exe orbit.exe ytm-starcast --migrate
                                                      the move to YTM Starcast: first puts Pocketfader and YTM Beacon
                                                      on the machine the way they were installed before YTM Starcast,
                                                      then has the standalone Orbit move it over and remove everything
                                                      again
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orbit import net                                   # noqa: E402
from orbit.apps.nova import Nova                        # noqa: E402
from orbit.config import Config                         # noqa: E402
from orbit.install.engine import Installer, Sources     # noqa: E402
from orbit.install.host import Host                     # noqa: E402
from orbit.install.recipes import load                  # noqa: E402
from orbit.net import Releases                          # noqa: E402
from orbit.runner import Runner                         # noqa: E402
from orbit.server import Ctx, Jobs                      # noqa: E402


def say(text):
    print(text, flush=True)


def fail(text):
    say(f"::error::{text}")
    sys.exit(1)


def run(installer, op, opts):
    say(f"\n===== {op} {opts}")
    job = installer.start("nova", op, opts)
    seen = 0
    while True:
        snap = job.snapshot(seen)
        for line in snap["log"]:
            say("  " + line)
        seen = snap["logNext"]
        if snap["state"] != "running":
            break
        time.sleep(1)
    for s in snap["steps"]:
        took = f"{s['ended'] - s['started']:.1f} s" if s.get("ended") and s.get("started") else ""
        say(f"  [{s['state']:>7}] {s['label']} {took} {s.get('detail') or ''}")
    if snap["state"] != "done":
        fail(f"{op} {snap['state']}: {snap['error']} {snap['hint']}")
    say(f"  result: {snap['result'].get('headline')} {snap['result'].get('body')}")
    return snap


def answers():
    for url, insecure in (("https://127.0.0.1:8495/api/v1/ping", True), ("http://127.0.0.1:8095/api/v1/ping", False)):
        try:
            if (net.get_json(url, timeout=3, insecure=insecure) or {}).get("ok"):
                return url
        except net.HttpError:
            pass
    return ""


def main():
    host = Host()
    if host.system != "windows":
        fail("this only runs on Windows")
    sources = Sources()
    recipe = load(host, sources)["nova"]
    info = recipe.info("install")
    say(f"{info['title']} · support {info['support']['level']} · {info['release'] and info['release']['tag']}")
    for line in info["summary"]:
        say(" › " + line)
    if info["blocked"]:
        fail("blocked: " + info["blocked"])
    if info["support"]["level"] != "limited":
        fail("expected Nova on Windows to be a preview (limited)")
    installer = Installer(host, sources, {"nova": recipe}, spawn=Runner().spawn)

    run(installer, "install", {"firewall": True})
    got = recipe.detect()
    say(f"detected: {got}")
    if not got["installed"]:
        fail("Orbit doesn't see Nova after installing it")
    deadline = time.time() + 60
    while not answers() and time.time() < deadline:
        time.sleep(2)
    if not answers():
        fail("Nova doesn't answer after the install")
    say("answers at " + answers())

    cfg = Config(Path(tempfile.mkdtemp()) / "config.json")
    page = Nova(Ctx(cfg, Runner(), Releases(), Jobs()))
    st = page.status()
    say(f"Orbit's Nova page: {st['state']} · {st['headline']} · {st.get('version')} · {st['facts']}")
    if st["state"] != "ok":
        fail("Orbit's Nova page doesn't show Nova running")
    acts = [a["id"] for a in page.actions()]
    say(f"actions: {acts}")
    if "win:add" not in acts or "gui" not in acts:
        fail("the Windows actions are missing")
    if not recipe.win_shortcut.exists():
        fail(f"no Start menu entry at {recipe.win_shortcut}")

    snap = run(installer, "install", {"firewall": True})     # over itself: an update keeps settings and devices
    if not snap["result"]["headline"].startswith(("Nova is updated", "Nova is installed")):
        fail("unexpected result after installing again")
    if not answers():
        fail("Nova doesn't answer after installing again")

    run(installer, "uninstall", {"purge": True})
    recipe.forget()
    if recipe.detect()["installed"]:
        fail("Nova is still there after removing it")
    left = [p for p in (recipe.win_dir, Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "Nova") if p.exists()]
    if left:
        fail(f"left behind: {left}")
    say("\nNova installed, answered, updated and was removed through Orbit.")


# What each app is installed with, and removed with, through the program (the window's own choices).
APPS = {"nova": ({"firewall": True}, {"purge": True}),
        "ytm-starcast": ({}, {"purge": True})}

# The apps YTM Starcast replaces, at fixed releases: Pocketfader's last one (in YTM Starcast's repository, its new
# name) and YTM Beacon's (its repository stays readable). (repository, tag, Windows file, checksum list)
LEGACY_POCKETFADER = ("dash1101/ytm-starcast", "v0.1.0-alpha.15", "pocketfader-v0.1.0-alpha.15-windows-x64.zip",
                      "SHA256SUMS.txt")
LEGACY_BEACON = ("dash1101/ytm-beacon", "v0.1.4", "ytm-beacon_windows_amd64.zip", "checksums.txt")
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
BEACON_PORTS = (41713, 41714, 41715, 41716, 41717)


def fetch_release_file(repo, tag, name, sums_name, folder):
    """One file of a given release, checked against that release's checksum list (as Orbit checks its downloads)."""
    import urllib.parse
    from orbit.install.engine import parse_sums, sha256_file
    sources = Sources()
    rel = net.get_json(f"{sources.api}/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}", timeout=20,
                       headers=sources._api_headers())
    assets = {a["name"]: {"url": a.get("browser_download_url", ""), "api": a.get("url", "")}
              for a in rel.get("assets") or [] if isinstance(a, dict) and a.get("name")}
    for n in (name, sums_name):
        if n not in assets:
            fail(f"{repo} {tag} has no {n}")
        if not sources.allowed(assets[n]["url"], repo):
            fail(f"{n} isn't hosted where Orbit expects: {assets[n]['url']}")
    with sources.open_asset(assets[sums_name], repo) as r:
        sums = parse_sums(r.read(1 << 20).decode("utf-8", "replace"))
    dest = Path(folder) / name
    with sources.open_asset(assets[name], repo) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    if sums.get(name) != sha256_file(dest):
        fail(f"{name} from {repo} {tag} doesn't match its published checksum")
    say(f"downloaded {repo} {tag} {name} (checksum OK)")
    return dest


def beacon_answers():
    for port in BEACON_PORTS:
        try:
            if (net.get_json(f"http://127.0.0.1:{port}/v1/ping", timeout=2) or {}).get("app") == "ytm-beacon":
                return port
        except net.HttpError:
            pass
    return 0


def starcast_info():
    """What answers on YTM Starcast's port (Pocketfader before the move), or {}."""
    net.forget_ports()
    try:
        return net.get_json("http://127.0.0.1:8777/api/info", timeout=3) or {}
    except net.HttpError:
        return {}


def windows_query(argv):
    """Exit code of a command that only looks (schtasks /Query, reg query)."""
    import subprocess
    return subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL).returncode


def put_legacy_apps():
    """This machine as a person's PC was before YTM Starcast: Pocketfader where Orbit 0.3 put it (its program in
    %LOCALAPPDATA%\\Programs\\Pocketfader, its scheduled task, running), and YTM Beacon through its own installer."""
    import json
    import shutil
    import subprocess
    from orbit.install.engine import safe_extract
    local = Path(os.environ["LOCALAPPDATA"])
    work = Path(tempfile.mkdtemp())

    say("\n===== putting Pocketfader v0.1.0-alpha.15 in place, as Orbit 0.3 did")
    repo, tag, name, sums_name = LEGACY_POCKETFADER
    safe_extract(fetch_release_file(repo, tag, name, sums_name, work), work / "pf")
    exe = next((work / "pf").rglob("pocketfader.exe"), None)
    if not exe:
        fail(f"{name} has no pocketfader.exe")
    dest = local / "Programs" / "Pocketfader"
    shutil.copytree(exe.parent, dest, dirs_exist_ok=True)
    (dest / "orbit-install.json").write_text(json.dumps({"version": tag.lstrip("v")}), encoding="utf-8")
    ps = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    subprocess.run(ps + ["-File", str(dest / "install-exe.ps1")], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(ps + ["-Command", "Start-ScheduledTask -TaskName Pocketfader"], check=True, stdin=subprocess.DEVNULL)
    deadline = time.time() + 90
    while not starcast_info() and time.time() < deadline:
        time.sleep(2)
    if not starcast_info():
        fail("Pocketfader doesn't answer on port 8777")
    say(f"Pocketfader answers: {starcast_info()}")

    say("\n===== installing YTM Beacon v0.1.4 with its own installer")
    repo, tag, name, sums_name = LEGACY_BEACON
    safe_extract(fetch_release_file(repo, tag, name, sums_name, work), work / "beacon")
    exe = next((work / "beacon").rglob("ytm-beacon.exe"), None)
    if not exe:
        fail(f"{name} has no ytm-beacon.exe")
    p = subprocess.run([str(exe), "install", "--no-open"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=180)
    say(p.stdout + p.stderr)
    if p.returncode != 0:
        fail(f"YTM Beacon's installer stopped with code {p.returncode}")
    deadline = time.time() + 60
    while not beacon_answers() and time.time() < deadline:
        time.sleep(2)
    if not beacon_answers():
        fail("YTM Beacon doesn't answer after installing it")
    if not (local / "ytm-beacon" / "ytm-beacon.exe").exists():
        fail("YTM Beacon isn't where Orbit looks for it (%LOCALAPPDATA%\\ytm-beacon\\ytm-beacon.exe)")
    say(f"YTM Beacon answers on port {beacon_answers()}")


def check_moved():
    """After the move: Pocketfader and YTM Beacon are gone, nothing of theirs starts at logon, YTM Starcast
    answers."""
    local = Path(os.environ["LOCALAPPDATA"])
    if windows_query(["schtasks", "/Query", "/TN", "Pocketfader"]) == 0:
        fail("Pocketfader's scheduled task is still there")
    if (local / "Programs" / "Pocketfader").exists():
        fail("Pocketfader's program folder is still there")
    beacon = local / "ytm-beacon" / "ytm-beacon.exe"
    deadline = time.time() + 30
    while beacon.exists() and time.time() < deadline:
        time.sleep(1)
    if beacon.exists():
        fail("YTM Beacon's program is still there")
    if beacon_answers():
        fail("YTM Beacon still answers")
    if windows_query(["reg", "query", RUN_KEY, "/v", "YTMBeacon"]) == 0:
        fail("YTM Beacon still starts at login")
    if windows_query(["schtasks", "/Query", "/TN", "YTM Starcast"]) != 0:
        fail("YTM Starcast's scheduled task is missing")

    def starcast():
        info = starcast_info()
        return info if info and not str(info.get("version", "")).startswith("0.1.") else None
    deadline = time.time() + 120
    while not starcast() and time.time() < deadline:
        time.sleep(2)
    info = starcast()
    if not info:
        fail(f"YTM Starcast doesn't answer on port 8777 (it says {starcast_info()})")
    say(f"moved: Pocketfader and YTM Beacon are gone, YTM Starcast {info.get('version')} answers")


def check_family_gone():
    """After removing YTM Starcast with its settings: nothing of YTM Starcast, Pocketfader or YTM Beacon is left."""
    local, roaming = Path(os.environ["LOCALAPPDATA"]), Path(os.environ["APPDATA"])
    left = [p for p in (local / "Programs" / "YTM Starcast", local / "Programs" / "Pocketfader", local / "ytm-beacon",
                        roaming / "ytm-starcast", roaming / "pocketfader", roaming / "ytm-beacon") if p.exists()]
    if left:
        fail(f"left behind: {left}")
    for task in ("YTM Starcast", "Pocketfader"):
        if windows_query(["schtasks", "/Query", "/TN", task]) == 0:
            fail(f"the scheduled task {task} is still there")
    deadline = time.time() + 20
    while starcast_info() and time.time() < deadline:
        time.sleep(1)
    if starcast_info():
        fail("something still answers on port 8777")


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
$all = $log + ".all"; $pass = 0
$seen = @{}
Set-Content -LiteralPath $ready -Value "ready"
while (-not (Test-Path -LiteralPath $stop)) {
  [W]::EnumWindows({ param($h, $l)
    if ([W]::IsWindowVisible($h) -and -not $seen.ContainsKey($h)) {
      $c = New-Object Text.StringBuilder 256; [void][W]::GetClassName($h, $c, 256)
      if ($c.ToString() -in @('ConsoleWindowClass', 'CASCADIA_HOSTING_WINDOW_CLASS', 'PseudoConsoleWindow')) {
        $t = New-Object Text.StringBuilder 512; [void][W]::GetWindowText($h, $t, 512)
        $seen[$h] = 1; Add-Content -LiteralPath $log -Value ("{0:HH:mm:ss.fff}|{1}|{2}" -f (Get-Date), $c, $t)
      } else {
        $t = New-Object Text.StringBuilder 512; [void][W]::GetWindowText($h, $t, 512)
        $seen[$h] = 1; Add-Content -LiteralPath $all -Value ("{0}|{1}" -f $c, $t)
      }
    }
    $true }, [IntPtr]::Zero) | Out-Null
  $pass++; if ($pass % 100 -eq 1) { Add-Content -LiteralPath $all -Value ("pass {0}: {1} windows seen so far, session {2}" -f $pass, $seen.Count, (Get-Process -Id $PID).SessionId) }
  Start-Sleep -Milliseconds 80
}
"""


def watch_windows(folder):
    """Windows: start recording every console window that opens on the desktop (what a person would see flash up),
    from now until the returned function is called; it returns them, as (time, class, title) lines."""
    import subprocess
    if os.name != "nt":
        return lambda: []
    script, log, ready, stop = (folder / n for n in ("watch.ps1", "windows.txt", "ready.txt", "stop.txt"))
    script.write_text(WATCH)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(log), str(ready), str(stop)],
                     creationflags=0x08000000)   # CREATE_NO_WINDOW: the watcher itself shows nothing
    end = time.time() + 90
    while not ready.exists() and time.time() < end:
        time.sleep(0.3)
    if not ready.exists():
        fail("the window watcher didn't start")
    # it sees a console window on this desktop: open one on purpose (or "none seen" would prove nothing)
    subprocess.Popen(["cmd", "/c", "title orbit-watch-check & ping -n 4 127.0.0.1 >nul"], creationflags=0x00000010)  # CREATE_NEW_CONSOLE
    end = time.time() + 20
    while time.time() < end and not (log.exists() and "orbit-watch-check" in log.read_text(errors="replace")):
        time.sleep(0.3)
    if not (log.exists() and "orbit-watch-check" in log.read_text(errors="replace")):
        other = Path(str(log) + ".all")
        say("what the watcher saw:\n" + (other.read_text(errors="replace")[-3000:] if other.exists() else "(nothing at all)"))
        fail("the window watcher doesn't see console windows on this machine")
    say("window watcher: it sees console windows here (the check window was seen)")

    def done():
        stop.write_text("stop")
        time.sleep(1)
        return [ln for ln in log.read_text(errors="replace").splitlines() if ln.strip() and "orbit-watch-check" not in ln] if log.exists() else []
    return done


def through_the_program(exe, apps, migrate=False):
    """Install each app through the standalone program's own API (what its install window calls), check that it
    answers and that Orbit's page for it sees that, then remove it and check it's gone. With `migrate` (YTM Starcast
    only): Pocketfader and YTM Beacon are put in place first, the install is the move from them, and the removal takes
    everything of all three with it."""
    import json
    import socket
    import subprocess
    import urllib.error
    import urllib.request

    if migrate:
        if list(apps) != ["ytm-starcast"]:
            fail("--migrate is for ytm-starcast alone")
        put_legacy_apps()

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
    # Orbit keeps its own settings in a folder of its own here; for the move it sees the machine's real settings
    # folder, where Pocketfader, YTM Beacon and YTM Starcast keep theirs, so that removing them with their settings
    # is checked
    env = dict(os.environ) if migrate else dict(os.environ, APPDATA=str(home))
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
            if migrate:
                found = listing(app)
                inst = found["installed"]
                say(f"before the move: {inst} · update offered: {found.get('update')}")
                if not (inst["installed"] and inst.get("migrate") and inst.get("how") == "Pocketfader (its old name)"
                        and inst.get("legacy", {}).get("beacon")):
                    fail("Orbit doesn't see Pocketfader and YTM Beacon as something to move to YTM Starcast")
                # the card offers the move once Orbit has learned the latest release, which it looks up in the
                # background after it starts
                until("YTM Starcast's card to offer the move", lambda: listing(app).get("update"), 120)
                old = call(f"/api/apps/{app}")["status"]
                say(f"Orbit's {app} page before the move: {old['state']} · {old['headline']} · {old.get('version')}")
            elif installed(app):
                fail(f"{app} is already installed here")
            job(app, "install", install)
            if migrate:
                check_moved()
                after = listing(app)["installed"]
                if after.get("migrate") or after.get("how") != "standalone program":
                    fail(f"after the move Orbit still sees something to move: {after}")
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
            if migrate:
                check_family_gone()
                say("Pocketfader and YTM Beacon moved to YTM Starcast, and YTM Starcast removed with its settings, "
                    "through the standalone Orbit.")
            say(f"{app}: installed, answered and removed through the standalone Orbit.")
        say(f"\nAll of {', '.join(apps)}: installed, answered and removed through the standalone Orbit.")
        polling["on"] = False
        seen = windows()
        theirs = [w for w in seen if "ytm-starcast" in w.lower()]
        ours = [w for w in seen if w not in theirs]
        for w in theirs:
            say(f"::warning::a console window of YTM Starcast's own: {w}")
        if ours:
            fail("console windows opened during the run (Orbit should run everything without one):\n  " + "\n  ".join(ours))
        say(f"No console window from Orbit during the run ({len(theirs)} of YTM Starcast's own)." if os.name == "nt" else "")
    finally:
        polling["on"] = False
        p.kill()
        try:
            say("--- Orbit's output:\n" + (home / "orbit.log").read_text(errors="replace")[-2000:])
        except OSError:
            pass


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--migrate"]
    if len(args) > 1 and args[0] == "--exe":
        through_the_program(args[1], args[2].split(",") if len(args) > 2 else ["nova"], migrate="--migrate" in sys.argv)
    else:
        main()
