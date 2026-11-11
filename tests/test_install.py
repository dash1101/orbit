"""The installer, against a stub GitHub and fake programs in a temporary home folder.

Nothing here touches the real system: downloads come from a local stub server, "installers" are
small shell scripts that write a marker file, pkexec/apt-get/systemctl/python3 are fakes, and the
other systems (Windows, macOS) are pretend hosts run in dry-run.
"""
import hashlib
import io
import json
import os
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
from orbit.install.recipes.beacon import BeaconRecipe
from orbit.install.recipes.nova import NovaRecipe
from orbit.install.recipes.pocketfader import PocketfaderRecipe, plist

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


# ---- YTM Beacon ----------------------------------------------------------------------------
FAKE_BEACON = 'echo "beacon $*" >> "$MARK"\n[ "$1" = version ] && echo "ytm-beacon v0.1.9 (abc) linux/amd64"\nexit 0\n'


@unix_only
class TestBeacon(Base):
    def recipe(self, system="linux", **kw):
        r = BeaconRecipe(self.host(system, **kw), self.sources)
        r.ports, r.wait_tries = [CLOSED_PORT], 1
        return r

    def publish(self, tamper=False):
        arc = targz({"ytm-beacon": (("#!/bin/sh\n" + FAKE_BEACON).encode(), 0o755)})
        files = {"ytm-beacon_linux_amd64.tar.gz": arc}
        listed = sums(files) if not tamper else sums({"ytm-beacon_linux_amd64.tar.gz": b"something else"})
        self.gh.publish("dash1101/ytm-beacon", "v0.1.9", dict(files, **{"checksums.txt": listed}))

    def test_installs_for_real_into_a_temp_home(self):
        self.publish()
        r = self.recipe()
        info = r.info("install")
        self.assertEqual(info["title"], "Install YTM Beacon")
        self.assertEqual(info["release"]["version"], "0.1.9")
        self.assertTrue(info["options"][0]["value"])  # first install: open the setup page
        snap = wait(self.installer(r).start("ytm-beacon", "install", {"setup": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        self.assertEqual([s["state"] for s in snap["steps"]], ["done"] * 6)
        self.assertIn("beacon install --no-open", self.mark.read_text())
        self.assertIn("Checksum OK", "\n".join(snap["log"]))
        self.assertTrue(snap["result"]["next"])

    def test_a_tampered_download_is_never_run(self):
        self.publish(tamper=True)
        snap = wait(self.installer(self.recipe()).start("ytm-beacon", "install", {}))
        self.assertEqual(snap["state"], "failed")
        self.assertIn("checksum", snap["error"])
        self.assertEqual([s["state"] for s in snap["steps"]][:3], ["done", "done", "failed"])
        self.assertFalse(self.mark.exists())

    def test_uninstall_runs_beacons_own_uninstaller(self):
        exe = self.home / ".local" / "bin" / "ytm-beacon"
        exe.parent.mkdir(parents=True)
        script(exe, FAKE_BEACON)
        r = self.recipe()
        self.assertEqual(r.installed()["version"], "0.1.9")
        snap = wait(self.installer(r).start("ytm-beacon", "uninstall", {"purge": True}))
        self.assertEqual(snap["state"], "failed")  # the fake doesn't delete itself, so "make sure it's gone" says so
        self.assertIn("beacon uninstall --purge", self.mark.read_text())
        self.assertIn("still there", snap["error"])

    def test_other_systems_pick_their_archive(self):
        self.assertEqual(self.recipe("windows", arch="arm64").archive(), "ytm-beacon_windows_arm64.zip")
        self.assertEqual(self.recipe("darwin", arch="arm64").archive(), "ytm-beacon_darwin_arm64.tar.gz")
        self.assertEqual(self.recipe("linux", arch="riscv64").support()["level"], "none")

    def test_cancel_stops_the_job(self):
        self.publish()
        r = self.recipe()
        slow = [Step("a", "slow", lambda s: s.sleep(5)), Step("b", "never", lambda s: None)]
        r.steps = lambda op, opts: slow
        inst = self.installer(r)
        job = inst.start("ytm-beacon", "install", {})
        time.sleep(0.2)
        inst.cancel(job.id)
        snap = wait(job)
        self.assertEqual(snap["state"], "cancelled")
        self.assertEqual([s["state"] for s in snap["steps"]], ["failed", "pending"])


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


# ---- Pocketfader ---------------------------------------------------------------------------
FAKE_PYTHON = r'''echo "python $*" >> "$MARK"
case "$1" in
  -c) echo 3.12; exit 0 ;;
  -m) case "$2" in
        venv) d="$4"; mkdir -p "$d/bin" "$d/lib/python3.12/site-packages"; cp "$0" "$d/bin/python"; exit 0 ;;
        pip) b=$(dirname "$0"); v=$(dirname "$b"); sp="$v/lib/python3.12/site-packages"
             mkdir -p "$sp/pocketfader-0.1.0a10.dist-info" "$sp/pocketfader/static"; echo '<svg/>' > "$sp/pocketfader/static/icon.svg"
             printf '#!/bin/sh\n' > "$b/pocketfader"; printf '#!/bin/sh\n' > "$b/pocketfader-gui"; chmod +x "$b/pocketfader" "$b/pocketfader-gui"; exit 0 ;;
      esac ;;
esac
exit 3
'''


@unix_only
class TestPocketfader(Base):
    def setUp(self):
        super().setUp()
        self.wheel = b"PK fake wheel"
        self.gh.publish("dash1101/pocketfader", "v0.1.0-alpha.10", {
            "pocketfader-0.1.0a10-py3-none-any.whl": self.wheel,
            "pocketfader-v0.1.0-alpha.10-windows-x64.zip": b"PK zip",
            "SHA256SUMS.txt": sums({"pocketfader-0.1.0a10-py3-none-any.whl": self.wheel,
                                    "pocketfader-v0.1.0-alpha.10-windows-x64.zip": b"PK zip"})}, prerelease=True)
        self.gh.publish("dash1101/pocketfader", "v0.1.0-alpha.9", {}, prerelease=True)  # listed after, but older

    def recipe(self, system="linux", **kw):
        r = PocketfaderRecipe(self.host(system, **kw), self.sources)
        r.port, r.wait_seconds = CLOSED_PORT, 1
        return r

    def test_linux_install_for_real_with_fake_tools(self):
        py = script(self.bin / "python3", FAKE_PYTHON)
        sc = script(self.bin / "systemctl", 'echo "systemctl $*" >> "$MARK"\n')
        r = self.recipe(which={"python3": py, "systemctl": sc, "pactl": "/usr/bin/pactl", "busctl": "/usr/bin/busctl"})
        info = r.info("install", {"lan": True, "ytdlp": False})
        self.assertFalse(info["blocked"], info)
        self.assertEqual(info["release"]["tag"], "v0.1.0-alpha.10")
        self.assertIn("home Wi-Fi", " ".join(info["summary"]))
        snap = wait(self.installer(r).start("pocketfader", "install", {"lan": True, "ytdlp": False}))
        self.assertEqual(snap["state"], "done", snap["error"] + "\n" + "\n".join(snap["log"]))
        unit = (self.home / ".config/systemd/user/pocketfader.service").read_text()
        self.assertIn("pocketfader/venv/bin/pocketfader --lan", unit)
        self.assertIn("Exec=", (self.home / ".local/share/applications/pocketfader.desktop").read_text())
        self.assertTrue((self.home / ".local/bin/pocketfader").is_symlink())
        m = self.mark.read_text()
        self.assertIn("systemctl --user enable pocketfader.service", m)
        self.assertIn("systemctl --user restart pocketfader.service", m)
        self.assertIn("--no-deps --force-reinstall", m)
        r.forget()
        self.assertEqual(r.installed()["version"], "0.1.0a10")
        self.assertTrue(r.options("install")[1]["value"])  # remembers --lan for the next reinstall

    def test_linux_blocks_without_pactl(self):
        py = script(self.bin / "python3", FAKE_PYTHON)
        info = self.recipe(which={"python3": py}).info("install")
        self.assertIn("needs a few things", info["blocked"])
        self.assertTrue(any("pulseaudio-utils" in r["hint"] for r in info["requirements"] if not r["ok"]))

    def test_windows_plan_dry_run(self):
        r = self.recipe("windows", which={"powershell": "powershell", "schtasks": "schtasks", "taskkill": "taskkill"})
        info = r.info("install")
        self.assertEqual(info["support"]["level"], "full")
        self.assertIn("Start menu", " ".join(info["summary"]))
        self.assertEqual(info["release"]["sizeText"], "6 B")
        snap = wait(self.installer(r, dry_run=True).start("pocketfader", "install", {"autostart": True}))
        self.assertEqual(snap["state"], "done", snap["error"])
        log = "\n".join(snap["log"])
        self.assertIn("install-exe.ps1", log)
        self.assertIn("Start-ScheduledTask -TaskName Pocketfader", log)
        self.assertIn("WScript.Shell", log)
        self.assertFalse(any(self.home.rglob("*")))  # dry run wrote nothing

    def test_macos_is_limited_and_says_why(self):
        py = script(self.bin / "python3", FAKE_PYTHON)
        r = self.recipe("darwin", which={"python3": py, "launchctl": "/bin/launchctl"})
        info = r.info("install")
        self.assertEqual(info["support"]["level"], "limited")
        self.assertIn("YouTube Music remote", info["support"]["note"])
        snap = wait(self.installer(r, dry_run=True).start("pocketfader", "install", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertIn("launchctl bootstrap gui/", "\n".join(snap["log"]))
        p = plist("app.pocketfader.server", ["/x/pocketfader", "--audio", "none", "--lan"], True, "/l.log")
        self.assertIn("<string>--audio</string>", p)
        self.assertIn("<true/>", p)

    def test_uninstall_keeps_settings_unless_asked(self):
        (self.home / ".local/share/pocketfader/venv/bin").mkdir(parents=True)
        (self.home / ".local/share/pocketfader/venv/bin/pocketfader").write_text("")
        (self.home / ".config/pocketfader").mkdir(parents=True)
        sc = script(self.bin / "systemctl", 'echo "systemctl $*" >> "$MARK"\n')
        r = self.recipe(which={"systemctl": sc})
        snap = wait(self.installer(r).start("pocketfader", "uninstall", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertFalse((self.home / ".local/share/pocketfader").exists())
        self.assertTrue((self.home / ".config/pocketfader").exists())
        self.assertIn("disable --now pocketfader.service", self.mark.read_text())

    def test_installed_another_way_is_left_alone(self):
        r = self.recipe(which={"pocketfader": "/usr/local/bin/pocketfader"})
        self.assertEqual(r.installed()["how"], "installed another way")
        self.assertIn("installed another way", r.info("uninstall")["blocked"])

    def test_bad_options_are_refused(self):
        from orbit.apps.base import BadRequest
        r = self.recipe()
        with self.assertRaises(BadRequest):
            r.info("install", {"nope": True})
        with self.assertRaises(BadRequest):
            r.info("install", {"lan": "yes"})
        with self.assertRaises(BadRequest):
            r.info("upgrade")


# ---- over HTTP -----------------------------------------------------------------------------
class TestInstallHTTP(Base):
    def setUp(self):
        super().setUp()
        from orbit.config import Config
        from orbit.runner import Runner
        from orbit.server import Hub, serve
        arc = targz({"ytm-beacon": (("#!/bin/sh\n" + FAKE_BEACON).encode(), 0o755)})
        self.gh.publish("dash1101/ytm-beacon", "v0.1.9", {"ytm-beacon_linux_amd64.tar.gz": arc,
                                                          "checksums.txt": sums({"ytm-beacon_linux_amd64.tar.gz": arc})})
        self.spawned = []
        runner = Runner(dry_run=True)
        runner.spawn = lambda argv: self.spawned.append(argv) or True
        runner.launch = lambda argv: self.spawned.append(argv) or True
        host = self.host()
        rec = BeaconRecipe(host, self.sources)
        rec.ports, rec.wait_tries = [CLOSED_PORT], 1
        self.hub = Hub(config=Config(self.tmp / "cfg.json"), runner=runner, app_classes=[], host=host,
                       sources=self.sources, recipes={"ytm-beacon": rec})
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
        self.assertEqual(lst["apps"][0]["id"], "ytm-beacon")
        plan = self.call("/api/install/ytm-beacon/plan", {"op": "install", "options": {"setup": False}})
        self.assertEqual(plan["steps"][1]["label"], "Download")
        job = self.call("/api/install/ytm-beacon/start", {"op": "install", "options": {}})
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
            self.call("/api/install/ytm-beacon/start", {"op": "uninstall"})   # not installed here
        self.assertEqual(c.exception.status, 400)
        self.assertIn("isn't installed", str(c.exception))
        with self.assertRaises(net.HttpError) as c:
            net.request("POST", self.url + "/api/install/ytm-beacon/start", body={"op": "install"}, timeout=5,
                        headers={"Origin": "https://evil.example"})
        self.assertEqual(c.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
