"""Orbit updating itself: the new program must match the release's checksums and start as the right version before
it takes the running one's place; anything else leaves Orbit as it was."""
import shutil
import tempfile
import unittest
from pathlib import Path

from orbit.install.engine import Installer, Sources
from orbit.install.host import Host
from orbit.install.recipes.orbit import OrbitRecipe, cleanup_old, switch, waiting
from tests import unix_only
from tests.test_install import StubGitHub, sums, wait


def program(version):
    """A stand-in for the standalone Orbit: answers --write-version like the real one."""
    return ('#!/bin/sh\nif [ "$1" = "--write-version" ]; then echo "orbit ' + version + '" > "$2"; exit 0; fi\nexit 3\n').encode()


@unix_only
class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.gh = StubGitHub()
        self.addCleanup(self.gh.close)
        self.tmp = Path(tempfile.mkdtemp(prefix="orbit-self-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sources = Sources(api=self.gh.base, allow=(self.gh.base,), token=lambda: "")
        self.exe = self.tmp / "apps" / "orbit"
        self.exe.parent.mkdir()
        self.exe.write_bytes(program("0.3.0"))
        self.exe.chmod(0o755)

    def recipe(self, system="linux"):
        host = Host(system=system, arch="amd64", home=self.tmp, env={}, which={})
        r = OrbitRecipe(host, self.sources)
        r.program, r.version = str(self.exe), "0.3.0"
        return r

    def publish(self, body, listed=None):
        name = "orbit-v9.9.9-linux-x86_64"
        files = {name: body}
        self.gh.publish("dash1101/orbit", "v9.9.9", dict(files, SHA256SUMS=sums(listed or files)))

    def run_update(self, r):
        return wait(Installer(r.host, self.sources, {"orbit": r}, pace=0).start("orbit", "install", {}))

    def test_gets_ready_then_switches_at_restart(self):
        self.publish(program("9.9.9"))
        r = self.recipe()
        info = r.info("install")
        self.assertFalse(info["blocked"], info)
        self.assertEqual(info["title"], "Update Orbit to 9.9.9")
        snap = self.run_update(r)
        self.assertEqual(snap["state"], "done", snap["error"] + "\n".join(snap["log"]))
        # the running program's file is left alone until the restart (it still reads parts of itself from it)
        self.assertEqual(self.exe.read_bytes(), program("0.3.0"))
        self.assertEqual((self.exe.parent / "orbit.new").read_bytes(), program("9.9.9"))
        self.assertEqual(waiting(self.exe), "9.9.9")
        self.assertEqual(r.detect()["pending"], "9.9.9")
        self.assertEqual(snap["result"]["next"][0]["kind"], "restart")
        self.assertTrue(switch(self.exe))                # what the restart does
        self.assertEqual(self.exe.read_bytes(), program("9.9.9"))
        self.assertEqual((self.exe.parent / "orbit.old").read_bytes(), program("0.3.0"))
        self.assertEqual(waiting(self.exe), "")
        self.assertFalse(switch(self.exe))               # nothing waiting any more
        self.assertTrue(cleanup_old(self.exe))           # what the new Orbit does when it starts
        self.assertEqual(sorted(p.name for p in self.exe.parent.iterdir()), ["orbit"])

    def test_a_half_finished_download_is_never_switched_to(self):
        (self.exe.parent / "orbit.new").write_bytes(b"partial")       # no .new.ok: it was never checked
        self.assertEqual(waiting(self.exe), "")
        self.assertFalse(switch(self.exe))
        self.assertEqual(self.exe.read_bytes(), program("0.3.0"))

    def test_a_program_that_says_another_version_never_replaces_this_one(self):
        self.publish(program("6.6.6"))
        snap = self.run_update(self.recipe())
        self.assertEqual(snap["state"], "failed")
        self.assertIn("didn't start as Orbit 9.9.9", snap["error"])
        self.assertEqual(self.exe.read_bytes(), program("0.3.0"))
        self.assertEqual(sorted(p.name for p in self.exe.parent.iterdir()), ["orbit"])

    def test_a_program_that_doesnt_start_never_replaces_this_one(self):
        self.publish(b"\x7fELF not really a program")
        snap = self.run_update(self.recipe())
        self.assertEqual(snap["state"], "failed")
        self.assertEqual(self.exe.read_bytes(), program("0.3.0"))
        self.assertEqual(sorted(p.name for p in self.exe.parent.iterdir()), ["orbit"])

    def test_a_tampered_download_is_never_run(self):
        self.publish(b"#!/bin/sh\ntouch " + str(self.tmp / "RAN").encode() + b"\n",
                     listed={"orbit-v9.9.9-linux-x86_64": program("9.9.9")})
        snap = self.run_update(self.recipe())
        self.assertEqual(snap["state"], "failed")
        self.assertIn("doesn't match its published checksum", snap["error"])
        self.assertFalse((self.tmp / "RAN").exists())
        self.assertEqual(self.exe.read_bytes(), program("0.3.0"))

    def test_says_how_to_update_where_it_cant_itself(self):
        self.publish(program("9.9.9"))
        mac = self.recipe("darwin")
        self.assertIn(".dmg", mac.info("install")["requirements"][0]["hint"])
        self.assertTrue(mac.info("install")["blocked"])
        src = self.recipe()
        src.program = None                     # running from the source folder (tests are never frozen)
        self.assertIn("git pull", src.info("install")["requirements"][0]["hint"])
        self.assertTrue(src.info("install")["blocked"])
        self.assertIn("doesn't remove itself", src.info("uninstall")["blocked"] or src.can_uninstall()[1])

    def test_dry_run_changes_nothing(self):
        self.publish(program("9.9.9"))
        r = self.recipe()
        snap = wait(Installer(r.host, self.sources, {"orbit": r}, dry_run=True, pace=0).start("orbit", "install", {}))
        self.assertEqual(snap["state"], "done", snap["error"])
        self.assertEqual(self.exe.read_bytes(), program("0.3.0"))
        self.assertEqual(sorted(p.name for p in self.exe.parent.iterdir()), ["orbit"])


    def test_the_window_offers_the_restart_once_it_is_ready(self):
        from unittest import mock
        from tests.test_orbit import make_hub
        hub = make_hub(app_classes=[], tmp=str(self.tmp))
        with mock.patch("orbit.install.recipes.orbit.running_program", lambda: self.exe):
            (self.exe.parent / "orbit.new").write_bytes(program("9.9.9"))
            (self.exe.parent / "orbit.new.ok").write_text("orbit 9.9.9\n")
            self.assertEqual(hub.orbit_update(), {"version": "9.9.9", "url": "https://github.com/dash1101/orbit/releases",
                                                  "here": True, "pending": True})
            mine = next(a for a in hub.install_list()["apps"] if a["id"] == "orbit")
        self.assertTrue(mine["self"])
        self.assertEqual(mine["pending"], "9.9.9")
        self.assertFalse(mine["update"])


class ChildProgramTests(unittest.TestCase):
    def test_programs_a_standalone_orbit_starts_are_their_own(self):
        import os
        import sys
        from unittest import mock
        from orbit.__main__ import _independent_children
        env = {"PATH": "/usr/bin", "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEIabc", "_PYI_PARENT_PROCESS_LEVEL": "1",
               "_MEIPASS2": "/tmp/_MEIabc", "LD_LIBRARY_PATH": "/tmp/_MEIabc", "LD_LIBRARY_PATH_ORIG": "/opt/lib",
               "DYLD_LIBRARY_PATH": "/tmp/_MEIabc/lib"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "_MEIPASS", "/tmp/_MEIabc", create=True):
            _independent_children()
            got = dict(os.environ)
        self.assertEqual(got, {"PATH": "/usr/bin", "PYINSTALLER_RESET_ENVIRONMENT": "1", "LD_LIBRARY_PATH": "/opt/lib"})

if __name__ == "__main__":
    unittest.main()
