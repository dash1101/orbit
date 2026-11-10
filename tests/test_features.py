"""In-app terminals, terminal actions, Nova's and Beacon's new actions and settings, custom themes, icons and
"not available on this system yet". Fakes and harmless shell commands only."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from orbit.apps.base import App, BadRequest
from orbit.apps.beacon import Beacon
from orbit.apps.nova import Nova
from orbit.install.engine import Sources
from orbit.install.host import Host
from orbit.install.recipes import load as load_recipes
from orbit.term import Terminals
from tests.test_orbit import RUNNING, Stub, make_hub


def wait_done(session, timeout=10):
    end = time.time() + timeout
    while session.ended is None and time.time() < end:
        time.sleep(0.05)
    return session.snapshot()


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.t = Terminals()
        self.addCleanup(self.t.stop_all)

    def test_real_terminal_input_and_hidden_password(self):
        s = self.t.start(["bash", "-c", 'tty >/dev/null && echo tty-ok; read -s -p "pw? " p; echo; echo "len ${#p}"'],
                         "t", cols=77)
        time.sleep(0.3)
        s.write("hunter2\r")
        snap = wait_done(s)
        self.assertIn("tty-ok", snap["data"])
        self.assertIn("len 7", snap["data"])
        self.assertNotIn("hunter2", snap["data"])   # not echoed
        self.assertEqual(snap["code"], 0)

    def test_ctrl_c_and_cleanup_and_mask(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        s = self.t.start(["bash", "-c", "sleep 30", "_", "SECRET-TOKEN"], "t", cleanup=[path], mask=["SECRET-TOKEN"])
        time.sleep(0.2)
        s.interrupt()
        snap = wait_done(s)
        self.assertFalse(snap["running"])
        self.assertNotIn("SECRET-TOKEN", snap["data"])
        self.assertFalse(Path(path).exists())

    def test_output_is_read_from_an_offset(self):
        s = self.t.start(["printf", "abc"], "t")
        snap = wait_done(s)
        later = s.snapshot(snap["offset"])
        self.assertEqual(later["data"], "")
        self.assertIn("abc", snap["data"])


class TermApp(App):
    id, name = "termapp", "Term app"

    def status(self):
        return {"state": "ok"}

    def actions(self):
        return [{"id": "look", "label": "look", "term": True},
                {"id": "change", "label": "change", "term": True, "changes": True}]

    def term_command(self, action, args):
        return {"argv": ["printf", action]}


class TerminalActionTests(unittest.TestCase):
    def test_dry_run_runs_read_only_commands_and_blocks_changes(self):
        hub = make_hub(dry_run=True, app_classes=[TermApp])
        self.addCleanup(hub.terminals.stop_all)
        a = hub.apps["termapp"]
        r = hub.run_action(a, "change", {})
        self.assertIn("dry run: would run printf change", r["message"])
        self.assertNotIn("term", r)
        r = hub.run_action(a, "look", {})
        s = hub.terminals.get(r["term"])
        self.assertIn("look", wait_done(s)["data"])
        with self.assertRaises(BadRequest):
            hub.run_action(a, "nope", {})


class NovaActionTests(unittest.TestCase):
    def nova(self, dry_run=False):
        hub = make_hub(dry_run=dry_run, app_classes=())
        return Nova(hub.ctx, local_hosts=[])

    def test_commands_are_fixed(self):
        n = self.nova()
        ids = {a["id"] for a in n.actions() if a.get("term")}
        self.assertIn("term:cf-token", ids)
        self.assertIn("term:tailscale-up", ids)
        self.assertEqual(n.term_command("term:status", {})["argv"], ["sudo", "nova", "status"])
        self.assertIsNone(n.term_command("term:anything-else", {}))
        with self.assertRaises(BadRequest):
            n.term_command("term:approve", {"value": "x; rm -rf /"})
        self.assertEqual(n.term_command("term:approve", {"value": "AB12 CD34"})["argv"][-1], "AB12CD34")

    @unittest.skipUnless(os.path.exists("/usr/lib/nova-api/cfsync.py"), "needs Nova installed")
    def test_cloudflare_token_goes_through_a_private_file(self):
        n = self.nova()
        tok = "a" * 40
        cmd = n.term_command("term:cf-token", {"value": tok})
        path = cmd["argv"][-1]
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        self.assertNotIn(tok, " ".join(cmd["argv"]))
        self.assertEqual(Path(path).read_text(), tok)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(cmd["cleanup"], [path])
        with self.assertRaises(BadRequest):
            n.term_command("term:cf-token", {"value": "too short"})


class BeaconFeatureTests(unittest.TestCase):
    def setUp(self):
        self.state = {"app": "ytm-beacon", "version": "v0.1.4", "commit": "abc", "os": "linux", "arch": "amd64",
                      "pid": 42, "port": 41713, "uptimeSec": 7300, "visible": True, "presenceActive": True,
                      "discord": {"state": "connected", "user": "someone",
                                  "endpoint": "/run/user/1000/.flatpak/dev.vencord.Vesktop/xdg-run/discord-ipc-0"},
                      "extension": {"lastSeenAgoSec": 5, "everConnected": True}, "nowPlaying": None,
                      "config": {"enabled": True, "clientId": ""}, "extensionBuild": "b1",
                      "extensionDirs": {"chrome": "/x/chrome"}}
        self.posts = []
        self.stub = Stub({("GET", "/v1/status"): lambda b: (200, self.state),
                          ("GET", "/v1/ping"): lambda b: (200, {"app": "ytm-beacon"}),
                          ("POST", "/v1/config"): lambda b: self.posts.append(b) or (200, b)})
        self.addCleanup(self.stub.close)

    def app(self):
        hub = make_hub({"ytm-beacon.service": RUNNING}, app_classes=())
        return Beacon(hub.ctx, ports=(self.stub.port,))

    def test_details(self):
        more = dict(self.app().status()["more"])
        self.assertEqual(more["discord app"], "Vesktop")
        self.assertIn("YTM Beacon's", more["application id"])
        self.assertEqual(more["running for"], "2 h 1 min")

    def test_application_id(self):
        b = self.app()
        with self.assertRaises(BadRequest):
            b.apply_settings({"clientId": "12ab"})
        b.apply_settings({"clientId": " 123456789012345678 "})
        self.assertEqual(self.posts[-1], {"clientId": "123456789012345678"})
        b.apply_settings({"clientId": ""})   # back to Beacon's own
        self.assertEqual(self.posts[-1], {"clientId": ""})

    def test_doctor_runs_in_orbit(self):
        b = self.app()
        b.binary = lambda: "/bin/ytm-beacon"
        self.assertEqual(b.term_command("update-check", {})["argv"], ["/bin/ytm-beacon", "update", "--check"])
        self.assertTrue(next(a for a in b.actions() if a["id"] == "update")["changes"])


class ThemeTests(unittest.TestCase):
    TOK = {k: "#112233" for k in ("bg", "raise", "line", "line-hi", "text", "muted", "legend", "accent", "accent-ink",
                                   "ok", "warn", "down", "off", "sel")}

    def test_custom_themes(self):
        hub = make_hub(app_classes=())
        info = hub.set_hub({"themes": [{"id": "custom-a", "label": " Harbor ", "tokens": dict(self.TOK, radius=6)}],
                            "theme": "custom-a"})
        self.assertEqual(info["settings"]["theme"], "custom-a")
        mine = [t for t in info["themes"] if t.get("custom")]
        self.assertEqual(mine[0]["label"], "Harbor")
        self.assertFalse(mine[0]["tokens"]["glow"])
        for bad in ({"id": "evil", "label": "x", "tokens": self.TOK},
                    {"id": "custom-b", "label": "x", "tokens": dict(self.TOK, bg="red")},
                    {"id": "custom-b", "label": "x", "tokens": dict(self.TOK, radius=99)},
                    {"id": "custom-b", "label": "x", "tokens": dict(self.TOK, script="#000000")}):
            with self.assertRaises(BadRequest):
                hub.set_hub({"themes": [bad]})
        info = hub.set_hub({"themes": []})   # deleting the theme in use falls back to the default
        self.assertEqual(info["settings"]["theme"], "tui")
        self.assertEqual(len([t for t in info["themes"] if not t.get("custom")]), 9)


class AvailabilityTests(unittest.TestCase):
    def test_not_available_on_this_system_yet(self):
        tmp = tempfile.mkdtemp()
        host = Host(system="windows", arch="amd64", home=tmp, env={}, which={}, systemd=False)
        src = Sources(api="http://127.0.0.1:9", allow=("http://127.0.0.1:9",), token=lambda: "")
        hub = make_hub(app_classes=[Nova], tmp=tmp)
        hub2 = type(hub)(config=hub.ctx.config, runner=hub.ctx.runner, releases=hub.ctx.releases, app_classes=[Nova],
                         host=host, sources=src, recipes=load_recipes(host, src))
        card = next(a for a in hub2.overview() if a["id"] == "nova")
        self.assertEqual(card["headline"], "Not available on Windows yet")
        self.assertEqual(card["state"], "off")
        self.assertTrue(card["unavailable"])
        self.assertEqual(card["icon"], "/apps/nova.svg")
        self.assertIn("isn't available on Windows yet", hub2.detail("nova")["status"]["detail"])


if __name__ == "__main__":
    unittest.main()
