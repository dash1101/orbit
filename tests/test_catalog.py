"""Orbit's catalog (packaging/catalog.json), which every Orbit reads from this repository: it's up to date with
tools/make-catalog.py and the icons, and names the apps Orbit has built in."""
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_catalog", ROOT / "tools" / "make-catalog.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


class CatalogTests(unittest.TestCase):
    def test_the_published_catalog_is_up_to_date(self):
        self.assertEqual((ROOT / "packaging" / "catalog.json").read_text(encoding="utf-8"), mc.build(),
                         "packaging/catalog.json is out of date: run tools/make-catalog.py")

    def test_it_names_the_apps_orbit_has_built_in(self):
        apps = {a["id"]: a for a in json.loads(mc.build())["apps"]}
        kt = (ROOT / "android/app/src/main/java/app/novalabs/orbit/Catalog.kt").read_text()
        built_in = dict(re.findall(r'id = "([\w-]+)", name = "([^"]+)"', kt))
        self.assertEqual(set(built_in), set(apps))
        for app_id, name in built_in.items():
            self.assertEqual(apps[app_id]["name"], name)
            self.assertTrue(apps[app_id]["icon"].startswith("<svg"))
            self.assertRegex(apps[app_id]["color"], r"^#[0-9a-f]{6}$")


if __name__ == "__main__":
    unittest.main()
