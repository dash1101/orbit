"""Themes: the Android app's copies (Look.kt) have the web page's colours, and text stays readable in every theme."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "orbit" / "static" / "index.html").read_text(encoding="utf-8")
LOOK = (ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "novalabs" / "orbit" / "Look.kt").read_text(encoding="utf-8")
ORDER = ["bg", "raise", "line", "line-hi", "text", "muted", "legend", "accent", "accent-ink", "ok", "warn", "down", "off", "sel"]


def full(colour):
    """#fff -> #ffffff, lower case."""
    c = colour.lower()
    return "#" + "".join(ch * 2 for ch in c[1:]) if len(c) == 4 else c


def web_themes():
    out = {}
    for ids, body in re.findall(r"^([^{\n]*\[data-theme=[^{]*)\{([^}]*)\}", PAGE, re.M):
        tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})", body))
        for tid in re.findall(r'data-theme="([\w-]+)"', ids):
            out[tid] = {k: full(v) for k, v in tokens.items()}
    return out


def android_themes():
    out = {}
    for tid, args in re.findall(r'Triple\("([\w-]+)", "[^"]*", Tokens\(((?:[^()]|\([^()]*\))*)\)\)', LOOK):
        colours = ["#" + x.lower() for x in re.findall(r"c\(0x([0-9a-fA-F]{6})\)", args)]
        out[tid] = dict(zip(ORDER, colours))
    return out


def contrast(a, b):
    def lum(h):
        r, g, b_ = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    x, y = sorted([lum(a), lum(b)], reverse=True)
    return (x + 0.05) / (y + 0.05)


class ThemeTests(unittest.TestCase):
    def test_android_has_the_web_colours(self):
        web, android = web_themes(), android_themes()
        self.assertEqual(sorted(android), sorted(web))
        for tid, tokens in android.items():
            for k, v in tokens.items():
                self.assertEqual(v, web[tid][k], f"theme {tid}: --{k} is {web[tid][k]} on the web page, {v} on Android")

    def test_text_is_readable_in_every_theme(self):
        for tid, t in web_themes().items():
            for k in ["text", "muted", "legend", "accent", "ok", "warn", "down"]:
                self.assertGreaterEqual(contrast(t[k], t["bg"]), 4.5, f"theme {tid}: --{k} on its background")
            self.assertGreaterEqual(contrast(t["accent-ink"], t["accent"]), 4.5, f"theme {tid}: text on the accent")


if __name__ == "__main__":
    unittest.main()
