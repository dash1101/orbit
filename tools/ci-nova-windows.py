"""For CI only: Orbit's installer puts Nova on this Windows machine, for real, and takes it off again.

Runs on GitHub's Windows machines (.github/workflows/nova-windows.yml), never on a person's PC: it installs
Nova's newest release (asking Windows for administrator rights the way it would on a PC, which GitHub's machines
grant without a prompt), checks that Nova answers and that Orbit's Nova page sees it, installs again over
itself (an update), then removes it with its settings and makes sure nothing is left.

    python tools/ci-nova-windows.py                   through Orbit's Python code
    python tools/ci-nova-windows.py --exe orbit.exe [nova,pocketfader,ytm-beacon]
                                                      through a built standalone Orbit, the way its window does it
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
        "pocketfader": ({}, {"purge": True}),
        "ytm-beacon": ({"setup": False}, {"purge": True})}


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

    def installed(app):
        return next(a for a in call("/api/install")["apps"] if a["id"] == app)["installed"]["installed"]

    home = Path(tempfile.mkdtemp())
    env = dict(os.environ, APPDATA=str(home))
    p = subprocess.Popen([exe, "--no-window", "--port", str(port)], env=env, stdin=subprocess.DEVNULL,
                         stdout=open(home / "orbit.log", "w"), stderr=subprocess.STDOUT)
    try:
        hub = until("Orbit to start", lambda: call("/api/hub"), 90)
        say(f"Orbit {hub['version']} on port {port}")
        for app in apps:
            install, remove = APPS[app]
            if installed(app):
                fail(f"{app} is already installed here")
            job(app, "install", install)
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
    finally:
        p.kill()
        try:
            say("--- Orbit's output:\n" + (home / "orbit.log").read_text(errors="replace")[-2000:])
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--exe":
        through_the_program(sys.argv[2], sys.argv[3].split(",") if len(sys.argv) > 3 else ["nova"])
    else:
        main()
