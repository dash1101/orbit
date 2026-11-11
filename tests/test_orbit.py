"""Orbit tests. Everything runs against fakes and stub servers: nothing here touches real services."""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orbit import net, systemd
from orbit.apps import base
from orbit.apps.base import App, BadRequest, coerce, field
from orbit.apps.beacon import Beacon, headline
from orbit.apps.custom import validate
from orbit.apps.nova import Nova
from orbit.apps.pocketfader import Pocketfader
from orbit.config import Config
from orbit.runner import Runner
from orbit.server import Hub, serve


# ------------------------------------------------------------------------------------ fakes
class FakeRunner(Runner):
    """systemctl/journalctl answers from a dict; writes are recorded, never run."""

    def __init__(self, units=None, dry_run=False):
        super().__init__(dry_run=dry_run)
        self.units = units or {}
        self.writes, self.spawns = [], []

    def read(self, argv, timeout=5):
        if argv[:1] == ["systemctl"] and "show" in argv:
            unit = argv[argv.index("show") + 1]
            props = self.units.get(unit, {"LoadState": "not-found", "ActiveState": "inactive"})
            return 0, "\n".join(f"{k}={v}" for k, v in props.items())
        if argv[:1] == ["journalctl"]:
            return 0, "line one\nline two\n"
        if argv[:1] == ["hostname"]:
            return 0, ""
        return 127, "not faked"

    def write(self, argv, timeout=120):
        if self.dry_run:
            return super().write(argv, timeout)
        self.writes.append(argv)
        return 0, "ok"

    def launch(self, argv):
        self.spawns.append(list(argv))
        return True

    def spawn(self, argv):
        self.spawns.append(argv)
        return True


RUNNING = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "UnitFileState": "enabled",
           "Result": "success", "ActiveEnterTimestamp": "Mon 2026-11-09 01:00:00 EST"}
STOPPED = {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "disabled",
           "Result": "success"}
FAILED = {"LoadState": "loaded", "ActiveState": "failed", "SubState": "failed", "UnitFileState": "enabled",
          "Result": "exit-code"}


class Stub:
    """A tiny JSON server: routes = {(method, path): callable(body) -> (status, obj)}. Records requests."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                stub.calls.append((method, self.path, body, self.headers.get("Origin")))
                fn = stub.routes.get((method, self.path))
                code, obj = fn(body) if fn else (404, {"error": "nope"})
                data = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class NoReleases:
    def latest(self, repo, force=False):
        return {"ok": True, "release": {"tag": "v9.9.9", "url": "https://example.invalid"}}

    def cached(self, repo):
        return None


def make_hub(units=None, dry_run=False, app_classes=None, tmp=None):
    tmp = tmp or tempfile.mkdtemp()
    return Hub(config=Config(Path(tmp) / "config.json"), runner=FakeRunner(units, dry_run=dry_run),
               releases=NoReleases(), app_classes=app_classes)


# ------------------------------------------------------------------------------------ units
class SystemdTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(systemd.summarize("a.service", RUNNING)["state"], "running")
        self.assertEqual(systemd.summarize("a.service", STOPPED)["state"], "stopped")
        self.assertEqual(systemd.summarize("a.service", FAILED)["state"], "failed")
        self.assertEqual(systemd.summarize("a.service", {"LoadState": "not-found"})["state"], "missing")
        timer = dict(RUNNING, SubState="waiting")
        self.assertEqual(systemd.summarize("a.timer", timer)["state"], "running")

    def test_unit_names(self):
        self.assertTrue(systemd.valid_unit("ytm-beacon.service"))
        self.assertTrue(systemd.valid_unit("nova-helper.socket"))
        for bad in ("", "x", "a b.service", "--now.service; rm", "../x.service", "x.exe"):
            self.assertFalse(systemd.valid_unit(bad), bad)

    def test_system_units_are_read_only(self):
        r = FakeRunner()
        ok, _ = systemd.act(r, "nova-api.service", "restart", user=False)
        self.assertFalse(ok)
        self.assertEqual(r.writes, [])

    def test_only_known_verbs(self):
        r = FakeRunner()
        self.assertFalse(systemd.act(r, "x.service", "mask")[0])
        self.assertTrue(systemd.act(r, "x.service", "restart")[0])
        self.assertEqual(r.writes, [["systemctl", "--user", "restart", "x.service"]])


class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(net.is_newer("v0.1.5", "v0.1.4"))
        self.assertFalse(net.is_newer("v0.1.4", "v0.1.4"))
        self.assertFalse(net.is_newer("v0.9.13-alpha", "0.9.13-alpha"))
        self.assertTrue(net.is_newer("v0.9.14-alpha", "0.9.13~alpha"))
        self.assertTrue(net.is_newer("v0.10.0", "0.9.13"))
        self.assertFalse(net.is_newer("v0.1.0-alpha.9", "0.1.0a9"))
        self.assertTrue(net.is_newer("v0.1.0-alpha.10", "0.1.0a9"))
        self.assertTrue(net.is_newer("v0.1.0", "0.1.0a9"))
        self.assertFalse(net.is_newer("", "1.0"))


class FieldTests(unittest.TestCase):
    def test_coerce(self):
        self.assertIs(coerce(field("a", "A", "bool", False), True), True)
        with self.assertRaises(BadRequest):
            coerce(field("a", "A", "bool", False), "yes")
        n = field("n", "N", "int", 0, min=0, max=10)
        self.assertEqual(coerce(n, 5), 5)
        for bad in (11, -1, 2.5, True, "3"):
            with self.assertRaises(BadRequest):
                coerce(n, bad)
        c = field("c", "C", "choice", "x", options=[["x", "X"], ["y", "Y"]])
        self.assertEqual(coerce(c, "y"), "y")
        with self.assertRaises(BadRequest):
            coerce(c, "z")
        w = field("w", "W", "words", [])
        self.assertEqual(coerce(w, "a, b ,,c"), ["a", "b", "c"])
        with self.assertRaises(BadRequest):
            coerce(w, ["x" * 81])
        with self.assertRaises(BadRequest):
            coerce(field("t", "T", "note", ""), "x")


# ------------------------------------------------------------------------------------ beacon
def beacon_status(**over):
    s = {"app": "ytm-beacon", "version": "v0.1.4", "port": 0, "visible": True, "snoozeSec": 0, "hidden": "",
         "presenceActive": True, "discord": {"state": "connected"},
         "extension": {"lastSeenAgoSec": 3, "everConnected": True, "quietAfterSec": 90},
         "nowPlaying": {"title": "Song", "artist": "Artist", "state": "playing"},
         "config": {"enabled": True, "privateMode": False, "statusDisplay": "name", "showPaused": True,
                    "pauseTimeoutMinutes": 5, "showAlbumArt": True, "showProgress": True, "showStateIcon": True,
                    "showButtons": True, "hideMatching": []}}
    s.update(over)
    return s


class BeaconTests(unittest.TestCase):
    def setUp(self):
        self.state = beacon_status()
        self.stub = Stub({
            ("GET", "/v1/ping"): lambda b: (200, {"app": "ytm-beacon", "version": "v0.1.4"}),
            ("GET", "/v1/status"): lambda b: (200, self.state),
            ("POST", "/v1/visibility"): lambda b: (200, {"enabled": b["action"] == "show"}),
            ("POST", "/v1/config"): lambda b: (200, b),
            ("GET", "/v1/logs"): lambda b: (200, "beacon log"),
        })
        self.addCleanup(self.stub.close)

    def app(self, dry_run=False):
        hub = make_hub({"ytm-beacon.service": RUNNING}, dry_run=dry_run, app_classes=())
        b = Beacon(hub.ctx, ports=(1, self.stub.port))  # port 1 never answers: the scan moves on
        return b

    def test_headlines(self):
        self.assertEqual(headline(beacon_status()), "showing")
        self.assertEqual(headline(beacon_status(visible=False)), "off")
        self.assertEqual(headline(beacon_status(visible=False, snoozeSec=60)), "snoozed")
        self.assertEqual(headline(beacon_status(discord={"state": "connecting"})), "discord-down")
        self.assertEqual(headline(beacon_status(extension={"lastSeenAgoSec": None, "everConnected": False})), "extension-missing")
        self.assertEqual(headline(beacon_status(extension={"lastSeenAgoSec": 500, "quietAfterSec": 90})), "extension-quiet")
        self.assertEqual(headline(beacon_status(nowPlaying=None)), "idle")
        self.assertEqual(headline(beacon_status(hidden="paused")), "paused-hidden")

    def test_status(self):
        s = self.app().status()
        self.assertEqual(s["state"], "ok")
        self.assertEqual(s["headline"], "Showing on Discord")
        self.assertIn(["playing", "Song — Artist"], s["facts"])
        self.assertTrue(s["update"]["available"])  # fake release v9.9.9

    def test_settings_go_to_beacon_without_origin(self):
        b = self.app()
        b.apply_settings({"enabled": False, "privateMode": True, "hideMatching": "a, b"})
        posts = [(c[1], c[2], c[3]) for c in self.stub.calls if c[0] == "POST"]
        self.assertIn(("/v1/visibility", {"action": "hide"}, None), posts)
        self.assertIn(("/v1/config", {"privateMode": True, "hideMatching": ["a", "b"]}, None), posts)

    def test_dry_run_sends_nothing(self):
        b = self.app(dry_run=True)
        b.apply_settings({"privateMode": True})
        b.run_action("hide:30", {})
        self.assertFalse([c for c in self.stub.calls if c[0] == "POST"])

    def test_rejects_bad_settings(self):
        with self.assertRaises(BadRequest):
            self.app().apply_settings({"pauseTimeoutMinutes": 5000})
        with self.assertRaises(BadRequest):
            self.app().apply_settings({"clientId": "123"})  # not exposed

    def test_hide_for(self):
        self.app().run_action("hide:60", {})
        self.assertIn(("POST", "/v1/visibility", {"action": "hide", "minutes": 60}, None),
                      [c for c in self.stub.calls])

    def test_not_running(self):
        self.stub.close()
        b = self.app()
        b.binary = lambda: "/x/ytm-beacon"
        hub_units = b.ctx.runner.units
        hub_units["ytm-beacon.service"] = STOPPED
        s = b.status()
        self.assertEqual((s["state"], s["headline"]), ("off", "Not running"))
        self.stub = Stub({})  # so cleanup has something to close


# ------------------------------------------------------------------------------------ pocketfader
class PocketfaderTests(unittest.TestCase):
    def setUp(self):
        self.prefs = {"pauseOthers": False}
        self.stub = Stub({
            ("GET", "/api/info"): lambda b: (200, {"name": "pocketfader", "version": "0.1.0a9", "auth": False}),
            ("GET", "/api/state"): lambda b: (200, {"volume": {"available": True, "percent": 40, "muted": False, "device": "Desk"},
                                                    "media": {"title": "T", "artist": "A", "playing": True},
                                                    "capabilities": {"eq": True, "engine_label": "Built-in EQ", "ytm_bridge": True}}),
            ("GET", "/api/ytm/state"): lambda b: (200, {"connected": False}),
            ("GET", "/api/update"): lambda b: (200, {"latest": "v0.1.0-alpha.9", "available": False}),
            ("GET", "/api/prefs"): lambda b: (200, self.prefs),
            ("POST", "/api/prefs"): lambda b: (self.prefs.update(b), (200, self.prefs))[1],
        })
        self.addCleanup(self.stub.close)
        self.hub = make_hub({"pocketfader.service": RUNNING}, app_classes=(Pocketfader,))
        self.hub.ctx.config.update_app("pocketfader", {"url": f"http://127.0.0.1:{self.stub.port}"})
        self.pf = self.hub.app("pocketfader")

    def test_status(self):
        s = self.pf.status()
        self.assertEqual(s["state"], "ok")
        self.assertIn(["output", "Desk"], s["facts"])
        self.assertIn(["playing", "T — A"], s["facts"])
        self.assertFalse(s["update"]["available"])

    def test_pref_and_hub_settings_split(self):
        self.pf.apply_settings({"pauseOthers": True, "token": "abc"})
        self.assertTrue(self.prefs["pauseOthers"])
        self.assertEqual(self.hub.ctx.config.app("pocketfader")["token"], "abc")
        self.assertNotIn("token", self.prefs)

    def test_token_is_sent(self):
        self.hub.ctx.config.update_app("pocketfader", {"token": "sekrit"})
        seen = []
        self.stub.routes[("GET", "/api/info")] = lambda b: (200, {"version": "x", "auth": True})
        orig = self.stub.httpd.RequestHandlerClass.do_GET

        def spy(handler):
            seen.append(handler.headers.get("Authorization"))
            orig(handler)
        self.stub.httpd.RequestHandlerClass.do_GET = spy
        self.pf.status()
        self.assertIn("Bearer sekrit", seen)

    def test_unreachable_and_stopped(self):
        self.stub.close()
        self.hub.ctx.runner.units["pocketfader.service"] = STOPPED
        self.assertEqual(self.pf.status()["state"], "off")
        self.hub.ctx.runner.units["pocketfader.service"] = FAILED
        self.assertEqual(self.pf.status()["state"], "down")
        self.stub = Stub({})

    def test_service_actions(self):
        ids = [a["id"] for a in self.pf.actions()]
        self.assertIn("svc:restart", ids)
        self.assertIn("svc:disable", ids)
        self.pf.run_action("svc:restart", {})
        self.assertEqual(self.hub.ctx.runner.writes[-1], ["systemctl", "--user", "restart", "pocketfader.service"])
        self.pf.run_action("open", {})
        self.assertEqual(self.hub.ctx.runner.spawns[-1][0], "xdg-open")


# ------------------------------------------------------------------------------------ nova
class NovaTests(unittest.TestCase):
    def test_not_installed(self):
        hub = make_hub(app_classes=())
        n = Nova(hub.ctx, local_hosts=[])
        s = n.status()
        self.assertEqual(s["state"], "off")
        self.assertIn("Not installed", s["headline"])

    def test_units_down_and_failed_job(self):
        units = {u: RUNNING for u in ("nova-api.service", "nova-gate.service", "nova-helper.socket", "nova-alerts.timer",
                                      "nova-backups.timer", "nova-labs.timer", "nova-update-check.timer")}
        units["nova-update-check.service"] = FAILED
        hub = make_hub(units, app_classes=())
        n = Nova(hub.ctx, local_hosts=[])
        s = n.status()
        self.assertEqual(s["state"], "down")  # nothing answers /ping
        self.assertIn(["last run failed", "update check", "warn"], s["facts"])

    UNITS = {u: RUNNING for u in ("nova-api.service", "nova-gate.service", "nova-helper.socket", "nova-alerts.timer",
                                  "nova-backups.timer", "nova-labs.timer", "nova-update-check.timer")}

    def _nova(self, status=None, units=None):
        """A Nova whose monitor file holds [status] (None: no file) — as text when it isn't a dict."""
        hub = make_hub(dict(self.UNITS if units is None else units), app_classes=())
        n = Nova(hub.ctx, local_hosts=[])
        n.status_file = Path(tempfile.mkdtemp()) / "status.json"
        if status is not None:
            n.status_file.write_text(status if isinstance(status, str) else json.dumps(status), encoding="utf-8")
        return n

    @staticmethod
    def _monitor(level="ok", active=(), age=30, **metrics):
        from datetime import datetime, timedelta, timezone
        return {"updated": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(timespec="seconds"),
                "level": level, "headline": "\U0001F7E2 All systems normal", "active": list(active), "metrics": metrics}

    def test_health_from_the_monitor_file(self):
        n = self._nova(self._monitor(containers="11/11 running", websites="3/3 up", data_backup="5h ago", uptime="0d 14h",
                                     storage=[{"name": "System drive", "mount": "/", "pct": 84.8, "free": 18e9},
                                              {"name": "Photos", "mount": "/mnt/p", "pct": 94.2, "free": 115e9}]))
        hl = n.health()
        self.assertEqual((hl["level"], hl["headline"], hl["issues"], hl["stale"]), ("ok", "All systems normal", [], False))
        facts = n.status()["facts"]
        self.assertIn(["containers", "11/11 running"], facts)
        self.assertIn(["websites", "3/3 up"], facts)
        self.assertIn(["storage", "Photos 94% · 115 GB free (fullest of 2)", "warn"], facts)
        self.assertIn(["last backup", "5h ago"], facts)
        self.assertIn(["up for", "0d 14h"], facts)

    def test_health_lists_what_needs_attention(self):
        active = [{"level": "warning", "title": "\u26a0\ufe0f Swap 87% used"}, {"level": "critical", "title": "\U0001F534 Drive sdb is failing"},
                  {"level": "ok", "title": "\U0001F7E2 Nothing active"}]
        n = self._nova(self._monitor("critical", active))
        self.assertEqual(n.health()["issues"], ["Swap 87% used", "Drive sdb is failing"])
        facts = n.status()["facts"]
        self.assertIn(["needs attention", "Swap 87% used", "warn"], facts)
        self.assertIn(["", "Drive sdb is failing", "warn"], facts)     # the label once, for the first

    def test_health_headlines_when_nova_answers(self):
        from unittest import mock
        answer = {"ok": True, "api": "0.9.14-alpha", "api_level": 3, "min_level": 0}
        cases = ((self._monitor(), "ok", "Running · all systems normal"),
                 (self._monitor("warning", [{"level": "warning", "title": "\u26a0\ufe0f Swap 87% used"}]), "warn",
                  "Running · 1 needs attention: Swap 87% used"),
                 (self._monitor("warning", [{"level": "warning", "title": "a"}, {"level": "warning", "title": "b"}]), "warn",
                  "Running · 2 need attention: a"),
                 (self._monitor(age=3 * 3600), "warn", "Running, but its alerts have stopped checking the server"),
                 (None, "ok", "Running"))
        for status, state, headline in cases:
            n = self._nova(status)
            with mock.patch.object(Nova, "address", return_value="https://127.0.0.1:8495"), \
                    mock.patch("orbit.apps.nova.net.get_json", return_value=answer):
                s = n.status()
            self.assertEqual((s["state"], s["headline"]), (state, headline))
            self.assertEqual(s["version"], "0.9.14-alpha")

    def test_health_when_the_monitor_stopped_or_the_file_is_odd(self):
        n = self._nova(self._monitor("warning", [{"level": "warning", "title": "old news"}], age=2 * 3600, containers="1/1 running"))
        self.assertTrue(n.health()["stale"])
        facts = n.status()["facts"]
        self.assertIn(["monitor", "last checked 2h ago: Nova's alerts aren't running", "warn"], facts)
        self.assertNotIn(["containers", "1/1 running"], facts)                 # old numbers aren't shown as current
        for odd in ("not json", "[]", json.dumps({"level": "purple"}), json.dumps({"level": "ok", "metrics": "x", "active": "y"})):
            n = self._nova(odd)
            n.status()                                                         # never raises
        self.assertIsNone(self._nova("[]").health())
        self.assertIsNone(self._nova(None).health())
        # not this computer's Nova (no units here): the file is ignored even if one is lying around
        self.assertEqual([f for f in self._nova(self._monitor(containers="9/9 running"), units={}).status()["facts"]
                          if f[0] == "containers"], [])

    def test_optional_units_only_when_present(self):
        labels = lambda n: [u["label"] for u in n.status()["units"]]
        self.assertNotIn("assistant", labels(self._nova()))
        self.assertIn("assistant", labels(self._nova(units={**self.UNITS, "nova-brain.service": RUNNING})))

    def test_no_privileged_actions(self):
        hub = make_hub(app_classes=())
        n = Nova(hub.ctx, local_hosts=[])
        for a in n.actions():
            self.assertFalse(a["id"].startswith("svc:"))
        with self.assertRaises(BadRequest):
            n.run_action("svc:restart", {})
        with self.assertRaises(BadRequest):
            n.apply_settings({"_note": "x"})


# ------------------------------------------------------------------------------------ custom apps
class CustomTests(unittest.TestCase):
    def test_validate(self):
        spec = validate({"name": "My App", "unit": "myapp", "healthUrl": "http://127.0.0.1:1/"})
        self.assertEqual(spec["id"], "my-app")
        self.assertEqual(spec["unit"], "myapp.service")
        for bad in ({"name": ""}, {"name": "x"}, {"name": "x", "unit": "a b"}, {"name": "x", "openUrl": "file:///etc/passwd"},
                    {"name": "x", "unit": "x", "scope": "root"}):
            with self.assertRaises(BadRequest, msg=bad):
                validate(bad)

    def test_add_edit_remove(self):
        hub = make_hub({"myapp.service": RUNNING}, app_classes=None)
        hub.add_custom({"name": "My App", "unit": "myapp.service"})
        self.assertIn("my-app", hub.apps)
        self.assertEqual(hub.app("my-app").status()["state"], "ok")
        with self.assertRaises(BadRequest):
            hub.add_custom({"name": "My App", "unit": "myapp.service"})
        hub.app("my-app").apply_settings({"name": "Renamed"})
        self.assertEqual(hub.ctx.config.get("custom")[0]["name"], "Renamed")
        res = hub.app("my-app").run_action("remove", {})
        self.assertTrue(res["removed"])
        hub.reload()
        self.assertNotIn("my-app", hub.apps)


# ------------------------------------------------------------------------------------ the hub over HTTP
class FakeApp(App):
    id, name, glyph, color = "fake", "Fake", "FK", "#123456"
    units = (("fake.service", True),)

    def __init__(self, ctx):
        super().__init__(ctx)
        self.value = False

    def status(self):
        return {"state": "ok", "headline": "fine", "facts": [["k", "v"]]}

    def settings(self):
        return [{"title": "fake", "fields": [field("flag", "Flag", "bool", self.value)]}]

    def apply_app_settings(self, patch):
        self.value = patch["flag"]

    def actions(self):
        return [{"id": "ping", "label": "ping"}]

    def run_action(self, action, args):
        return {"ok": True, "message": "pong"}


class Broken(FakeApp):
    id = "broken"

    def status(self):
        raise RuntimeError("boom")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.hub = make_hub(app_classes=(FakeApp, Broken))
        self.httpd, self.port = serve(self.hub, 0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close()))

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"} if body is not None else {}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data.decode()

    def test_pages_and_overview(self):
        code, html = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("orbit", html)
        code, d = self.req("GET", "/api/apps")
        self.assertEqual([a["id"] for a in d["apps"]], ["fake", "broken"])
        self.assertEqual(d["apps"][1]["state"], "unknown")  # a crashing adapter is contained

    def test_settings_and_actions(self):
        code, _ = self.req("POST", "/api/apps/fake/settings", {"flag": True})
        self.assertEqual(code, 200)
        self.assertTrue(self.hub.app("fake").value)
        self.assertEqual(self.req("POST", "/api/apps/fake/settings", {"flag": "yes"})[0], 400)
        self.assertEqual(self.req("POST", "/api/apps/fake/settings", {"other": 1})[0], 400)
        self.assertEqual(self.req("POST", "/api/apps/fake/action", {"action": "ping"})[1]["message"], "pong")
        self.assertEqual(self.req("POST", "/api/apps/fake/action", {"action": "rm -rf"})[0], 400)
        self.assertEqual(self.req("GET", "/api/apps/nope")[0], 404)

    def test_guards(self):
        self.assertEqual(self.req("GET", "/api/apps", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/apps/fake/action", {"action": "ping"},
                                  headers={"Origin": "https://evil.example"})[0], 403)
        own = {"Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.req("POST", "/api/apps/fake/action", {"action": "ping"}, headers=own)[0], 200)
        # a plain HTML form can't send JSON
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/apps/fake/action", body="action=ping",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(c.getresponse().status, 400)
        c.close()

    def test_hub_settings(self):
        code, d = self.req("POST", "/api/hub", {"theme": "amber", "refreshSec": 5})
        self.assertEqual((code, d["settings"]["theme"], d["settings"]["refreshSec"]), (200, "amber", 5))
        self.assertEqual(self.req("POST", "/api/hub", {"theme": "nope"})[0], 400)
        self.assertEqual(self.req("POST", "/api/hub", {"refreshSec": 0})[0], 400)
        self.assertEqual(self.req("POST", "/api/hub", {"order": ["fake"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/hub", {"enabled": {"fake": False}})[0], 200)
        d = self.req("GET", "/api/apps")[1]
        self.assertEqual(d["apps"][0]["headline"], "hidden (orbit settings → apps)")
        # survives a restart
        self.assertEqual(Config(self.hub.ctx.config.path).get("theme"), "amber")


if __name__ == "__main__":
    unittest.main()
