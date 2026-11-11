"""App icons: the desktop and Android copies must match, and every icon something points at must exist."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sync_icons", ROOT / "tools" / "sync-icons.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class IconTests(unittest.TestCase):
    def test_android_copies_match_desktop(self):
        for name, (_repo, _path, android) in sync.ICONS.items():
            self.assertTrue((sync.DESKTOP / name).is_file(), name)
            if android:
                self.assertEqual((sync.ANDROID / name).read_bytes(), (sync.DESKTOP / name).read_bytes(),
                                 f"{name} differs: run tools/sync-icons.py")
            else:
                self.assertFalse((sync.ANDROID / name).exists(), f"{name} isn't used on Android")

    def test_every_listed_icon_exists(self):
        kt = (ROOT / "android/app/src/main/java/app/novalabs/orbit/Catalog.kt").read_text()
        for icon in re.findall(r'icon = "([^"]+)"', kt):
            self.assertTrue((sync.ANDROID / icon).is_file(), f"Android catalogue names {icon}")
        from orbit.apps import BUILTIN
        for app in BUILTIN:
            if app.icon:
                self.assertTrue((sync.DESKTOP / app.icon).is_file(), f"{app.id} names {app.icon}")

    def test_android_catalogue_keeps_the_phone_apps_ids(self):
        kt = (ROOT / "android/app/src/main/java/app/novalabs/orbit/Catalog.kt").read_text()
        # YTM Starcast's phone app kept Pocketfader's package ids (it updates in place); alpha.13 used the second one
        self.assertIn("app.novalabs.pocketfader", kt)
        self.assertIn("app.pocketfader.client", kt)
        self.assertIn('repo = "dash1101/ytm-starcast"', kt)
        self.assertNotIn("dash1101/pocketfader", kt)
        self.assertNotIn('id = "pocketfader"', kt)
        self.assertNotIn('id = "ytm-beacon"', kt)           # part of YTM Starcast
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text()
        for pkg in ("app.novalabs.pocketfader", "app.pocketfader.client", "app.novalabs.nova"):
            self.assertIn(pkg, manifest)

    def test_no_stale_icons(self):
        """Every icon file Orbit ships belongs to an app it lists (Orbit's own icon aside)."""
        for folder in (sync.DESKTOP, sync.ANDROID):
            for f in folder.iterdir():
                if f.name != "orbit.svg":
                    self.assertIn(f.name, sync.ICONS, f"{f.relative_to(ROOT)} isn't one of tools/sync-icons.py's icons")


if __name__ == "__main__":
    unittest.main()
