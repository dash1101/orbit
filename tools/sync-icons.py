#!/usr/bin/env python3
"""Pull each app's current icon from its own GitHub repository into Orbit (desktop and Android).

    python3 tools/sync-icons.py           update the icons that changed
    python3 tools/sync-icons.py --check   only report (exit 1 when an icon is out of date)

Uses the GitHub command line (`gh`), signed in, so private repositories work. Run it before a release.
"""
import base64
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "web" / "apps"
ANDROID = ROOT / "android" / "app" / "src" / "main" / "assets" / "icons"

# file name in Orbit -> (repository, where its icon is, newest place first, also used by the Android app?).
# The first of the paths that exists on the repository's default branch is used, so an app that moves or redraws its
# icon still comes through; an SVG is kept as it is, a PNG is wrapped in an SVG (so every icon in Orbit is one file
# type, inlined in the catalog). Every file in Orbit's icon folders is one of these (tests/test_icons.py checks that).
ICONS = {
    "ytm-starcast.svg": ("dash1101/ytm-starcast", ["starcast/static/icon.svg"], True),
    "nova.svg": ("dash1101/Nova", ["server/web/icon.svg"], True),
    "ytdl-ui.svg": ("JustAyden/YTDL-UI", ["static/img/icon.svg", "static/img/icon-192.png"], False),
    "xmas-tui.svg": ("dash1101/xmas-tui", ["web/site/icon.svg", "web/site/icon-192.png"], False),
    "rpcortex-studio.svg": ("dash1101/rpcortex-studio", ["assets/icon.svg"], False),
}
# The Android app's catalog takes icons up to 64 KB (Catalog.kt): a PNG must stay well under that once wrapped.
MAX_ICON = 60_000


class Missing(Exception):
    pass


def fetch(repo, path):
    p = subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}"], capture_output=True, text=True)
    if p.returncode != 0:
        if "Not Found" in p.stdout + p.stderr:
            raise Missing(path)
        raise SystemExit(f"couldn't read {repo}/{path}: {p.stderr.strip() or 'is gh signed in?'}")
    return base64.b64decode(json.loads(p.stdout)["content"])


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SystemExit("not a PNG")
    return struct.unpack(">II", data[16:24])


def as_svg(path, data):
    """An SVG icon as it is; a PNG inside an SVG of the same size (xlink:href: what every renderer here reads, Android's too)."""
    if path.endswith(".svg"):
        return data
    w, h = png_size(data)
    b64 = base64.b64encode(data).decode()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {w} {h}">'
            f'<image width="{w}" height="{h}" xlink:href="data:image/png;base64,{b64}"/>'
            f'</svg>\n').encode()


def icon(repo, paths):
    """(the path used, the icon as SVG) from the first path that exists."""
    for path in paths:
        try:
            data = as_svg(path, fetch(repo, path))
        except Missing:
            continue
        if len(data) > MAX_ICON:
            raise SystemExit(f"{repo}/{path} is too big for an icon ({len(data) // 1024} KB as SVG)")
        return path, data
    raise SystemExit(f"{repo} has none of its icon paths any more: {', '.join(paths)}")


def main():
    check = "--check" in sys.argv
    stale = 0
    for name, (repo, paths, android) in ICONS.items():
        used, data = icon(repo, paths)
        targets = [DESKTOP / name] + ([ANDROID / name] if android else [])
        for t in targets:
            if not t.exists() or t.read_bytes() != data:
                stale += 1
                print(("out of date: " if check else "updated: ") + f"{t.relative_to(ROOT)} (from {repo}/{used})")
                if not check:
                    t.parent.mkdir(parents=True, exist_ok=True)
                    t.write_bytes(data)
    print("all icons are current" if not stale else (f"{stale} out of date" if check else f"{stale} updated"))
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
