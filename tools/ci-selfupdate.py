"""For CI only: a real standalone Orbit updates itself to a newer build and restarts on it.

    python tools/ci-selfupdate.py <this build> <a build that says 99.0.0> <asset suffix, e.g. linux-x86_64>

    python tools/ci-selfupdate.py --real <a published Orbit> <the newest version>

A stand-in for GitHub (on 127.0.0.1, named to Orbit by ORBIT_TEST_GITHUB_API) publishes the newer build as release
v99.0.0 with its SHA256SUMS, under the name releases use (orbit-linux-x86_64 …). In the "restart" run the program
starts out named the way downloads before 0.3.5 were (orbit-v1.0.0-linux-x86_64), and must end up under the plain name,
with Orbit's own menu entry following it (Linux and Windows). With --real, an Orbit downloaded from an earlier release updates itself from the real
GitHub releases instead. A copy of this build is started, asked through its own API to update itself (exactly
what the window's "update" button does), then to restart; the newer Orbit must come up on the same port and the
old program must be gone. Run by the build workflow on Linux and Windows.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REAL = len(sys.argv) > 1 and sys.argv[1] == "--real"
DEB = len(sys.argv) > 1 and sys.argv[1] == "--deb"
if DEB:
    # python tools/ci-selfupdate.py --deb <this build's .deb> <a .deb that says 99.0.0> <asset suffix, e.g. linux-x86_64>
    old_deb, new_deb, suffix = sys.argv[2:5]
    old_exe = "/usr/bin/orbit"
    WANT, TAG = "99.0.0", "v99.0.0"
    NAME = f"orbit-{suffix}.deb"
    DATA = Path(new_deb).read_bytes()
    SUMS = f"{hashlib.sha256(DATA).hexdigest()}  {NAME}\n".encode()
elif REAL:
    old_exe, WANT = sys.argv[2:4]
else:
    old_exe, new_exe, suffix = sys.argv[1:4]
    WANT = "99.0.0"
    TAG = "v99.0.0"
    NAME = f"orbit-{suffix}"
    DATA = Path(new_exe).read_bytes()
    SUMS = f"{hashlib.sha256(DATA).hexdigest()}  {NAME}\n".encode()


def say(text):
    print(text, flush=True)


def fail(text):
    say(f"::error::{text}")
    sys.exit(1)


class GitHub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        path = self.path.split("?", 1)[0]
        if path == "/repos/dash1101/orbit/releases":
            assets = [{"name": n, "size": len(b), "url": f"{base}/api-assets/{n}", "browser_download_url": f"{base}/files/{n}"}
                      for n, b in ((NAME, DATA), ("SHA256SUMS", SUMS))]
            rel = [{"tag_name": TAG, "draft": False, "prerelease": True, "published_at": "2026-11-11T00:00:00Z",
                    "html_url": "", "assets": assets}]
            return self.send(200, json.dumps(rel).encode(), "application/json")
        if path == f"/files/{NAME}":
            return self.send(200, DATA)
        if path == "/files/SHA256SUMS":
            return self.send(200, SUMS)
        self.send(404, b"{}", "application/json")


def beside(path, suffix):
    """Where Orbit keeps the new and old copies (the same rule as orbit/install/recipes/orbit.py)."""
    if path.suffix == ".app":
        s = path.stem
        return path.with_name({".new": f".{s}-new.app", ".old": f".{s}-old.app", ".new.ok": f".{s}-new.ok"}[suffix])
    return path.with_name(path.name + suffix)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port, path, body=None, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{port}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_for(what, check, seconds=90, quiet=False):
    end = time.time() + seconds
    while time.time() < end:
        try:
            got = check()
            if got:
                return got
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    if quiet:
        return None
    fail(f"timed out waiting for {what}")


def borrowed_files(app):
    """Linux: unpacked files the running Orbits use that no longer exist (they were another, ended Orbit's)."""
    if not sys.platform.startswith("linux"):
        return []
    gone = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if not os.readlink(f"/proc/{pid}/exe").startswith(str(app)):
                continue
            with open(f"/proc/{pid}/maps") as f:
                for line in f:
                    path = line.split(None, 5)[-1].strip() if len(line.split()) >= 6 else ""
                    if "/_MEI" in path and (path.endswith("(deleted)") or not os.path.exists(path)):
                        gone.add(path)
        except OSError:
            pass
    return sorted(gone)


def show_processes():
    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'orbit*' } | "
              "Format-Table -AutoSize ProcessId, ParentProcessId, ExecutablePath, CommandLine | Out-String -Width 400")
        say(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout)
    else:
        say(subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout)


def stop_everything(app):
    if os.name == "nt":
        # by program name: every name it has during a run (as downloaded, with or without a version)
        names = {Path(old_exe).name} | ({f"orbit-{suffix}", f"orbit-v1.0.0-{suffix}"} if not REAL else set())
        for name in sorted(names):
            subprocess.run(["taskkill", "/F", "/T", "/IM", name], capture_output=True)
        return
    if sys.platform == "darwin":
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
        for line in out.splitlines():
            pid, _, cmd = line.strip().partition(" ")
            if cmd.startswith(str(app)) and pid.isdigit():
                try:
                    os.kill(int(pid), 15)
                except OSError:
                    pass
        return
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if os.readlink(f"/proc/{pid}/exe").startswith(str(app)):
                    os.kill(int(pid), 15)
            except OSError:
                pass


def scenario(gh, how):
    """how = "restart" (the window's restart button), "reopen" (Orbit closed, then started again) or "auto" (nobody
    presses anything: Orbit downloads the update by itself after it starts, and it takes over when Orbit is reopened)."""
    say(f"\n===== update, then {how}")
    root = Path(tempfile.mkdtemp(prefix="orbit-selfupdate-"))
    app = root / "app"
    app.mkdir()
    if old_exe.endswith(".app"):          # macOS: the whole app, as dragged into Applications
        bundle = app / Path(old_exe).name
        shutil.copytree(old_exe, bundle, symlinks=True)
        exe = bundle / "Contents" / "MacOS" / "Orbit"
        original = Path(old_exe) / "Contents" / "MacOS" / "Orbit"
    else:
        name = Path(old_exe).name
        if not REAL:
            name = f"orbit-v1.0.0-{suffix}" if how == "restart" else f"orbit-{suffix}"
        bundle = exe = app / name
        shutil.copy2(old_exe, exe)
        original = Path(old_exe)
    # where the program is after the update: the same place, or (named with a version) the plain name
    final = bundle if REAL or bundle.suffix == ".app" else app / f"orbit-{suffix}"
    final_exe = exe if final == bundle else final
    port = free_port()
    env = dict(os.environ, XDG_CONFIG_HOME=str(root / "config"), APPDATA=str(root / "config"),
               XDG_DATA_HOME=str(root / "data"))
    menu = sys.platform.startswith("linux") or sys.platform == "win32"
    if gh is not None:
        env["ORBIT_TEST_GITHUB_API"] = f"http://127.0.0.1:{gh.server_address[1]}"

    def start(log):
        say(f"starting {exe} on port {port}")
        return subprocess.Popen([str(exe), "--no-window", "--port", str(port)], env=env, stdin=subprocess.DEVNULL,
                                stdout=open(root / log, "w"), stderr=subprocess.STDOUT)

    first = start("first.log")
    try:
        hub = wait_for("Orbit to start", lambda: call(port, "/api/hub"))
        say(f"running Orbit {hub['version']}")
        if menu:
            # (on Windows this starts PowerShell, which takes a while the first time on a fresh machine)
            say("menu entry: " + call(port, "/api/hub/menu", {}, timeout=60).get("message", ""))
        if how == "auto":
            tag = wait_for("Orbit to get the update ready by itself",
                           lambda: (lambda t: t if t.get("pending") else None)(call(port, "/api/hub").get("orbitUpdate") or {}), 180)
            say(f"by itself, the window offers: {tag}")
        plan = call(port, "/api/install/orbit/plan", {"op": "install"}) if how != "auto" else {"title": f"Update Orbit to {WANT}", "blocked": ""}
        say(f"plan: {plan['title']} · blocked: {plan['blocked'] or 'no'}")
        if plan["blocked"] or plan["title"] != f"Update Orbit to {WANT}":
            fail("the update isn't offered")
        if how != "auto":
            job = call(port, "/api/install/orbit/start", {"op": "install"})
            snap = wait_for("the update", lambda: (lambda j: j if j["state"] != "running" else None)(
                call(port, f"/api/install/jobs/{job['id']}")), 180)
            for line in snap["log"]:
                say("  " + line)
            if snap["state"] != "done":
                fail(f"update {snap['state']}: {snap['error']} {snap['hint']}")
        old = beside(final, ".old")
        if not beside(bundle, ".new").exists() or not beside(bundle, ".new.ok").exists():
            fail("the new program isn't waiting next to the running one")
        if exe.read_bytes() != original.read_bytes():
            fail("the running program's file changed before the restart")
        tag = call(port, "/api/hub").get("orbitUpdate") or {}
        say(f"the window offers: {tag}")
        if not tag.get("pending") or tag.get("version") != WANT:
            fail("the window doesn't offer the restart")
        if how == "restart":
            call(port, "/api/hub/restart", {})
        else:
            stop_everything(app)
            wait_for("the port to be free", lambda: not port_busy(port), 30)
            start("second.log")
        hub = wait_for("the new Orbit on the same port",
                       lambda: (lambda h: h if h["version"] == WANT else None)(call(port, "/api/hub")), 90)
        say(f"Orbit {hub['version']} answers on port {port}")
        if how == "restart":
            try:
                first.wait(30)
                say(f"the first Orbit ended (exit {first.returncode})")
            except subprocess.TimeoutExpired:
                show_processes()
                fail("the first Orbit is still running 30 s after the restart")
        unpacked = borrowed_files(app)
        if unpacked:
            fail(f"the new Orbit uses unpacked files that are gone (another Orbit's): {unpacked}")
        out = root / "v.txt"
        subprocess.run([str(final_exe), "--write-version", str(out)], timeout=120)
        if out.read_text().strip() != f"orbit {WANT}":
            fail(f"the program in place says {out.read_text().strip()!r}")
        if not wait_for("the old program to be deleted", lambda: not old.exists(), 90, quiet=True):
            show_processes()
            fail("the old program is still there")
        left = sorted(x.name for x in app.iterdir())
        if left != [final.name]:
            fail(f"left next to Orbit: {left} (expected only {final.name})")
        if menu:
            def follows():
                try:
                    return os.path.samefile(menu_target(root), final_exe)   # the same file, however its path is written
                except OSError:
                    return False
            # the new Orbit updates its menu entry by itself once it runs (on Windows that takes PowerShell a while)
            same = wait_for("the menu entry to follow", follows, 90, quiet=True)
            say(f"the menu entry starts: {menu_target(root)}")
            if not same:
                fail(f"the menu entry doesn't start {final_exe}")
        say(f"{how}: the program in place is {WANT}, as {final.name}, and nothing else is left.")
    finally:
        stop_everything(app)
        for log in ("first.log", "second.log"):
            try:
                say(f"--- {log}:\n" + (root / log).read_text(errors="replace")[-2000:])
            except OSError:
                pass


def menu_target(root):
    """The program Orbit's own menu entry (apps menu, Start menu) starts."""
    if sys.platform == "win32":
        link = root / "config" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Orbit.lnk"
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"(New-Object -ComObject WScript.Shell).CreateShortcut('{link}').TargetPath"],
                           capture_output=True, text=True)
        return p.stdout.strip()
    entry = root / "data" / "applications" / "orbit.desktop"
    for line in entry.read_text().splitlines():
        if line.startswith("Exec="):
            return line[5:].strip('"')
    return ""


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def deb_version():
    p = subprocess.run(["dpkg-query", "-W", "-f=${Status}|${Version}", "orbit"], capture_output=True, text=True)
    return p.stdout.rsplit("|", 1)[-1].strip() if "install ok installed" in p.stdout else ""


def deb_scenario(gh):
    """Orbit installed from its Debian package updates itself: it downloads the newer .deb, apt installs it in place,
    and a restart brings up the new version from /usr/bin/orbit."""
    say("\n===== the Debian package: update through apt, then restart")
    subprocess.run(["sudo", "apt-get", "install", "-y", "--allow-downgrades", os.path.abspath(old_deb)], check=True,
                   stdout=subprocess.DEVNULL)
    before = deb_version()
    say(f"installed the orbit package {before}")
    root = Path(tempfile.mkdtemp(prefix="orbit-deb-"))
    port = free_port()
    env = dict(os.environ, XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data"),
               ORBIT_TEST_GITHUB_API=f"http://127.0.0.1:{gh.server_address[1]}")
    first = subprocess.Popen(["/usr/bin/orbit", "--no-window", "--port", str(port)], env=env, stdin=subprocess.DEVNULL,
                             stdout=open(root / "first.log", "w"), stderr=subprocess.STDOUT)
    try:
        hub = wait_for("Orbit to start", lambda: call(port, "/api/hub"))
        say(f"running Orbit {hub['version']} from /usr/bin/orbit")
        plan = call(port, "/api/install/orbit/plan", {"op": "install"})
        say(f"plan: {plan['title']} · {plan['installed'].get('how')} · blocked: {plan['blocked'] or 'no'}")
        if plan["blocked"] or plan["installed"].get("how") != "the Debian package" or plan["title"] != f"Update Orbit to {WANT}":
            fail("the update through apt isn't offered")
        job = call(port, "/api/install/orbit/start", {"op": "install"})
        snap = wait_for("the update", lambda: (lambda j: j if j["state"] != "running" else None)(
            call(port, f"/api/install/jobs/{job['id']}")), 300)
        for line in snap["log"]:
            say("  " + line)
        if snap["state"] != "done":
            fail(f"update {snap['state']}: {snap['error']} {snap['hint']}")
        if deb_version() != WANT:
            fail(f"apt lists orbit {deb_version()}, not {WANT}")
        tag = call(port, "/api/hub").get("orbitUpdate") or {}
        if not tag.get("pending") or tag.get("version") != WANT:
            fail(f"the window doesn't offer the restart: {tag}")
        call(port, "/api/hub/restart", {})
        hub = wait_for("the new Orbit on the same port",
                       lambda: (lambda h: h if h["version"] == WANT else None)(call(port, "/api/hub")), 90)
        first.wait(30)
        say(f"deb: Orbit {hub['version']} answers from /usr/bin/orbit; the package is {deb_version()}")
    finally:
        subprocess.run(["pkill", "-x", "orbit"], capture_output=True)
        subprocess.run(["sudo", "apt-get", "remove", "-y", "orbit"], stdout=subprocess.DEVNULL)
        try:
            say("--- first.log:\n" + (root / "first.log").read_text(errors="replace")[-1500:])
        except OSError:
            pass


def main():
    gh = None
    if DEB:
        gh = ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
        threading.Thread(target=gh.serve_forever, daemon=True).start()
        deb_scenario(gh)
        say("\nThe Debian package updates itself through apt and restarts on the new version.")
        return
    if not REAL:
        gh = ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
        threading.Thread(target=gh.serve_forever, daemon=True).start()
    scenario(gh, "restart")
    scenario(gh, "reopen")
    if not REAL:
        scenario(gh, "auto")
    say("\nSelf-update works: after a restart from the window, and after closing Orbit and opening it again.")


if __name__ == "__main__":
    main()
