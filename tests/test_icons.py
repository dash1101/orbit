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

    def test_android_catalogue_knows_both_pocketfader_ids(self):
        kt = (ROOT / "android/app/src/main/java/app/novalabs/orbit/Catalog.kt").read_text()
        self.assertIn("app.novalabs.pocketfader", kt)
        self.assertIn("app.pocketfader.client", kt)       # the released alpha.13 still uses this one
        self.assertNotIn('id = "ytm-beacon"', kt)           # built into Pocketfader on Android
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text()
        for pkg in ("app.novalabs.pocketfader", "app.pocketfader.client", "app.novalabs.nova"):
            self.assertIn(pkg, manifest)


if __name__ == "__main__":
    unittest.main()
