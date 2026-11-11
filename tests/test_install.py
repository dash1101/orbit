"""The installer, against a stub GitHub and fake programs in a temporary home folder.

Nothing here touches the real system: downloads come from a local stub server, "installers" are
small shell scripts that write a marker file, pkexec/apt-get/systemctl/python3 are fakes, and the
other systems (Windows, macOS) are pretend hosts run in dry-run.
"""
import hashlib
import io
import json
import os
import shlex
import shutil
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orbit import net
from orbit.install.engine import Failed, Installer, Session, Sources, Step, parse_sums, safe_extract
from tests import ed25519_sign, unix_only
from orbit.install.host import Host
from orbit.install.recipes.base import Recipe, option
from orbit.install.recipes.nova import NovaRecipe
from orbit.install.recipes.starcast import StarcastRecipe, plist

CLOSED_PORT = 9  # discard: nothing listens there


# ---- a stub GitHub -------------------------------------------------------------------------
class StubGitHub:
    """Serves /repos/<owner>/<repo>/releases and the files, recording every request's headers."""

    def __init__(self):
        self.releases = {}      # repo -> list of release dicts (API shape)
        self.files = {}         # path -> bytes
        self.requests = []      # (path, headers)
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                stub.requests.append((self.path, dict(self.headers)))
                path = self.path.split("?")[0]
                for repo, rels in stub.releases.items():
                    if path == f"/repos/{repo}/releases":
                        return self._send(200, json.dumps(rels).encode(), "application/json")
                if "/releases/assets/" in path:
                    self.send_response(302)
                    self.send_header("Location", stub.base + "/files/" + path.replace("/releases/assets/", "/", 1)[len("/repos/"):])
                    self.end_headers()
                    return
                if path in stub.files:
                    return self._send(200, stub.files[path], "application/octet-stream")
                self._send(404, b'{"message":"Not Found"}', "application/json")

            def _send(self, code, data, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def publish(self, repo, tag, files, prerelease=False, published="2026-11-01T00:00:00Z", extra=None):
        assets = []
        for name, data in files.items():
            self.files[f"/files/{repo}/{tag}/{name}"] = data
            assets.append({"name": name, "size": len(data), "url": f"{self.base}/repos/{repo}/releases/assets/{tag}/{name}",
                           "browser_download_url": f"{self.base}/files/{repo}/{tag}/{name}"})
        self.releases.setdefault(repo, []).append(dict({"tag_name": tag, "draft": False, "prerelease": prerelease,
                                                        "published_at": published, "html_url": "", "assets": assets},
                                                       **(extra or {})))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def sums(files):
    return "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items()).encode()


def targz(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, (data, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def script(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def wait(job, timeout=20):
    end = time.time() + timeout
    while job.state == "running" and time.time() < end:
        time.sleep(0.05)
    return job.snapshot()


class Base(unittest.TestCase):
    def setUp(self):
        self.gh = StubGitHub()
        self.tmp = Path(tempfile.mkdtemp(prefix="orbit-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.mark = self.tmp / "mark.log"
        self.sources = Sources(api=self.gh.base, allow=(self.gh.base,), token=lambda: "")

    def tearDown(self):
        self.gh.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def host(self, system="linux", which=None, **kw):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "MARK": str(self.mark),
               "DISPLAY": ":0", "LANG": "C.UTF-8"}
        return Host(system=system, arch=kw.pop("arch", "amd64"), home=self.home, env=env,
                    which=which if which is not None else {}, systemd=kw.pop("systemd", True),
                    distro=kw.pop("distro", "Testix 1"), **kw)

    def installer(self, recipe, dry_run=False, spawn=None):
        return Installer(recipe.host, self.sources, {recipe.id: recipe}, dry_run=dry_run,
                         spawn=spawn or (lambda argv: True), pace=0)


# ---- pieces --------------------------------------------------------------------------------
class TestPieces(unittest.TestCase):
    def test_parse_sums(self):
        s = parse_sums("a" * 64 + "  nova-server_0.9.13~alpha_all.deb\n" + "B" * 64 + " *x.zip\nnot a line\n")
        self.assertEqual(s["nova-server_0.9.13~alpha_all.deb"], "a" * 64)
        self.assertEqual(s["nova-server_0.9.13.alpha_all.deb"], "a" * 64)  # GitHub's spelling
        self.assertEqual(s["x.zip"], "b" * 64)

    def test_extract_refuses_escaping_paths(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d, "bad.tar.gz")
            bad.write_bytes(targz({"../evil": (b"x", 0o644)}))
            with self.assertRaises(ValueError):
                safe_extract(bad, Path(d, "out"))
            z = Path(d, "bad.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("/abs/evil", b"x")
            with self.assertRaises(ValueError):
                safe_extract(z, Path(d, "out2"))
            self.assertFalse(Path(d, "evil").exists())

    def test_extract_zip_and_tar(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d, "a.tar.gz")
            t.write_bytes(targz({"dir/run": (b"#!/bin/sh\n", 0o755)}))
            safe_extract(t, Path(d, "t"))
            self.assertTrue(os.access(Path(d, "t", "dir", "run"), os.X_OK))
            z = Path(d, "a.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("top/app.exe", b"MZ")
            safe_extract(z, Path(d, "z"))
            self.assertEqual(Path(d, "z", "top", "app.exe").read_bytes(), b"MZ")

    def test_newest_release_ignores_github_order(self):
        items = [{"tag_name": "v0.1.0-alpha.9"}, {"tag_name": "v0.1.0-alpha.10"}, {"tag_name": "v0.1.0-alpha.8"},
                 {"tag_name": "v9", "draft": True}]
        self.assertEqual(net.newest_release(items)["tag_name"], "v0.1.0-alpha.10")

    def test_every_recipe_belongs_to_an_app(self):
        from orbit.apps import BUILTIN
        from orbit.install.recipes import ALL
        # (Orbit's own recipe is how Orbit updates itself; Orbit isn't an app in its own side bar)
        self.assertTrue({r.id for r in ALL if not getattr(r, "self_app", False)} <= {a.id for a in BUILTIN})

    def test_host_places(self):
        w = Host(system="windows", home="/h", env={"LOCALAPPDATA": "C:/L", "APPDATA": "C:/R"}, which={})
        self.assertEqual(w.local_app_data, Path("C:/L"))
        self.assertIn("Start Menu", str(w.start_menu))
        m = Host(system="darwin", home="/Users/x", env={}, which={})
        self.assertEqual(m.local_app_data, Path("/Users/x/Library/Application Support"))
        self.assertEqual(Host(system="linux", arch="arm64", home="/h", env={}, which={}, distro="Pi OS").label,
                         "Pi OS · ARM 64-bit")


class TestSources(Base):
    def test_token_goes_to_the_api_only(self):
        self.gh.publish("o/app", "v1", {"f.bin": b"data"})
        src = Sources(api=self.gh.base, allow=(self.gh.base,), token=lambda: "SECRET")
        rel = src.latest("o/app")
        with src.open_asset(rel["assets"]["f.bin"], "o/app") as r:
            self.assertEqual(r.read(), b"data")
        auth = {p.split("?")[0]: h.get("Authorization") for p, h in self.gh.requests}
        self.assertEqual(auth["/repos/o/app/releases"], "Bearer SECRET")
        self.assertEqual(auth["/repos/o/app/releases/assets/v1/f.bin"], "Bearer SECRET")
        self.assertIsNone(auth["/files/o/app/v1/f.bin"])  # the redirected file host never sees it

    def test_github_only_allows_the_apps_own_release_files(self):
        s = Sources()
        self.assertTrue(s.allowed("https://github.com/o/app/releases/download/v1/x.zip", "o/app"))
        self.assertFalse(s.allowed("https://github.com/evil/app/releases/download/v1/x.zip", "o/app"))
        self.assertFalse(s.allowed("https://example.com/x.zip", "o/app"))

    def test_missing_repo_explains_private_repos(self):
        with self.assertRaises(Failed) as c:
            self.sources.latest("o/nope")
        self.assertIn("gh auth login", c.exception.hint)


# ---- the engine, through a small stand-in app --------------------------------------------------
# A recipe of its own, so these tests don't depend on what any real app needs from the computer.
TOY_ARCHIVE = "toy_linux_amd64.tar.gz"
FAKE_TOY = 'echo "toy $*" >> "$MARK"\nexit 0\n'


class ToyRecipe(Recipe):
    id, name, repo = "toy", "Toy", "o/toy"
    PLATFORMS = {"linux": ("full", ""), "darwin": ("full", ""), "windows": ("full", "")}

    def exe(self):
        return self.host.bin_dir / "toy"

    def detect(self):
        if not self.exe().exists():
            return {"installed": False, "version": "", "where": "", "how": ""}
        return {"installed": True, "version": "1.0", "where": str(self.exe()), "how": "its own installer"}

    def options(self, op):
        if op == "uninstall":
            return [option("purge", "Also delete its settings", "bool", False)]
        return [option("setup", "Open the setup page when done", "bool", not self.installed()["installed"])]

    def assets(self, rel):
        return [TOY_ARCHIVE]

    def steps(self, op, opts):
        if op == "uninstall":
            return [Step("remove", "Remove it", lambda s: s.remove(self.exe()), 1)]
        return [Step("find", "Find the latest release", self._find, 0.5),
                Step("download", "Download", lambda s: s.vars.update(file=s.download(s.vars["rel"], TOY_ARCHIVE, self.repo)), 4),
                Step("verify", "Check the download", self._verify, 0.5),
                Step("unpack", "Unpack", lambda s: s.vars.update(exe=s.find(s.extract(s.vars["file"]), "toy")), 0.5),
                Step("install", "Install it", lambda s: s.run([s.vars["exe"], "install"]), 1)]

    def _find(self, s):
        s.vars["rel"] = rel = s.release(self.repo)
        s.asset(rel, TOY_ARCHIVE, self.repo)
        s.asset(rel, "checksums.txt", self.repo)

    def _verify(self, s):
        s.verify(s.vars["file"], s.fetch_text(s.vars["rel"], "checksums.txt", self.repo), TOY_ARCHIVE)

    def finish(self, sess, op, opts):
        return {"headline": "Toy is installed.", "body": "",
                "next": [{"id": "app", "label": "open in orbit", "kind": "app", "app": "toy", "primary": True},
                         {"id": "dash", "label": "open its page", "kind": "url", "url": f"http://127.0.0.1:{CLOSED_PORT}/"}]}


def publish_toy(gh, tamper=False):
    arc = targz({"toy": (("#!/bin/sh\n" + FAKE_TOY).encode(), 0o755)})
    listed = sums({TOY_ARCHIVE: arc if not tamper else b"something else"})
    gh.publish("o/toy", "v1.0", {TOY_ARCHIVE: arc, "checksums.txt": listed})


@unix_only
class TestEngine(Base):
    def test_installs_for_real_into_a_temp_home(self):
        publish_toy(self.gh)
        r = ToyRecipe(self.host(), self.sources)
        info = r.info("install")
        self.assertEqual(info["title"], "Install Toy")
        self.assertEqual(info["release"]["version"], "1.0")
        self.assertTrue(info["options"][0]["value"])  # first install: open the setup page
        snap = wait(self.installer(r).start("toy", "install", {"setup": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertEqual([s["state"] for s in snap["steps"]], ["done"] * 5)
        self.assertIn("toy install", self.mark.read_text())
        self.assertIn("Checksum OK", "\n".join(snap["log"]))
        self.assertTrue(snap["result"]["next"])

    def test_a_tampered_download_is_never_run(self):
        publish_toy(self.gh, tamper=True)
        snap = wait(self.installer(ToyRecipe(self.host(), self.sources)).start("toy", "install", {}))
        self.assertEqual(snap["state"], "failed")
        self.assertIn("checksum", snap["error"])
        self.assertEqual([s["state"] for s in snap["steps"]][:3], ["done", "done", "failed"])
        self.assertFalse(self.mark.exists())

    def test_cancel_stops_the_job(self):
        publish_toy(self.gh)
        r = ToyRecipe(self.host(), self.sources)
        slow = [Step("a", "slow", lambda s: s.sleep(5)), Step("b", "never", lambda s: None)]
        r.steps = lambda op, opts: slow
        inst = self.installer(r)
        job = inst.start("toy", "install", {})
        time.sleep(0.2)
        inst.cancel(job.id)
        snap = wait(job)
        self.assertEqual(snap["state"], "cancelled")
        self.assertEqual([s["state"] for s in snap["steps"]], ["failed", "pending"])

    def test_other_processors_are_not_offered(self):
        self.assertEqual(ToyRecipe(self.host("linux", arch="riscv64"), self.sources).support()["level"], "none")


# ---- Nova ----------------------------------------------------------------------------------
class SignedNova(Base):
    """A stand-in for Nova's release key, and releases signed with it (signed in Python, so any system can run these)."""

    def setUp(self):
        super().setUp()
        self.key = os.urandom(32)
        self.pub = ed25519_sign.public_pem(self.key)

    def signed(self, files, listed=None, sign_with=None):
        import base64
        listed = listed or sums(files)
        sig = ed25519_sign.sign(sign_with or self.key, listed)
        self.gh.publish("dash1101/Nova", "v0.9.20-alpha",
                        dict(files, SHA256SUMS=listed, **{"SHA256SUMS.sig": base64.b64encode(sig) + b"\n"}))


@unix_only
class TestNova(SignedNova):
    def setUp(self):
        super().setUp()
        self.vfile = self.tmp / "VERSION"
        script(self.bin / "pkexec", 'echo "pkexec $*" >> "$MARK"\nexec "$@"\n')
        script(self.bin / "apt-get", f'echo "apt-get $*" >> "$MARK"\necho 0.9.20~alpha > "{self.vfile}"\n')
        script(self.bin / "dpkg-query", "exit 1\n")

    def recipe(self, **kw):
        which = {n: str(self.bin / n) for n in ("pkexec", "apt-get", "dpkg-query")}
        which.update(dpkg="/usr/bin/dpkg", sudo="/usr/bin/sudo")
        which.update(kw.pop("which", {}))
        r = NovaRecipe(self.host("linux", which=which, **kw), self.sources)
        r.release_key, r.version_file, r.port, r.start_tries = self.pub, self.vfile, CLOSED_PORT, 1
        r.setup_gui, r.nova_gui = getattr(self, "gui", None) or (str(self.tmp / "no-setup"), str(self.tmp / "no-gui"))
        return r

    def publish(self, sign_with=None):
        deb = b"!<arch>\nfake deb"
        files = {"nova-server_0.9.20.alpha_all.deb": deb, "nova-installer.run": b"#!/bin/sh\n"}
        listed = sums({"nova-server_0.9.20~alpha_all.deb": deb, "nova-installer.run": files["nova-installer.run"]})
        self.signed(files, listed, sign_with)

    def test_signed_release_installs_through_pkexec(self):
        self.publish()
        r = self.recipe()
        info = r.info("install")
        self.assertFalse(info["blocked"], info)
        self.assertIn("password window", info["needsAdmin"])
        snap = wait(self.installer(r).start("nova", "install", {}))
        log = "\n".join(snap["log"])
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + log)
        self.assertIn("Release signature checks out", log)
        m = self.mark.read_text()
        self.assertIn("pkexec env DEBIAN_FRONTEND=noninteractive apt-get install -y", m)
        self.assertIn("nova-server_0.9.20.alpha_all.deb", m)
        self.assertEqual(snap["result"]["next"][0]["argv"], ["sudo", "nova", "setup"])  # first install: set it up

    def test_wrong_signature_stops_before_downloading(self):
        self.publish(sign_with=os.urandom(32))     # someone else's key
        snap = wait(self.installer(self.recipe()).start("nova", "install", {}))
        self.assertEqual(snap["state"], "failed")
        self.assertIn("signature doesn't match", snap["error"])
        self.assertFalse(self.mark.exists())

    def test_closed_password_window_is_explained(self):
        script(self.bin / "pkexec", "exit 126\n")
        self.publish()
        snap = wait(self.installer(self.recipe()).start("nova", "install", {}))
        self.assertEqual(snap["state"], "failed")
        self.assertIn("password window was closed", snap["error"])

    def test_novas_own_windows_after_installing_when_it_has_them(self):
        self.publish()
        self.gui = (script(self.bin / "setup-desktop", "exit 0\n"), script(self.bin / "nova-gui", "exit 0\n"))
        snap = wait(self.installer(self.recipe()).start("nova", "install", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        nxt = {n["id"]: n for n in snap["result"]["next"]}
        self.assertEqual((nxt["setup"]["kind"], nxt["setup"]["argv"]), ("program", [self.gui[0]]))
        self.assertEqual((nxt["pair"]["kind"], nxt["pair"]["argv"]), ("program", [self.gui[1]]))

    def test_other_linux_uses_the_one_file_installer(self):
        self.publish()
        r = self.recipe(which={"apt-get": None, "dpkg": None})
        self.assertEqual(r.assets(self.sources.latest("dash1101/Nova"))[0], "nova-installer.run")

    def test_not_on_macos_or_without_systemd(self):
        r = NovaRecipe(self.host("darwin"), self.sources)
        info = r.info("install")
        self.assertEqual(info["support"]["level"], "none")
        self.assertIn("runs on Linux", info["blocked"])
        with self.assertRaises(Failed):
            r.plan("install")
        self.assertIn("systemd", NovaRecipe(self.host(systemd=False), self.sources).support()["note"])

    def test_uninstall_only_for_the_debian_package(self):
        self.vfile.write_text("0.9.13~alpha")
        r = self.recipe()
        self.assertIn("one-file installer", r.info("uninstall")["blocked"])
        script(self.bin / "dpkg-query", 'echo "install ok installed|0.9.13~alpha"\n')
        r.forget()
        self.assertFalse(r.info("uninstall")["blocked"])


# ---- Nova on Windows (a preview there): no shell scripts, so these also run on Windows itself ----
class TestNovaWindows(SignedNova):
    def win_recipe(self):
        pf, pd = self.tmp / "Program Files", self.tmp / "ProgramData"
        pf.mkdir(exist_ok=True)
        host = Host(system="windows", arch="amd64", home=self.home, env={"ProgramFiles": str(pf), "ProgramData": str(pd)},
                    which={"powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"})
        r = NovaRecipe(host, self.sources)
        r.release_key, r.port, r.plain_port, r.start_tries = self.pub, CLOSED_PORT, CLOSED_PORT, 1
        return r

    def publish_windows(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Nova/windows/install.ps1", "# Nova's installer\r\n")
            z.writestr("Nova/windows/uninstall.ps1", "# Nova's uninstaller\r\n")
            z.writestr("Nova/api/server.py", "API_VERSION = \"0.9.20-alpha\"\n")
        self.signed({"nova-windows.zip": buf.getvalue()})

    def test_windows_preview_installs_with_one_permission_prompt(self):
        self.publish_windows()
        r = self.win_recipe()
        info = r.info("install")
        self.assertEqual(info["support"]["level"], "limited")
        self.assertFalse(info["blocked"], info)
        self.assertEqual(info["needsAdmin"], "Windows asks once")
        self.assertEqual(r.assets(self.sources.latest("dash1101/Nova")), ["nova-windows.zip", "SHA256SUMS", "SHA256SUMS.sig"])
        self.assertEqual([o["key"] for o in info["options"]], ["firewall"])
        self.assertIn("unpack", [s["id"] for s in info["steps"]])
        ran = []

        def admin(sess, script, args=(), what="", hint=""):
            ran.append((Path(script), list(args)))
            (r.win_dir / "lib").mkdir(parents=True, exist_ok=True)
            (r.win_dir / "lib" / "VERSION").write_text("0.9.20-alpha\r\n")
            return 0, ""

        with mock.patch.object(Session, "admin_windows", admin):
            snap = wait(self.installer(r).start("nova", "install", {"firewall": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertIn("Release signature checks out", "\n".join(snap["log"]))
        script_path, args = ran[0]
        self.assertEqual(script_path.parts[-3:], ("Nova", "windows", "install.ps1"))
        self.assertTrue(str(script_path).startswith(tempfile.gettempdir()) or "orbit-install-" in str(script_path))
        self.assertEqual(args, ["-NoFirewall"])
        self.assertEqual(r.detect(), {"installed": True, "version": "0.9.20-alpha", "where": str(r.win_dir),
                                      "how": "Nova's Windows installer"})
        nxt = {n["id"]: n for n in snap["result"]["next"]}
        self.assertEqual(nxt["gui"]["kind"], "shortcut")
        self.assertTrue(nxt["gui"]["path"].endswith("Nova Server.lnk"))

    def test_windows_tampered_zip_never_reaches_the_installer(self):
        self.publish_windows()
        self.gh.files["/files/dash1101/Nova/v0.9.20-alpha/nova-windows.zip"] = b"PK not the zip that was signed"
        r = self.win_recipe()
        with mock.patch.object(Session, "admin_windows", side_effect=AssertionError("ran the installer")):
            snap = wait(self.installer(r).start("nova", "install", {}))
        self.assertEqual(snap["state"], "failed")
        self.assertIn("doesn't match its published checksum", snap["error"])

    def test_windows_remove_uses_novas_uninstaller(self):
        r = self.win_recipe()
        self.assertIn("isn't installed", r.info("uninstall")["blocked"])
        (r.win_dir / "lib").mkdir(parents=True)
        (r.win_dir / "lib" / "VERSION").write_text("0.9.20-alpha")
        r.forget()
        self.assertIn("uninstaller isn't in", r.info("uninstall")["blocked"])
        (r.win_dir / "uninstall.ps1").write_text("# uninstaller")
        r.forget()
        self.assertFalse(r.info("uninstall")["blocked"])
        ran = []

        def admin(sess, script, args=(), what="", hint=""):
            ran.append((Path(script).name, list(args)))
            shutil.rmtree(r.win_dir)
            return 0, ""

        with mock.patch.object(Session, "admin_windows", admin):
            snap = wait(self.installer(r).start("nova", "uninstall", {"purge": True}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertEqual(ran, [("uninstall.ps1", ["-Purge"])])
        self.assertFalse(r.detect()["installed"])

    def test_windows_dry_run_changes_nothing(self):
        self.publish_windows()
        r = self.win_recipe()
        snap = wait(self.installer(r, dry_run=True).start("nova", "install", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertIn("would run, as the administrator", "\n".join(snap["log"]))
        self.assertFalse(r.win_dir.exists() and any(r.win_dir.iterdir()))


# ---- YTM Starcast --------------------------------------------------------------------------
FAKE_PYTHON = r'''echo "python $*" >> "$MARK"
case "$1" in
  -c) echo 3.12; exit 0 ;;
  -m) case "$2" in
        venv) d="$4"; mkdir -p "$d/bin" "$d/lib/python3.12/site-packages"; cp "$0" "$d/bin/python"; exit 0 ;;
        pip) b=$(dirname "$0"); v=$(dirname "$b"); sp="$v/lib/python3.12/site-packages"
             mkdir -p "$sp/ytm_starcast-0.2.0a1.dist-info" "$sp/starcast/static"
             echo '<svg/>' > "$sp/starcast/static/icon.svg"
             printf '#!/bin/sh\n' > "$b/ytm-starcast"; printf '#!/bin/sh\n' > "$b/ytm-starcast-gui"
             chmod +x "$b/ytm-starcast" "$b/ytm-starcast-gui"; exit 0 ;;
      esac ;;
esac
exit 3
'''
# A stand-in for the old YTM Beacon program: notes how it was called; "uninstall" removes it, as Beacon's does.
FAKE_BEACON = ('echo "beacon $*" >> "$MARK"\n[ "$1" = version ] && echo "ytm-beacon v0.1.4 (abc) linux/amd64"\n'
               '[ "$1" = uninstall ] && rm -f "$0"\nexit 0\n')
WHEEL = "ytm_starcast-0.2.0a1-py3-none-any.whl"
WINZIP = "ytm-starcast-v0.2.0-alpha.1-windows-x64.zip"
# The task name has a space, so PowerShell gets it quoted ('YTM Starcast'); the dry-run log then shows the whole
# PowerShell command quoted once more, for a shell.
PS_TASK = shlex.quote("'YTM Starcast'")[1:-1]


def ps_shown(command):
    """A PowerShell -Command as the dry-run log shows it (quoted once more, for a shell)."""
    return shlex.quote(command)[1:-1]


class StarcastBase(Base):
    def setUp(self):
        super().setUp()
        self.wheel = b"PK fake wheel"
        self.gh.publish("dash1101/ytm-starcast", "v0.2.0-alpha.1", {
            WHEEL: self.wheel, WINZIP: b"PK zip",
            "SHA256SUMS.txt": sums({WHEEL: self.wheel, WINZIP: b"PK zip"})}, prerelease=True)
        self.gh.publish("dash1101/ytm-starcast", "v0.1.0-alpha.15", {}, prerelease=True)  # listed after, but older
        self.py = script(self.bin / "python3", FAKE_PYTHON)
        self.sc = script(self.bin / "systemctl", 'echo "systemctl $*" >> "$MARK"\n')

    def recipe(self, system="linux", **kw):
        if system == "linux" and "which" not in kw:
            kw["which"] = {"python3": self.py, "systemctl": self.sc, "pactl": "/usr/bin/pactl", "busctl": "/usr/bin/busctl"}
        r = StarcastRecipe(self.host(system, **kw), self.sources)
        r.port, r.wait_seconds, r.beacon_wait = CLOSED_PORT, 1, 1
        return r

    def answering(self, r):
        """Something answers where YTM Starcast would (so the install's last check passes)."""
        from tests.test_orbit import Stub
        stub = Stub({("GET", "/api/info"): lambda b: (200, {"version": "0.2.0a1"})})
        self.addCleanup(stub.close)
        r.port = stub.port
        return stub

    def file(self, rel, text="", mode=None):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if mode:
            p.chmod(mode)
        return p

    def link(self, rel, target):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(target)
        return p

    def pocketfader(self, lan=True, version="0.1.0a15"):
        """Pocketfader as its install.sh (or Orbit 0.3) left it on Linux."""
        venv = self.home / ".local/share/pocketfader/venv"
        self.file(".local/share/pocketfader/venv/bin/pocketfader", "#!/bin/sh\n", 0o755)
        self.file(".local/share/pocketfader/venv/bin/pocketfader-gui", "#!/bin/sh\n", 0o755)
        (venv / f"lib/python3.12/site-packages/pocketfader-{version}.dist-info").mkdir(parents=True)
        self.file(".config/systemd/user/pocketfader.service",
                  f"[Service]\nExecStart={venv}/bin/pocketfader" + (" --lan" if lan else "") + "\n")
        self.link(".local/bin/pocketfader", venv / "bin/pocketfader")
        self.file(".local/share/applications/pocketfader.desktop", "[Desktop Entry]\n")
        self.file(".local/share/icons/hicolor/scalable/apps/pocketfader.svg", "<svg/>")
        self.file(".config/pocketfader/config.json", '{"token": "paired phones use this"}')
        return venv

    def beacon(self):
        """YTM Beacon as its own installer left it on Linux."""
        self.file(".config/ytm-beacon/config.json", '{"enabled": true}')
        self.file(".local/share/ytm-beacon/ytm-beacon.log", "")
        return str(self.file(".local/bin/ytm-beacon", "#!/bin/sh\n" + FAKE_BEACON, 0o755))


@unix_only
class TestStarcast(StarcastBase):
    def test_linux_install_for_real_with_fake_tools(self):
        r = self.recipe()
        info = r.info("install", {"lan": True, "ytdlp": False})
        self.assertFalse(info["blocked"], info)
        self.assertEqual(info["title"], "Install YTM Starcast")
        self.assertEqual(info["release"]["tag"], "v0.2.0-alpha.1")
        self.assertIn("home Wi-Fi", " ".join(info["summary"]))
        self.assertEqual([s["id"] for s in info["steps"]],
                         ["find", "download", "verify", "venv", "pip", "menu", "service", "check"])   # nothing to move
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"lan": True, "ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        unit = (self.home / ".config/systemd/user/ytm-starcast.service").read_text()
        self.assertIn("ytm-starcast/venv/bin/ytm-starcast --lan", unit)
        self.assertIn("Description=YTM Starcast", unit)
        desktop = (self.home / ".local/share/applications/ytm-starcast.desktop").read_text()
        for want in ("Name=YTM Starcast", "Icon=ytm-starcast", "/ytm-starcast-gui",
                     "StartupWMClass=app.novalabs.starcast\n"):    # the window's id, as install.sh writes it
            self.assertIn(want, desktop)
        self.assertEqual((self.home / ".local/share/icons/hicolor/scalable/apps/ytm-starcast.svg").read_text(),
                         "<svg/>\n")
        self.assertTrue((self.home / ".local/bin/ytm-starcast").is_symlink())
        self.assertTrue((self.home / ".local/bin/ytm-starcast-gui").is_symlink())
        m = self.mark.read_text()
        self.assertIn("systemctl --user enable ytm-starcast.service", m)
        self.assertIn("systemctl --user restart ytm-starcast.service", m)
        self.assertIn("--no-deps --force-reinstall", m)
        self.assertNotIn("pocketfader", m)
        r.forget()
        self.assertEqual(r.installed()["version"], "0.2.0a1")
        self.assertFalse(r.installed()["migrate"])
        self.assertTrue(r.options("install")[1]["value"])  # remembers --lan for the next reinstall

    def test_linux_blocks_without_pactl(self):
        info = self.recipe(which={"python3": self.py}).info("install")
        self.assertIn("needs a few things", info["blocked"])
        self.assertTrue(any("pulseaudio-utils" in r["hint"] for r in info["requirements"] if not r["ok"]))
        self.assertTrue(any("Discord" in r["label"] and r["ok"] for r in info["requirements"]))

    def test_windows_plan_dry_run(self):
        r = self.recipe("windows", which={"powershell": "powershell", "schtasks": "schtasks", "taskkill": "taskkill",
                                          "reg": "reg"}, probe=lambda argv: (1, ""))
        info = r.info("install")
        self.assertEqual(info["support"]["level"], "full")
        self.assertIn("Start menu", " ".join(info["summary"]))
        self.assertEqual(info["release"]["sizeText"], "6 B")
        self.assertNotIn("retire", [s["id"] for s in info["steps"]])
        snap = wait(self.installer(r, dry_run=True).start("ytm-starcast", "install", {"autostart": True}))
        self.assertEqual(snap["state"], "done", snap["error"])
        log = "\n".join(snap["log"])
        self.assertIn("YTM Starcast/install-exe.ps1", log)
        # home Wi-Fi off is said explicitly: without -Lan the script would keep a --lan its task had before
        script = self.home / "AppData/Local/Programs/YTM Starcast/install-exe.ps1"
        self.assertIn(ps_shown(f"& '{script}' -Lan:$false; exit 0"), log)
        self.assertIn("Start-ScheduledTask -TaskName " + PS_TASK, log)
        self.assertIn("WScript.Shell", log)
        self.assertIn("schtasks /Delete /TN Pocketfader /F", log)       # a Pocketfader installed by hand stops too
        self.assertFalse(any(self.home.rglob("*")))  # dry run wrote nothing
        self.assertEqual(r.info("install", {"autostart": False})["options"][1]["value"], False)
        # without start at logon only the task goes: install-exe.ps1 -Uninstall would remove the program just installed
        snap = wait(self.installer(r, dry_run=True).start("ytm-starcast", "install", {"autostart": False}))
        self.assertEqual(snap["state"], "done", snap["error"])
        log = "\n".join(snap["log"])
        self.assertIn("schtasks /Delete /TN 'YTM Starcast' /F", log)
        self.assertNotIn("YTM Starcast/install-exe.ps1' -Uninstall", log)
        self.assertIn("Start-Process", log)
        self.assertIn("Windows on ARM", self.recipe("windows", arch="arm64", probe=lambda argv: (1, ""))
                      .info("install")["requirements"][0]["hint"])

    def test_macos_is_limited_and_says_why(self):
        r = self.recipe("darwin", which={"python3": self.py, "launchctl": "/bin/launchctl"})
        info = r.info("install")
        self.assertEqual(info["support"]["level"], "limited")
        self.assertIn("YouTube Music remote", info["support"]["note"])
        snap = wait(self.installer(r, dry_run=True).start("ytm-starcast", "install", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        log = "\n".join(snap["log"])
        self.assertIn("launchctl bootstrap gui/", log)
        self.assertIn("app.novalabs.starcast.server.plist", log)
        self.assertIn("Library/Application Support/YTM Starcast/venv", log)
        p = plist("app.novalabs.starcast.server", ["/x/ytm-starcast", "--audio", "none", "--lan"], True, "/l.log")
        self.assertIn("<string>--audio</string>", p)
        self.assertIn("<true/>", p)

    def test_uninstall_keeps_settings_unless_asked(self):
        self.file(".local/share/ytm-starcast/venv/bin/ytm-starcast")
        self.file(".config/ytm-starcast/config.json", "{}")
        r = self.recipe(which={"systemctl": self.sc})
        snap = wait(self.installer(r).start("ytm-starcast", "uninstall", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertFalse((self.home / ".local/share/ytm-starcast").exists())
        self.assertTrue((self.home / ".config/ytm-starcast").exists())
        self.assertIn("disable --now ytm-starcast.service", self.mark.read_text())
        self.assertIn(str(self.home / ".config/ytm-starcast"), snap["result"]["body"])

    def test_installed_another_way_is_left_alone(self):
        for name in ("ytm-starcast", "pocketfader"):
            r = self.recipe(which={name: f"/usr/local/bin/{name}"})
            self.assertEqual(r.installed()["how"], "installed another way")
            self.assertIn("installed another way", r.info("uninstall")["blocked"])
        self.beacon()
        r = self.recipe(which={"ytm-starcast": "/usr/local/bin/ytm-starcast"})
        self.assertTrue(r.installed()["legacy"]["beacon"])
        self.assertFalse(r.installed()["migrate"])          # Orbit doesn't put a second copy next to it

    def test_either_spelling_of_the_python_package(self):
        """pip writes the distribution's hyphen as an underscore; the hyphenated spelling is read as well."""
        venv = self.home / ".local/share/ytm-starcast/venv"
        self.file(".local/share/ytm-starcast/venv/bin/ytm-starcast", "", 0o755)
        (venv / "lib/python3.12/site-packages/ytm-starcast-0.2.0a2.dist-info").mkdir(parents=True)
        r = self.recipe()
        self.assertEqual((r.installed()["how"], r.installed()["version"]), ("its own Python environment", "0.2.0a2"))
        for wheel in ("ytm_starcast-0.2.0a2-py3-none-any.whl", "ytm-starcast-0.2.0a2-py3-none-any.whl"):
            rel = {"tag": "v0.2.0-alpha.2", "assets": ["SHA256SUMS.txt", wheel, "ytm-starcast-android.apk"]}
            self.assertEqual(r.wheel_name(rel), wheel)

    def test_bad_options_are_refused(self):
        from orbit.apps.base import BadRequest
        r = self.recipe()
        with self.assertRaises(BadRequest):
            r.info("install", {"nope": True})
        with self.assertRaises(BadRequest):
            r.info("install", {"lan": "yes"})
        with self.assertRaises(BadRequest):
            r.info("upgrade")


@unix_only
class TestMove(StarcastBase):
    """From Pocketfader and YTM Beacon to YTM Starcast. Settings are never copied or deleted: YTM Starcast takes
    them over."""

    def test_linux_moves_pocketfader_and_removes_beacon(self):
        old = self.pocketfader(lan=True)
        self.file(".local/bin/pocketfader-gui", "#!/bin/sh\n# not Pocketfader's: someone else's file\n", 0o755)
        exe = self.beacon()
        r = self.recipe()
        inst = r.installed()
        self.assertEqual((inst["installed"], inst["how"], inst["version"]), (True, "Pocketfader (its old name)", "0.1.0a15"))
        self.assertTrue(inst["migrate"])
        self.assertEqual(inst["legacy"], {"pocketfader": str(old.parent), "beacon": exe})
        info = r.info("install", {"ytdlp": False})
        self.assertEqual(info["title"], "Move Pocketfader to YTM Starcast 0.2.0-alpha.1")
        self.assertTrue(next(o for o in info["options"] if o["key"] == "lan")["value"])      # from the old service
        summary = " ".join(info["summary"])
        self.assertIn("Replace Pocketfader 0.1.0a15", summary)
        self.assertIn("Remove YTM Beacon 0.1.4", summary)
        self.assertEqual([s["id"] for s in info["steps"]],
                         ["find", "download", "verify", "venv", "pip", "menu", "retire", "beacon", "service", "check",
                          "cleanup"])
        self.answering(r)
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertEqual({s["id"]: s["state"] for s in snap["steps"]}["cleanup"], "done")
        m = self.mark.read_text()
        stop_old, restart_new = m.index("disable --now pocketfader.service"), m.index("restart ytm-starcast.service")
        self.assertLess(m.index("venv --system-site-packages"), stop_old)        # the new one is in place first
        self.assertLess(stop_old, m.index("beacon uninstall"))
        self.assertLess(m.index("beacon uninstall"), restart_new)
        self.assertNotIn("--purge", m)                                           # Beacon keeps its settings
        for gone in (old.parent, self.home / ".local/bin/pocketfader", self.home / ".config/systemd/user/pocketfader.service",
                     self.home / ".local/share/applications/pocketfader.desktop",
                     self.home / ".local/share/icons/hicolor/scalable/apps/pocketfader.svg", Path(exe)):
            self.assertFalse(gone.exists() or gone.is_symlink(), gone)
        for kept in (".config/pocketfader/config.json", ".config/ytm-beacon/config.json", ".local/bin/pocketfader-gui"):
            self.assertTrue((self.home / kept).exists(), kept)
        self.assertIn("--lan", (self.home / ".config/systemd/user/ytm-starcast.service").read_text())
        self.assertEqual(snap["result"]["headline"], "Pocketfader is now YTM Starcast.")
        self.assertIn("YTM Beacon is removed", snap["result"]["body"])
        r.forget()
        inst = r.installed()
        self.assertEqual((inst["how"], inst["version"], inst["migrate"]), ("its own Python environment", "0.2.0a1", False))
        self.assertEqual(inst["legacy"], {"pocketfader": "", "beacon": ""})

    def test_beacon_only_machine(self):
        from orbit.config import Config
        from orbit.runner import Runner
        from orbit.server import Hub
        exe = self.beacon()
        r = self.recipe()
        self.assertEqual(r.info("install")["title"], "Install YTM Starcast (replaces YTM Beacon)")
        hub = Hub(config=Config(self.tmp / "cfg.json"), runner=Runner(dry_run=True), app_classes=[], host=r.host,
                  sources=self.sources, recipes={"ytm-starcast": r})
        self.addCleanup(lambda: net.set_login_provider(None))
        brief = hub.install_brief("ytm-starcast")
        self.assertEqual((brief["installed"], brief["migrate"], brief["latest"]), (False, True, "0.2.0-alpha.1"))
        info = r.info("install", {"ytdlp": False})
        self.assertNotIn("retire", [s["id"] for s in info["steps"]])
        self.assertNotIn("cleanup", [s["id"] for s in info["steps"]])
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertFalse(Path(exe).exists())
        self.assertIn("beacon uninstall\n", self.mark.read_text())
        self.assertTrue((self.home / ".config/ytm-beacon/config.json").exists())
        self.assertIn("ytm-starcast-extension.zip", snap["result"]["body"])        # its extension replaces Beacon's

    def test_beacon_that_wont_go_doesnt_fail_the_move(self):
        exe = self.file(".local/bin/ytm-beacon", '#!/bin/sh\necho "beacon $*" >> "$MARK"\nexit 1\n', 0o755)
        r = self.recipe()
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertTrue(Path(exe).exists())
        self.assertIn("still at", snap["result"]["body"])
        self.assertIn("ytm-beacon uninstall", snap["result"]["body"])
        # the Beacon left behind is no update: the card doesn't offer "update to" the version already installed
        r.forget()
        self.assertEqual((r.installed()["version"], r.installed()["migrate"]), ("0.2.0a1", False))
        self.assertFalse(self.hub(r).install_brief("ytm-starcast")["update"])

    def hub(self, r):
        from orbit.config import Config
        from orbit.runner import Runner
        from orbit.server import Hub
        r.info("install")                                   # learns the latest release
        self.addCleanup(lambda: net.set_login_provider(None))
        return Hub(config=Config(self.tmp / "cfg.json"), runner=Runner(dry_run=True), app_classes=[], host=r.host,
                   sources=self.sources, recipes={"ytm-starcast": r})

    def starcast(self, version="0.2.0a1"):
        """YTM Starcast as Orbit (or its install.sh) left it on Linux."""
        venv = self.home / ".local/share/ytm-starcast/venv"
        self.file(".local/share/ytm-starcast/venv/bin/ytm-starcast", "#!/bin/sh\n", 0o755)
        (venv / f"lib/python3.12/site-packages/ytm_starcast-{version}.dist-info").mkdir(parents=True)
        return venv

    def test_a_kept_beacon_is_left_alone(self):
        """Installed with --keep-ytm-beacon: no "update" for the version already here, and installing again only
        removes YTM Beacon when asked."""
        self.starcast()
        exe = self.beacon()
        r = self.recipe()
        inst = r.installed()
        self.assertEqual((inst["how"], inst["migrate"], inst["legacy"]["beacon"]),
                         ("its own Python environment", False, exe))
        brief = self.hub(r).install_brief("ytm-starcast")
        self.assertEqual((brief["latest"], brief["migrate"], brief["update"]), ("0.2.0-alpha.1", False, False))
        info = r.info("install", {"ytdlp": False})
        self.assertEqual(info["title"], "Reinstall YTM Starcast")
        self.assertFalse(next(o for o in info["options"] if o["key"] == "beacon")["value"])
        self.assertNotIn("beacon", [s["id"] for s in info["steps"]])
        self.assertIn("Keep YTM Beacon", " ".join(info["summary"]))
        self.answering(r)
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertTrue(Path(exe).exists())
        self.assertNotIn("beacon uninstall", self.mark.read_text())
        self.assertIn("YTM Beacon is kept", snap["result"]["body"])
        # asked for: removed
        info = r.info("install", {"ytdlp": False, "beacon": True})
        self.assertIn("beacon", [s["id"] for s in info["steps"]])
        self.assertIn("Remove YTM Beacon 0.1.4", " ".join(info["summary"]))
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False, "beacon": True}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertFalse(Path(exe).exists())
        # an older YTM Starcast next to a kept Beacon still gets its update
        shutil.rmtree(self.home / ".local/share/ytm-starcast")
        self.starcast("0.1.9")
        self.beacon()
        r.forget()
        self.assertTrue(self.hub(r).install_brief("ytm-starcast")["update"])

    def test_beacon_only_machine_can_keep_it(self):
        exe = self.beacon()
        r = self.recipe()
        info = r.info("install", {"ytdlp": False, "beacon": False})
        self.assertEqual(info["title"], "Install YTM Starcast")
        self.assertNotIn("beacon", [s["id"] for s in info["steps"]])
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False, "beacon": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertTrue(Path(exe).exists())
        self.assertIn("YTM Beacon is kept", snap["result"]["body"])

    def test_a_mac_keeps_beacon_unless_asked(self):
        self.beacon()
        r = self.recipe("darwin", which={"python3": self.py, "launchctl": "/bin/launchctl"})
        info = r.info("install")
        self.assertFalse(next(o for o in info["options"] if o["key"] == "beacon")["value"])
        self.assertNotIn("beacon", [s["id"] for s in info["steps"]])
        self.assertIn("isn't tested", info["support"]["note"])
        self.assertIn("beacon", [s["id"] for s in r.info("install", {"beacon": True})["steps"]])

    def sleeper(self, *argv, pidfile=None):
        """A real process with this command line (python running a script that waits), for stopping."""
        import subprocess
        import sys
        p = subprocess.Popen([sys.executable, *map(str, argv)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait()))
        if pidfile:
            self.file(pidfile, f"{p.pid}\n")
        return p

    def ended(self, p):
        try:
            p.wait(5)
        except Exception:
            return False
        return True

    @unittest.skipUnless(os.path.isdir("/proc/self"), "reads command lines from /proc")
    def test_linux_without_systemd_stops_a_running_pocketfader(self):
        """No service manager: Pocketfader ran from its login entry or its desktop window, which keep its port
        until they are stopped."""
        old = self.pocketfader(lan=False)
        (self.home / ".config/systemd/user/pocketfader.service").unlink()
        self.file(".config/autostart/pocketfader.desktop", f"[Desktop Entry]\nExec={old}/bin/pocketfader\n")
        wait_forever = "import time\ntime.sleep(120)\n"
        (old / "bin/pocketfader").write_text(wait_forever)
        from_login = self.sleeper(old / "bin/pocketfader", "--port", "8777")
        other = self.tmp / "elsewhere/bin/pocketfader"         # one installed another way, started by its window
        other.parent.mkdir(parents=True)
        other.write_text(wait_forever)
        from_window = self.sleeper(other, pidfile=".config/pocketfader/server.pid")
        # a pid file left from before a restart, now some other program's number: never stopped
        unrelated = self.tmp / "unrelated.py"
        unrelated.write_text(wait_forever)
        bystander = self.sleeper(unrelated, pidfile=".config/ytm-starcast/server.pid")
        r = self.recipe(systemd=False)
        info = r.info("install", {"ytdlp": False})
        self.assertEqual([s["id"] for s in info["steps"]],
                         ["find", "download", "verify", "venv", "pip", "menu", "retire", "login", "cleanup"])
        # a dry run only says so
        snap = wait(self.installer(r, dry_run=True).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"])
        log = "\n".join(snap["log"])
        import re
        self.assertRegex(log, rf"would stop process {from_login.pid}\b")
        self.assertRegex(log, rf"would stop process {from_window.pid} \(started by its desktop window")
        self.assertIsNone(re.search(rf"process {bystander.pid}\b", log))
        self.assertIsNone(from_login.poll())
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertTrue(self.ended(from_login))
        self.assertTrue(self.ended(from_window))
        self.assertIsNone(bystander.poll())
        self.assertEqual({s["id"]: s["state"] for s in snap["steps"]}["cleanup"], "done")
        for gone in (old.parent, self.home / ".config/autostart/pocketfader.desktop",
                     self.home / ".config/pocketfader/server.pid"):
            self.assertFalse(gone.exists(), gone)
        for kept in (".config/pocketfader/config.json", ".config/ytm-starcast/server.pid",
                     ".config/autostart/ytm-starcast.desktop"):
            self.assertTrue((self.home / kept).exists(), kept)
        self.assertNotIn("still running", snap["result"]["body"])

    @unittest.skipUnless(os.path.isdir("/proc/self"), "reads command lines from /proc")
    def test_linux_without_systemd_uninstall_stops_a_running_starcast(self):
        venv = self.starcast()
        (venv / "bin/ytm-starcast").write_text("import time\ntime.sleep(120)\n")
        p = self.sleeper(venv / "bin/ytm-starcast", pidfile=".config/ytm-starcast/server.pid")
        r = self.recipe(systemd=False)
        snap = wait(self.installer(r).start("ytm-starcast", "uninstall", {}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertTrue(self.ended(p))
        self.assertEqual({s["id"]: s["state"] for s in snap["steps"]}["stop"], "done")
        self.assertFalse(venv.exists())
        self.assertTrue((self.home / ".config/ytm-starcast").exists())       # settings kept, only its pid file gone
        self.assertFalse((self.home / ".config/ytm-starcast/server.pid").exists())

    def test_cleanup_waits_until_the_new_one_answers(self):
        old = self.pocketfader(lan=False)
        r = self.recipe()                                   # nothing answers on its port
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertEqual({s["id"]: s["state"] for s in snap["steps"]}["cleanup"], "skipped")
        self.assertTrue(old.exists())
        self.assertFalse((self.home / ".config/systemd/user/pocketfader.service").exists())   # stopped all the same
        r.forget()
        inst = r.installed()
        self.assertEqual((inst["how"], inst["migrate"], inst["legacy"]["pocketfader"]),
                         ("its own Python environment", False, str(old.parent)))
        self.assertIn("Remove what is left of Pocketfader", " ".join(r.info("install")["summary"]))
        self.answering(r)
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertFalse(old.exists())
        self.assertTrue((self.home / ".config/pocketfader/config.json").exists())

    def test_linux_without_systemd_keeps_starting_at_login(self):
        old = self.pocketfader(lan=False)
        (self.home / ".config/systemd/user/pocketfader.service").unlink()
        self.file(".config/autostart/pocketfader.desktop", f"[Desktop Entry]\nExec={old}/bin/pocketfader --lan\n")
        exe = self.beacon()
        self.file(".config/autostart/ytm-beacon.desktop", "[Desktop Entry]\n")
        r = self.recipe(systemd=False)
        info = r.info("install", {"ytdlp": False})
        self.assertTrue(next(o for o in info["options"] if o["key"] == "lan")["value"])     # from its login entry
        self.assertIn("Start it when you log in, as Pocketfader did", info["summary"])
        self.assertEqual([s["id"] for s in info["steps"]],
                         ["find", "download", "verify", "venv", "pip", "menu", "retire", "login", "beacon", "cleanup"])
        snap = wait(self.installer(r).start("ytm-starcast", "install", {"ytdlp": False, "lan": True}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        entry = (self.home / ".config/autostart/ytm-starcast.desktop").read_text()
        self.assertIn("ytm-starcast/venv/bin/ytm-starcast --lan", entry)
        for gone in (old.parent, self.home / ".config/autostart/pocketfader.desktop", Path(exe),
                     self.home / ".config/autostart/ytm-beacon.desktop"):
            self.assertFalse(gone.exists(), gone)
        self.assertNotIn("systemctl", self.mark.read_text())
        self.assertTrue((self.home / ".config/pocketfader/config.json").exists())
        r.forget()
        wait(self.installer(r).start("ytm-starcast", "uninstall", {}))
        self.assertFalse((self.home / ".config/autostart/ytm-starcast.desktop").exists())

    def test_old_venv_self_updated_to_starcast(self):
        old = self.pocketfader()
        (old / "lib/python3.12/site-packages/ytm_starcast-0.2.0a1.dist-info").mkdir()
        r = self.recipe()
        inst = r.installed()
        self.assertEqual((inst["how"], inst["version"], inst["migrate"]), ("Pocketfader (its old name)", "0.2.0a1", True))
        self.assertEqual(r.info("install")["title"], "Move Pocketfader to YTM Starcast 0.2.0-alpha.1")

    def test_windows_move_dry_run(self):
        local = self.home / "AppData/Local"
        self.file("AppData/Local/Programs/Pocketfader/pocketfader.exe", "MZ")
        self.file("AppData/Local/Programs/Pocketfader/install-exe.ps1", "# Pocketfader's")
        self.file("AppData/Local/Programs/Pocketfader/orbit-install.json", '{"version": "0.1.0-alpha.15"}')
        self.file("AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Pocketfader.lnk", "")
        beacon = self.file("AppData/Local/ytm-beacon/ytm-beacon.exe", "MZ")
        asked = []

        def probe(argv):
            asked.append(argv)
            if argv[-1] == "version":
                return 0, "ytm-beacon v0.1.4 (abc) windows/amd64"
            if argv[:4] == ["schtasks", "/Query", "/TN", "Pocketfader"]:
                return 0, "<Exec><Command>pocketfader.exe</Command><Arguments>--audio windows --lan</Arguments></Exec>"
            return 1, "ERROR: The system cannot find the file specified."

        r = self.recipe("windows", which={"powershell": "powershell", "schtasks": "schtasks", "taskkill": "taskkill",
                                          "reg": "reg"}, probe=probe)
        inst = r.installed()
        self.assertEqual((inst["how"], inst["version"], inst["migrate"]), ("Pocketfader (its old name)", "0.1.0-alpha.15", True))
        self.assertEqual(inst["legacy"]["beacon"], str(beacon))
        info = r.info("install")
        self.assertTrue(next(o for o in info["options"] if o["key"] == "lan")["value"])     # from Pocketfader's task
        self.assertEqual([s["id"] for s in info["steps"]],
                         ["find", "download", "verify", "unpack", "stop", "copy", "shortcut", "retire", "beacon", "task",
                          "start", "cleanup"])
        before = sorted(str(p) for p in self.home.rglob("*"))
        snap = wait(self.installer(r, dry_run=True).start("ytm-starcast", "install", {"autostart": True, "lan": True}))
        self.assertEqual(snap["state"], "done", snap["error"])
        log = "\n".join(snap["log"])
        for want in ("schtasks /End /TN Pocketfader", "taskkill /IM pocketfader.exe /F", "taskkill /IM ytm-starcast.exe /F",
                     "Pocketfader/install-exe.ps1 -Uninstall", "/v Pocketfader /f", "ytm-beacon.exe uninstall",
                     ps_shown(f"& '{local / 'Programs/YTM Starcast/install-exe.ps1'}' -Lan; exit 0"),
                     "Start-ScheduledTask -TaskName " + PS_TASK,
                     "schtasks /End /TN 'YTM Starcast'",
                     f"would remove {local / 'Programs/Pocketfader'}"):
            self.assertIn(want, log)
        self.assertNotIn("ytm-beacon.exe uninstall --purge", log)
        self.assertLess(log.index("Pocketfader/install-exe.ps1 -Uninstall"), log.index("Start-ScheduledTask"))
        self.assertLess(log.index("Start-ScheduledTask"), log.index("would remove"))
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*")), before)          # dry run: nothing changed
        self.assertEqual(snap["result"]["headline"], "Pocketfader is now YTM Starcast.")

    def test_macos_move_dry_run(self):
        self.file("Library/Application Support/Pocketfader/venv/bin/pocketfader", "", 0o755)
        self.file("Library/LaunchAgents/app.pocketfader.server.plist",
                  plist("app.pocketfader.server", ["/x/pocketfader", "--audio", "none", "--media", "none", "--lan"],
                        True, "/l.log"))
        r = self.recipe("darwin", which={"python3": self.py, "launchctl": "/bin/launchctl"})
        info = r.info("install")
        self.assertEqual(info["title"], "Move Pocketfader to YTM Starcast 0.2.0-alpha.1")
        self.assertTrue(next(o for o in info["options"] if o["key"] == "lan")["value"])
        snap = wait(self.installer(r, dry_run=True).start("ytm-starcast", "install", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        log = "\n".join(snap["log"])
        out = log.index("bootout gui/")
        self.assertIn("app.pocketfader.server.plist", log[out:log.index("\n", out)])
        self.assertLess(out, log.index("bootstrap gui/"))
        self.assertIn("app.novalabs.starcast.server.plist", log[log.index("bootstrap gui/"):])
        self.assertIn("would remove " + str(self.home / "Library/Application Support/Pocketfader"), log)

    def test_uninstall_purge_removes_the_whole_family(self):
        new = self.home / ".local/share/ytm-starcast/venv"
        self.file(".local/share/ytm-starcast/venv/bin/ytm-starcast", "", 0o755)
        self.link(".local/bin/ytm-starcast", new / "bin/ytm-starcast")
        self.file(".local/share/applications/ytm-starcast.desktop", "")
        self.file(".config/systemd/user/ytm-starcast.service", "")
        self.file(".config/ytm-starcast/config.json", "{}")
        old = self.pocketfader()
        exe = self.beacon()
        r = self.recipe()
        info = r.info("uninstall", {"purge": True})
        self.assertFalse(info["blocked"], info)
        self.assertIn("Remove YTM Beacon", " ".join(info["summary"]))
        snap = wait(self.installer(r).start("ytm-starcast", "uninstall", {"purge": True}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        m = self.mark.read_text()
        for want in ("disable --now ytm-starcast.service", "disable --now pocketfader.service", "beacon uninstall --purge"):
            self.assertIn(want, m)
        for gone in (new.parent, old.parent, Path(exe), self.home / ".local/bin/ytm-starcast",
                     self.home / ".local/bin/pocketfader",
                     self.home / ".local/share/applications/ytm-starcast.desktop",
                     self.home / ".local/share/applications/pocketfader.desktop",
                     self.home / ".config/systemd/user/ytm-starcast.service",
                     self.home / ".config/systemd/user/pocketfader.service",
                     self.home / ".config/ytm-starcast", self.home / ".config/pocketfader",
                     self.home / ".config/ytm-beacon", self.home / ".local/share/ytm-beacon"):
            self.assertFalse(gone.exists() or gone.is_symlink(), gone)
        self.assertEqual(snap["result"]["headline"], "YTM Starcast is removed.")
        r.forget()
        self.assertFalse(r.installed()["installed"])


# ---- over HTTP -----------------------------------------------------------------------------
class TestInstallHTTP(Base):
    def setUp(self):
        super().setUp()
        from orbit.config import Config
        from orbit.runner import Runner
        from orbit.server import Hub, serve
        publish_toy(self.gh)
        self.spawned = []
        runner = Runner(dry_run=True)
        runner.spawn = lambda argv: self.spawned.append(argv) or True
        runner.launch = lambda argv: self.spawned.append(argv) or True
        host = self.host()
        rec = ToyRecipe(host, self.sources)
        self.hub = Hub(config=Config(self.tmp / "cfg.json"), runner=runner, app_classes=[], host=host,
                       sources=self.sources, recipes={"toy": rec})
        self.addCleanup(lambda: net.set_login_provider(None))
        self.hub.installer.pace = 0
        self.httpd, self.port = serve(self.hub, 0)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, body=None):
        return net.request("GET" if body is None else "POST", self.url + path, body=body, timeout=5)

    @unix_only      # opens the result's address the Windows way (os.startfile), not through the recorded launcher
    def test_list_plan_start_follow(self):
        lst = self.call("/api/install")
        self.assertEqual(lst["host"]["system"], "linux")
        self.assertEqual(lst["apps"][0]["id"], "toy")
        plan = self.call("/api/install/toy/plan", {"op": "install", "options": {"setup": False}})
        self.assertEqual(plan["steps"][1]["label"], "Download")
        job = self.call("/api/install/toy/start", {"op": "install", "options": {}})
        self.assertTrue(job["dryRun"])
        for _ in range(100):
            snap = self.call(f"/api/install/jobs/{job['id']}?from=0")
            if snap["state"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(snap["state"], "done")
        later = self.call(f"/api/install/jobs/{job['id']}?from={snap['logNext']}")
        self.assertEqual(later["log"], [])  # only new lines are sent
        # the "open dashboard" next step only opens local addresses, through the (dry-run) runner
        r = self.call(f"/api/install/jobs/{job['id']}/next", {"id": "dash"})
        self.assertTrue(r["ok"])
        self.assertTrue(self.spawned and "127.0.0.1" in self.spawned[-1][-1])
        with self.assertRaises(net.HttpError) as c:
            self.call(f"/api/install/jobs/{job['id']}/next", {"id": "app"})
        self.assertEqual(c.exception.status, 400)

    def test_errors(self):
        with self.assertRaises(net.HttpError) as c:
            self.call("/api/install/nope/plan", {"op": "install"})
        self.assertEqual(c.exception.status, 404)
        with self.assertRaises(net.HttpError) as c:
            self.call("/api/install/toy/start", {"op": "uninstall"})   # not installed here
        self.assertEqual(c.exception.status, 400)
        self.assertIn("isn't installed", str(c.exception))
        with self.assertRaises(net.HttpError) as c:
            net.request("POST", self.url + "/api/install/toy/start", body={"op": "install"}, timeout=5,
                        headers={"Origin": "https://evil.example"})
        self.assertEqual(c.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
