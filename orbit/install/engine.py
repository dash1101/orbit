"""The install engine: runs a recipe's steps one after another in the background and reports progress.

A recipe (see `recipes/`) turns "install Pocketfader on this Windows PC" into a list of small steps.
Each step gets a `Session` with everything it may do — download a release file, check its SHA-256,
unpack it, run a command, ask for the administrator password — and every one of those goes through
here, so that:

* dry-run is automatic: nothing is downloaded, written or run, but the steps (and their progress
  bars) play out the same way, with the would-be commands in the log;
* only release files from the app's own GitHub repository can be downloaded, and they are checked
  against the checksums published with the release before anything is unpacked or run;
* any step can be cancelled, and a failure stops the install with a plain-language reason.
"""
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .. import net

MAX_DOWNLOAD = 300 << 20
SKIP = "skip"


class Cancelled(Exception):
    pass


class Failed(Exception):
    """A step failed in a way the person can understand (and often fix): `hint` says how."""

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.hint = hint


class Step:
    def __init__(self, id, label, fn, weight=1.0):
        self.id, self.label, self.fn, self.weight = id, label, fn, weight


# ---- where releases come from ----------------------------------------------------------------
class Sources:
    """GitHub releases. `allow` lists the URL prefixes downloads may come from (tests point both at a stub).

    Private repositories work when this computer is signed in to GitHub (the `gh` command) or has
    GITHUB_TOKEN/GH_TOKEN set: the token is only ever sent to api.github.com, never to the file host
    a download is redirected to.
    """

    TTL = 600

    def __init__(self, api="https://api.github.com", allow=("https://github.com/",), timeout=8, token=None):
        self.api, self.allow, self.timeout = api.rstrip("/"), tuple(allow), timeout
        self._token = token            # callable() -> str or None; None = look for gh / env
        self._cache = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls):
        """GitHub, or (for Orbit's own build checks only) a stand-in named by ORBIT_TEST_GITHUB_API."""
        stand_in = os.environ.get("ORBIT_TEST_GITHUB_API", "").rstrip("/")
        if stand_in.startswith("http://127.0.0.1:"):
            return cls(api=stand_in, allow=(stand_in + "/",), token=lambda: "")
        return cls()

    def token(self):
        return (self._token() if self._token is not None else net.github_token()) or ""

    def _api_headers(self, accept="application/vnd.github+json"):
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        tok = self.token()
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        return h

    def latest(self, repo, force=False):
        """Newest non-draft release (pre-releases count): {"tag", "published", "assets": {name: {"url", "size"}}}."""
        with self._lock:
            hit = self._cache.get(repo)
            if hit and not force and time.time() - hit[0] < self.TTL:
                return hit[1]
        try:
            items = net.get_json(f"{self.api}/repos/{repo}/releases?per_page=5", timeout=self.timeout,
                                 headers=self._api_headers())
        except net.HttpError as e:
            if e.status == 404:
                raise Failed(f"Couldn't find {repo} on GitHub.",
                             "If it's a private repository, sign in with GitHub in Orbit's settings (or run "
                             "gh auth login), then try again.") from None
            if e.status in (401, 403):
                raise Failed("GitHub turned the request down for now.",
                             "Without a sign-in GitHub answers about 60 requests an hour per network. Wait a few "
                             "minutes and try again. If you signed in, your sign-in may have expired: sign in again "
                             "in Orbit's settings.") from None
            raise Failed("Couldn't reach GitHub to find the latest release.",
                         "Check your internet connection and try again.") from None
        rel = net.newest_release(items)
        if not rel:
            raise Failed(f"{repo} has no releases to install yet.")
        info = {"tag": str(rel.get("tag_name", "")), "published": str(rel.get("published_at", "")),
                "url": str(rel.get("html_url", "")), "prerelease": bool(rel.get("prerelease")),
                "assets": {a["name"]: {"url": a.get("browser_download_url", ""), "api": a.get("url", ""),
                                       "size": int(a.get("size") or 0)}
                           for a in rel.get("assets") or [] if isinstance(a, dict) and a.get("name")}}
        with self._lock:
            self._cache[repo] = (time.time(), info)
        return info

    def cached(self, repo):
        with self._lock:
            hit = self._cache.get(repo)
            return hit[1] if hit else None

    def allowed(self, url, repo):
        if not isinstance(url, str):
            return False
        if self.allow == ("https://github.com/",):
            return url.startswith(f"https://github.com/{repo}/releases/download/")
        return url.startswith(self.allow)

    def open_asset(self, asset, repo):
        """Open a release file for reading: through the API (with the token) when there is one, else the public link."""
        api = asset.get("api") or ""
        if self.token() and api.startswith(f"{self.api}/repos/{repo}/releases/assets/"):
            req = urllib.request.Request(api, headers=dict(self._api_headers("application/octet-stream"),
                                                           **{"User-Agent": net.UA}))
            return _opener.open(req, timeout=30)
        req = urllib.request.Request(asset["url"], headers={"User-Agent": net.UA})
        return _opener.open(req, timeout=30)


class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Follow GitHub's redirect to its file host without carrying the token along."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            for k in list(new.headers):
                if k.lower() == "authorization":
                    del new.headers[k]
            new.headers.pop("Accept", None)
        return new


_opener = urllib.request.build_opener(_NoAuthOnRedirect)


# ---- what a step can do ---------------------------------------------------------------------
class Session:
    def __init__(self, job, host, sources, dry_run=False, spawn=None, pace=1.0):
        self.job, self.host, self.sources, self.dry_run = job, host, sources, dry_run
        self.pace = pace
        self.vars = {}
        self._tmp = None
        self._proc = None
        self._spawn = spawn

    # ---- reporting
    def log(self, text):
        self.job.add_log(text)

    def detail(self, text):
        self.job.set_step(detail=text)

    def progress(self, frac):
        self.job.set_step(progress=None if frac is None else max(0.0, min(1.0, frac)))

    def check(self):
        if self.job.cancel_requested:
            raise Cancelled()

    def sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.check()
            time.sleep(min(0.1, max(0, end - time.time())))

    def pause(self, seconds):
        """A dry run's stand-in for real work, so its steps (and their animation) can be followed."""
        self.sleep(seconds * self.pace)

    # ---- temporary folder (removed when the job ends)
    @property
    def tmp(self):
        if self._tmp is None:
            self._tmp = Path(tempfile.mkdtemp(prefix="orbit-install-"))
            # world-readable so tools that drop privileges (apt's sandbox user) can read a package from it
            os.chmod(self._tmp, 0o755)
        return self._tmp

    def cleanup(self):
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    # ---- releases
    def release(self, repo):
        self.detail("asking GitHub for the latest release")
        rel = self.sources.latest(repo, force=True)
        self.log(f"Latest release of {repo}: {rel['tag']}")
        return rel

    def asset(self, rel, name, repo):
        a = rel["assets"].get(name)
        if not a:
            raise Failed(f"The {rel['tag']} release doesn't include {name}.",
                         "It may still be uploading; try again in a few minutes.")
        if not self.sources.allowed(a["url"], repo):
            raise Failed(f"{name} isn't hosted where Orbit expects, so it won't be downloaded.")
        return a

    def download(self, rel, name, repo, dest_name=None):
        """Download one release file into the temporary folder, with progress. Returns its path."""
        a = self.asset(rel, name, repo)
        dest = self.tmp / (dest_name or name)
        size = a["size"]
        if size > MAX_DOWNLOAD:
            raise Failed(f"{name} is unexpectedly large ({size >> 20} MB); not downloading it.")
        self.detail(f"{name} · {fmt_size(size)}" if size else name)
        self.log(f"Downloading {a['url']}")
        if self.dry_run:
            steps = 12
            for i in range(steps + 1):
                self.pause(0.07)
                self.progress(i / steps)
                self.job.set_step(bytes=int(size * i / steps), total=size)
            self.log(f"dry run: would save {name} ({fmt_size(size)})")
            return dest
        got = 0
        h = hashlib.sha256()
        try:
            with self.sources.open_asset(a, repo) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or size or 0)
                while True:
                    self.check()
                    chunk = r.read(64 << 10)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_DOWNLOAD:
                        raise Failed(f"{name} is larger than expected; stopped downloading it.")
                    f.write(chunk)
                    h.update(chunk)
                    self.job.set_step(bytes=got, total=total, progress=(got / total) if total else None)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Failed(f"The download of {name} stopped: {getattr(e, 'reason', e)}",
                         "Check your internet connection and try again.") from None
        self.vars.setdefault("_sha", {})[str(dest)] = h.hexdigest()
        self.log(f"Saved {name} ({fmt_size(got)})")
        return dest

    def fetch_text(self, rel, name, repo):
        """A small release file (checksums, signature) as text."""
        a = self.asset(rel, name, repo)
        if self.dry_run:
            self.log(f"dry run: would read {name}")
            return ""
        try:
            with self.sources.open_asset(a, repo) as r:
                return r.read(1 << 20).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as e:
            raise Failed(f"Couldn't download {name}: {getattr(e, 'reason', e)}",
                         "Check your internet connection and try again.") from None

    def verify(self, path, sums_text, name=None):
        """Check a downloaded file against a `sha256  name` list (sha256sum format)."""
        name = name or Path(path).name
        self.detail(f"SHA-256 of {name}")
        if self.dry_run:
            self.pause(0.4)
            self.log(f"dry run: would check {name} against the release's checksums")
            return True
        expected = parse_sums(sums_text).get(name)
        if not expected:
            raise Failed(f"The release doesn't list a checksum for {name}, so it won't be installed.")
        actual = self.vars.get("_sha", {}).get(str(path)) or sha256_file(path)
        if actual != expected:
            raise Failed(f"{name} doesn't match its published checksum, so it won't be installed.",
                         "The download may be damaged. Try again; if it keeps happening, tell the app's author.")
        self.log(f"Checksum OK: {name} {actual[:16]}…")
        return True

    def extract(self, archive, dest=None):
        """Unpack a .tar.gz or .zip into the temporary folder (refusing any path that would land outside it)."""
        dest = Path(dest) if dest else self.tmp / (Path(archive).name + ".d")
        self.detail(Path(archive).name)
        if self.dry_run:
            self.pause(0.3)
            self.log(f"dry run: would unpack {Path(archive).name}")
            return dest
        dest.mkdir(parents=True, exist_ok=True)
        try:
            safe_extract(archive, dest)
        except (tarfile.TarError, zipfile.BadZipFile, OSError, ValueError) as e:
            raise Failed(f"Couldn't unpack {Path(archive).name}: {e}") from None
        return dest

    def find(self, folder, filename):
        """The first file called `filename` anywhere under `folder`."""
        if self.dry_run:
            return Path(folder) / filename
        for root, _dirs, files in os.walk(folder):
            if filename in files:
                return Path(root) / filename
        raise Failed(f"The download doesn't contain {filename}.")

    # ---- changing things
    def run(self, argv, ok=(0,), env=None, cwd=None, timeout=900, quiet=False, fail=None, hint=""):
        """Run a command, streaming its output into the log. Returns (exit code, output)."""
        argv = [str(a) for a in argv]
        shown = " ".join(_q(a) for a in argv)
        if self.dry_run:
            self.log(f"dry run: would run {shown}")
            self.pause(0.25)
            return 0, ""
        if not quiet:
            self.log(f"$ {shown}")
        full_env = dict(self.host.env if env is None else env)
        try:
            from ..proc import hidden
            kw = hidden()
            if sys.platform != "win32":
                kw["start_new_session"] = True     # no terminal: installers that would ask a question don't hang
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 cwd=cwd, env=full_env, **kw)
        except OSError as e:
            raise Failed(fail or f"Couldn't start {Path(argv[0]).name}: {e.strerror or e}", hint) from None
        self._proc = p
        out = []
        reader = threading.Thread(target=self._pump, args=(p, out, quiet), daemon=True)
        reader.start()
        start = time.time()
        try:
            while p.poll() is None:
                if self.job.cancel_requested:
                    p.terminate()
                    try:
                        p.wait(5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                    raise Cancelled()
                if time.time() - start > timeout:
                    p.kill()
                    raise Failed(f"{Path(argv[0]).name} took too long (over {timeout // 60} minutes) and was stopped.")
                time.sleep(0.1)
        finally:
            reader.join(2)
            self._proc = None
        text = "".join(out)
        if p.returncode not in ok:
            last = [ln for ln in text.strip().splitlines() if ln.strip()][-1:]
            raise Failed(fail or f"{Path(argv[0]).name} stopped with an error (code {p.returncode})"
                         + (f": {last[0].strip()[:200]}" if last else "."), hint)
        return p.returncode, text

    def _pump(self, p, out, quiet):
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace")
            out.append(line)
            if not quiet:
                self.log(strip_ansi(line.rstrip("\n")))
        p.stdout.close()

    def admin(self, argv, what, wait_for=None, hint=""):
        """Run a command as the administrator (Linux): the desktop's own password window via pkexec.

        Without pkexec, the command is opened in a terminal with sudo and the step waits until
        `wait_for()` says it worked.
        """
        argv = [str(a) for a in argv]
        if self.host.which("pkexec") and self.host.has_display:
            self.detail("waiting for your password in the system's window")
            self.log(f"Asking for permission to {what}")
            try:
                return self.run([self.host.tool("pkexec")] + argv, hint=hint)
            except Failed as e:
                if "code 126" in str(e) or "code 127" in str(e):
                    raise Failed("The password window was closed, so nothing was changed.",
                                 "Try again and enter your password when the system asks.") from None
                raise
        from ..runner import terminal_argv
        term = terminal_argv(["sudo"] + argv, title="Orbit")
        if not term or not wait_for:
            raise Failed(f"Orbit needs administrator rights to {what}, and can't ask for them here.",
                         "Run this in a terminal:  sudo " + " ".join(_q(a) for a in argv))
        self.detail("finish in the terminal window (it asks for your password)")
        self.log(f"Opened a terminal to {what}: sudo {' '.join(_q(a) for a in argv)}")
        if self.dry_run:
            self.pause(0.4)
            return 0, ""
        if not self._spawn(term):
            raise Failed("Couldn't open a terminal window.")
        deadline = time.time() + 30 * 60
        while time.time() < deadline:
            self.sleep(2)
            if wait_for():
                return 0, ""
        raise Failed("Gave up waiting for the terminal window.", "Run it again when you're ready.")

    def admin_windows(self, script, args=(), what="make the change", hint=""):
        """Run a PowerShell script as the administrator (Windows): Windows asks for permission once (the UAC
        prompt), the script runs without a window of its own, and what it prints lands in this job's log.

        The elevated PowerShell can't hand its output back directly, so it writes it to a file in the
        temporary folder that is read as it grows. Its exit code comes back through Start-Process.
        """
        script = Path(script)
        args = [str(a) for a in args]
        shown = " ".join([_q(str(script))] + args)
        self.detail("waiting for you to allow it in Windows' prompt")
        self.log(f"Asking Windows for permission to {what}: {shown}")
        if self.dry_run:
            self.pause(0.6)
            self.log(f"dry run: would run, as the administrator, powershell -File {shown}")
            return 0, ""
        log = self.tmp / "admin-output.log"
        run = self.tmp / "admin-run.ps1"
        lit = lambda s: "'" + str(s).replace("'", "''") + "'"
        # UTF-8 with a byte-order mark: Windows PowerShell 5 misreads UTF-8 without one (user names can have accents).
        run.write_text(
            "$ErrorActionPreference = 'Continue'\r\n"
            f"& powershell.exe -NoProfile -ExecutionPolicy Bypass -File {lit(script)} {' '.join(lit(a) for a in args)} "
            f"*>&1 | Out-File -FilePath {lit(log)} -Encoding utf8\r\n"
            "exit $LASTEXITCODE\r\n", encoding="utf-8-sig")
        outer = ("$ErrorActionPreference = 'Stop'; try { $p = Start-Process -FilePath powershell.exe -Verb RunAs "
                 "-WindowStyle Hidden -Wait -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', "
                 f"'-File', '\"{str(run).replace(chr(39), chr(39) * 2)}\"'); exit $p.ExitCode }} catch {{ Write-Output $_.Exception.Message; "
                 # 1223 = ERROR_CANCELLED: the person chose No in the prompt (checked by number, any language)
                 "$e = $_.Exception; while ($e) { if ($e.NativeErrorCode -eq 1223) { exit 1223 }; $e = $e.InnerException }; "
                 "exit 1 }")
        ps = self.host.which("powershell") or "powershell.exe"
        tail = {"pos": 0, "buf": b""}

        def drain():
            try:
                with open(log, "rb") as f:
                    f.seek(tail["pos"])
                    data = f.read()
            except OSError:
                return
            tail["pos"] += len(data)
            lines = (tail["buf"] + data).split(b"\n")
            tail["buf"] = lines.pop()
            for ln in lines:
                text = strip_ansi(ln.decode("utf-8", "replace").lstrip("﻿").rstrip("\r"))
                if text.strip():
                    self.log(text)
                    self.detail(text.strip()[:90])

        from ..proc import hidden
        try:
            p = subprocess.Popen([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", outer],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **hidden())
        except OSError as e:
            raise Failed(f"Couldn't start PowerShell: {e.strerror or e}", hint) from None
        self._proc = p
        try:
            while p.poll() is None:
                if self.job.cancel_requested:
                    # Only the waiting PowerShell can be stopped from here; the elevated script finishes by itself.
                    p.terminate()
                    raise Cancelled()
                drain()
                time.sleep(0.3)
        finally:
            self._proc = None
        drain()
        if tail["buf"].strip():
            self.log(strip_ansi(tail["buf"].decode("utf-8", "replace").lstrip("﻿").strip()))
        said = (p.stdout.read() or b"").decode("utf-8", "replace").strip()
        p.stdout.close()
        if p.returncode == 1223 or "canceled by the user" in said.lower() or "cancelled by the user" in said.lower():
            raise Failed("Windows' permission prompt was declined, so nothing was changed.",
                         "Try again and choose Yes when Windows asks.")
        if p.returncode != 0:
            if said:
                self.log(said)
            raise Failed(f"Couldn't {what} (code {p.returncode}).", hint or "The log below shows what it said.")
        return 0, ""

    def write(self, path, text, mode=None):
        path = Path(path)
        if self.dry_run:
            self.log(f"dry run: would write {path}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".orbit-tmp")
        tmp.write_text(text, encoding="utf-8")
        if mode is not None:
            os.chmod(tmp, mode)
        tmp.replace(path)
        self.log(f"Wrote {path}")

    def remove(self, *paths):
        for p in paths:
            p = Path(p)
            if not (p.exists() or p.is_symlink()):
                continue
            if self.dry_run:
                self.log(f"dry run: would remove {p}")
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, onerror=_force_remove)
            else:
                p.unlink()
            self.log(f"Removed {p}")

    def copy_tree(self, src, dest):
        if self.dry_run:
            self.log(f"dry run: would copy the program into {dest}")
            return
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for item in Path(src).iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        self.log(f"Copied the program into {dest}")

    def symlink(self, target, link):
        link = Path(link)
        if self.dry_run:
            self.log(f"dry run: would link {link} → {target}")
            return
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)

    def wait_http(self, url, seconds=30, insecure=False):
        """Wait until something answers at `url`. Returns True when it did."""
        self.detail(f"waiting for it to answer · {url.split('/')[2]}")
        if self.dry_run:
            self.pause(0.5)
            return True
        end = time.time() + seconds
        while time.time() < end:
            self.check()
            try:
                net.request("GET", url, timeout=1.5, insecure=insecure, raw=True)
                return True
            except net.HttpError as e:
                if e.status:  # it answered, even if with an error page
                    return True
            time.sleep(0.5)
        return False


# ---- jobs -----------------------------------------------------------------------------------
class Job:
    LOG_MAX = 3000

    def __init__(self, app_id, app_name, op, title, steps, dry_run):
        self.id = uuid.uuid4().hex[:12]
        self.app_id, self.app_name, self.op, self.title, self.dry_run = app_id, app_name, op, title, dry_run
        self.steps = [{"id": s.id, "label": s.label, "state": "pending", "detail": "", "progress": None,
                       "weight": s.weight} for s in steps]
        self.state = "running"
        self.error = self.hint = ""
        self.result = {}
        self.started, self.ended = time.time(), None
        self.cancel_requested = False
        self._log, self._log_base = [], 0
        self._cur = -1
        self._lock = threading.Lock()

    def add_log(self, text):
        with self._lock:
            for line in str(text).splitlines() or [""]:
                self._log.append(f"{time.strftime('%H:%M:%S')}  {line}")
            if len(self._log) > self.LOG_MAX:
                cut = len(self._log) - self.LOG_MAX
                del self._log[:cut]
                self._log_base += cut

    def set_step(self, **kw):
        with self._lock:
            if 0 <= self._cur < len(self.steps):
                self.steps[self._cur].update(kw)

    def begin(self, i):
        with self._lock:
            self._cur = i
            self.steps[i].update(state="active", started=time.time())

    def end_step(self, i, state):
        with self._lock:
            s = self.steps[i]
            s.update(state=state, ended=time.time())
            if state == "done":
                s["progress"] = 1.0

    def overall(self):
        total = sum(s["weight"] for s in self.steps) or 1
        got = 0.0
        for s in self.steps:
            if s["state"] in ("done", "skipped"):
                got += s["weight"]
            elif s["state"] == "active" and s.get("progress") is not None:
                got += s["weight"] * s["progress"]
        return got / total

    def snapshot(self, log_from=0):
        with self._lock:
            start = max(0, log_from - self._log_base)
            return {"id": self.id, "app": self.app_id, "appName": self.app_name, "op": self.op,
                    "title": self.title, "state": self.state, "dryRun": self.dry_run,
                    "steps": [dict(s) for s in self.steps], "current": self._cur, "progress": round(self.overall(), 4),
                    "error": self.error, "hint": self.hint, "result": self.result,
                    "started": self.started, "ended": self.ended,
                    "log": self._log[start:], "logNext": self._log_base + len(self._log)}


class Installer:
    """Starts install jobs (one at a time per app) and keeps the recent ones for the window to follow."""

    def __init__(self, host, sources, recipes, dry_run=False, spawn=None, on_change=None, pace=1.0):
        self.host, self.sources, self.recipes, self.dry_run = host, sources, recipes, dry_run
        self.pace = pace
        self.spawn = spawn or _spawn
        self.on_change = on_change
        self._jobs = {}
        self._lock = threading.Lock()

    def recipe(self, app_id):
        r = self.recipes.get(app_id)
        if not r:
            raise KeyError(app_id)
        return r

    def busy(self, app_id):
        with self._lock:
            for j in self._jobs.values():
                if j.app_id == app_id and j.state == "running":
                    return j.id
        return None

    def start(self, app_id, op, options):
        r = self.recipe(app_id)
        if self.busy(app_id):
            raise Failed(f"{r.name} is already being changed; wait for that to finish.")
        plan = r.plan(op, options)  # validates op + options, raises Failed when blocked
        job = Job(app_id, r.name, op, plan["title"], plan["steps"], self.dry_run)
        job.result = {}
        with self._lock:
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.started)[:-12]:
                if old.state != "running":
                    self._jobs.pop(old.id, None)
        sess = Session(job, self.host, self.sources, self.dry_run, spawn=self.spawn, pace=self.pace)
        sess.vars.update(plan.get("vars") or {})
        threading.Thread(target=self._work, args=(r, job, sess, plan), daemon=True,
                         name=f"orbit-install-{app_id}").start()
        return job

    def _work(self, recipe, job, sess, plan):
        job.add_log(f"{job.title} on {self.host.label}" + ("  (dry run: nothing will be changed)" if self.dry_run else ""))
        try:
            for i, step in enumerate(plan["steps"]):
                if job.cancel_requested:
                    raise Cancelled()
                job.begin(i)
                try:
                    r = step.fn(sess)
                except BaseException:
                    job.end_step(i, "failed")
                    raise
                job.end_step(i, "skipped" if r == SKIP else "done")
            job.result = plan["finish"](sess) if plan.get("finish") else {}
            job.state = "done"
            job.add_log("Finished.")
        except Cancelled:
            job.state = "cancelled"
            job.error = "Stopped. Anything already changed stays as it is; run the installer again to finish."
            job.add_log("Cancelled.")
        except Failed as e:
            job.state = "failed"
            job.error, job.hint = str(e), e.hint
            job.add_log(f"Failed: {e}")
        except Exception as e:  # a bug in a recipe: report it, keep the hub alive
            job.state = "failed"
            job.error = f"Something unexpected went wrong: {e}"
            job.add_log(traceback.format_exc())
        finally:
            sess.cleanup()
            job.ended = time.time()
            recipe.forget()
            if self.on_change:
                try:
                    self.on_change(job.app_id)
                except Exception:
                    pass

    def job(self, jid):
        with self._lock:
            return self._jobs.get(jid)

    def cancel(self, jid):
        j = self.job(jid)
        if not j:
            raise KeyError(jid)
        if j.state == "running":
            j.cancel_requested = True
        return j


# ---- helpers --------------------------------------------------------------------------------
def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def parse_sums(text):
    """`<sha256>  <name>` (or `<sha256> *<name>`) lines -> {name: sha256}."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64 and all(c in "0123456789abcdefABCDEF" for c in parts[0]):
            name = parts[1].strip().lstrip("*")
            out[name] = parts[0].lower()
            out.setdefault(name.replace("~", "."), parts[0].lower())   # GitHub stores "~" as "."
    return out


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _inside(base, name):
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
        raise ValueError(f"unsafe path in archive: {name}")
    return Path(base, *p.parts)


def safe_extract(archive, dest):
    archive = str(archive)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target = _inside(dest, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(target, mode | stat.S_IRUSR | stat.S_IWUSR)
        return
    with tarfile.open(archive, "r:*") as t:
        for m in t.getmembers():
            target = _inside(dest, m.name)
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with t.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, (m.mode & 0o755) | stat.S_IRUSR | stat.S_IWUSR)
            # links, devices and the like are skipped: release archives here don't need them


def strip_ansi(s):
    import re
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s).replace("\r", "")


def _q(s):
    if os.name == "nt":
        return f'"{s}"' if " " in s else s
    import shlex
    return shlex.quote(s)


def _force_remove(func, path, _exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _spawn(argv):
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=os.name != "nt")
        return True
    except OSError:
        return False
