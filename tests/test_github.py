"""Signing in with GitHub (device flow) against a fake GitHub, the token order, and the apps-menu launcher."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from orbit import desktop_entry, net
from orbit.config import Config
from orbit.github_login import Login, LoginError
from orbit.runner import Runner


class FakeGitHub:
    def __init__(self, polls):
        self.polls = list(polls)      # what each token poll answers
        self.calls = []

    def __call__(self, url, fields):
        self.calls.append((url, dict(fields)))
        if url.endswith("/login/device/code"):
            return {"device_code": "DEV", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device",
                    "interval": 0, "expires_in": 900}
        return self.polls.pop(0) if self.polls else {"error": "authorization_pending"}


def wait_until(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(Path(tempfile.mkdtemp()) / "config.json")
        self.addCleanup(lambda: net.set_login_provider(None))

    def login(self, polls, client_id="Iv1.test"):
        gh = FakeGitHub(polls)
        lg = Login(self.cfg, client_id=client_id, post=gh, user=lambda tok: "octo")
        lg.MIN_INTERVAL = 0
        return lg, gh

    def test_sign_in_and_out(self):
        lg, gh = self.login([{"error": "authorization_pending"}, {"error": "slow_down", "interval": 0},
                             {"access_token": "ghu_secret", "token_type": "bearer"}])
        st = lg.start()
        self.assertEqual(st["pending"]["user_code"], "ABCD-1234")
        self.assertNotIn("device_code", st["pending"])             # never handed to the page
        self.assertTrue(wait_until(lambda: lg.state()["source"] == "orbit"))
        st = lg.state()
        self.assertEqual((st["source"], st["login"], st["pending"]), ("orbit", "octo", None))
        self.assertEqual(self.cfg.get("github")["token"], "ghu_secret")
        self.assertEqual(oct(os.stat(self.cfg.path).st_mode & 0o777), "0o600")
        self.assertEqual(gh.calls[-1][1]["grant_type"], "urn:ietf:params:oauth:grant-type:device_code")
        self.assertNotIn("client_secret", gh.calls[-1][1])
        net.set_login_provider(lg.token)
        self.assertEqual(net.github_token(), "ghu_secret")         # Orbit's own sign-in comes first
        lg.sign_out()
        self.assertEqual(lg.token(), "")
        self.assertNotEqual(lg.state()["source"], "orbit")

    def test_cancelled_on_github(self):
        lg, _ = self.login([{"error": "access_denied"}])
        lg.start()
        self.assertTrue(wait_until(lambda: lg.state()["pending"] is None))
        self.assertIn("cancelled on GitHub", lg.state()["error"])
        self.assertEqual(lg.token(), "")

    def test_not_set_up(self):
        lg, _ = self.login([], client_id="")
        self.assertFalse(lg.state()["available"])
        with self.assertRaises(LoginError):
            lg.start()


class HubTests(unittest.TestCase):
    def test_token_never_reaches_the_page(self):
        from orbit.server import Hub
        from tests.test_orbit import FakeRunner, NoReleases
        tmp = tempfile.mkdtemp()
        cfg = Config(Path(tmp) / "config.json")
        cfg.update({"github": {"token": "ghu_topsecret", "login": "octo"}})
        hub = Hub(config=cfg, runner=FakeRunner(), releases=NoReleases(), app_classes=())
        self.addCleanup(lambda: net.set_login_provider(None))
        info = hub.hub_info()
        self.assertEqual(info["github"]["login"], "octo")
        self.assertNotIn("ghu_topsecret", json.dumps(info))
        self.assertNotIn("ghu_topsecret", json.dumps(hub.github.state()))
        self.assertEqual(net.github_token(), "ghu_topsecret")


@unittest.skipUnless(os.name == "posix", "Linux launcher")
class MenuTests(unittest.TestCase):
    def test_apps_menu_launcher(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), mock.patch("sys.platform", "linux"):
            r = desktop_entry.add(Runner(dry_run=True))
            self.assertIn("dry run", r["message"])
            self.assertFalse(Path(tmp, "applications", "orbit.desktop").exists())
            r = desktop_entry.add(Runner())
            self.assertTrue(r["ok"], r)
            entry = Path(tmp, "applications", "orbit.desktop").read_text()
            self.assertIn("Exec=", entry)
            self.assertIn("Icon=orbit", entry)
            self.assertTrue(Path(tmp, "icons", "hicolor", "scalable", "apps", "orbit.svg").is_file())
            self.assertTrue(desktop_entry.state()["added"])


if __name__ == "__main__":
    unittest.main()
