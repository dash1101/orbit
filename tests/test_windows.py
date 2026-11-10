"""Windows behaviour, checked from any system: Orbit must never flash a console window, never run Linux-only tools
there, and must not wait on apps that aren't running."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orbit import net
from orbit.apps.beacon import Beacon
from orbit.apps.nova import Nova
from orbit.apps.pocketfader import Pocketfader
from orbit.config import Config
from orbit.install.engine import Sources
from orbit.install.host import Host
from orbit.install.recipes import load as load_recipes
from orbit.runner import Runner
from orbit.server import Hub
from tests.test_orbit import NoReleases

CREATE_NO_WINDOW = 0x08000000
LINUX_ONLY = {"hostname", "systemctl", "journalctl", "tailscale", "xdg-open", "pactl", "busctl"}


class QuietBeacon(Beacon):
    def __init__(self, ctx):
        super().__init__(ctx, ports=(9, 10, 11))       # nothing listens there


class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        net.forget_ports()
        self.addCleanup(net.forget_ports)
        self.addCleanup(lambda: net.set_login_provider(None))

    def hub(self):
        cfg = Config(Path(self.tmp) / "config.json")
        cfg.update({"apps": {"pocketfader": {"url": "http://127.0.0.1:9"}}})
        host = Host(system="windows", arch="amd64", home=self.tmp, env={}, which={}, systemd=False)
        src = Sources(api="http://127.0.0.1:9", allow=("http://127.0.0.1:9",), token=lambda: "")
        return Hub(config=cfg, runner=Runner(), releases=NoReleases(), app_classes=[Pocketfader, Nova, QuietBeacon],
                   host=host, sources=src, recipes=load_recipes(host, src))

    def test_no_console_windows_and_no_linux_tools(self):
        calls = []

        def run(argv, *a, **kw):
            calls.append((list(argv), kw.get("creationflags", 0)))
            return subprocess.CompletedProcess(argv, 1, "", "")

        def popen(argv, *a, **kw):
            calls.append((list(argv), kw.get("creationflags", 0)))
            raise OSError("not started in tests")

        which = {"gh": r"C:\Program Files\GitHub CLI\gh.exe", "hostname": r"C:\Windows\System32\hostname.exe",
                 "tailscale": r"C:\Program Files\Tailscale\tailscale.exe"}.get   # tools a Windows PC may well have
        with mock.patch("sys.platform", "win32"), mock.patch("subprocess.run", run), \
                mock.patch("subprocess.Popen", popen), mock.patch("shutil.which", lambda n, *a, **k: which(n)):
            hub = self.hub()
            for _ in range(2):
                hub._cache.clear()
                hub.overview()
            for app in ("pocketfader", "nova", "ytm-beacon"):
                hub.detail(app)
                hub.app(app).logs()
            hub.hub_info()
            nova = next(a for a in hub.overview() if a["id"] == "nova")
            service_buttons = [a for a in hub.detail("pocketfader")["actions"] if a["id"].startswith("svc:")]
        self.assertEqual(nova["headline"], "Not available on Windows yet")
        for argv, flags in calls:
            self.assertTrue(flags & CREATE_NO_WINDOW, f"{argv} would flash a console window")
        ran = {Path(argv[0]).name.lower().removesuffix(".exe") for argv, _ in calls}
        self.assertFalse(ran & LINUX_ONLY, f"ran Linux-only tools on Windows: {ran & LINUX_ONLY}")
        self.assertFalse(service_buttons, "no systemd service buttons on Windows")

    def test_links_open_through_the_windows_shell(self):
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile", create=True) as start:
            self.assertTrue(Runner().open_url("https://github.com/dash1101/orbit"))
        start.assert_called_once_with("https://github.com/dash1101/orbit")

    def test_an_app_that_isnt_running_is_only_checked_once_in_a_while(self):
        tries = []
        real = net.port_open
        with mock.patch.object(net, "port_open", lambda h, p, timeout=0.3: tries.append(p) or real(h, p, timeout)):
            for _ in range(5):
                with self.assertRaises(net.HttpError):
                    net.get_json("http://127.0.0.1:9/api/info", timeout=2)
            self.assertEqual(tries, [9])                   # one quick look, then remembered
            net.forget_ports()                             # e.g. after an action that may have started it
            with self.assertRaises(net.HttpError):
                net.get_json("http://127.0.0.1:9/api/info", timeout=2)
            self.assertEqual(tries, [9, 9])


if __name__ == "__main__":
    unittest.main()
