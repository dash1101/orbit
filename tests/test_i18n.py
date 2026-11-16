"""Orbit's languages: every language has a translation for every text Orbit shows, with the same {} parts, and the
Android app carries the same files (tools/i18n.py)."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import i18n  # noqa: E402


class Languages(unittest.TestCase):
    def test_every_language_is_complete(self):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "i18n.py"), "check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_finding_text(self):
        self.assertTrue(i18n.is_text("Install {}, then try again."))
        self.assertTrue(i18n.is_text("{} (its installer uses it)"))
        self.assertTrue(i18n.is_text("kitty 0.36 or newer (the wallpaper runs in its panel)"))
        self.assertTrue(i18n.is_text("Hide after (minutes, 0 = never)"))
        self.assertFalse(i18n.is_text("systemctl --user restart nova"))
        self.assertFalse(i18n.is_text("grid g2".replace(" ", "-")))
        self.assertFalse(i18n.is_text("install"))           # one word: only from the page or the word list
        self.assertTrue(i18n.is_text("install", words=True))
        self.assertFalse(i18n.is_text("Deutsch"))
        self.assertFalse(i18n.is_text("a=b c"))

    def test_glued_js_text_becomes_a_pattern(self):
        got = [t for t, *_ in i18n.joined('h("x", {}, "── what " + W.meta.name + " needs"); f("copy " + a.url, 1)', "js")]
        self.assertIn("── what \x00 needs", got)
        self.assertIn("copy \x00", got)

    def test_rust_format_strings(self):
        self.assertEqual(i18n.rust_template("Couldn't start {name}: {e:?}"), "Couldn't start \x00: \x00")
        self.assertIsNone(i18n.rust_template("{{json}}"))


if __name__ == "__main__":
    unittest.main()
