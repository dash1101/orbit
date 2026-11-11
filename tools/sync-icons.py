#!/usr/bin/env python3
"""Pull each app's current icon from its own GitHub repository into Orbit (desktop and Android).

    python3 tools/sync-icons.py           update the icons that changed
    python3 tools/sync-icons.py --check   only report (exit 1 when an icon is out of date)

Uses the GitHub command line (`gh`), signed in, so private repositories work. Run it before a release.
"""
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "orbit" / "static" / "apps"
ANDROID = ROOT / "android" / "app" / "src" / "main" / "assets" / "icons"

# file name in Orbit -> (repository, path of the icon in it, also used by the Android app?)
# Every file in Orbit's icon folders is one of these (tests/test_icons.py checks that nothing stale is left).
ICONS = {
    "ytm-starcast.svg": ("dash1101/ytm-starcast", "starcast/static/icon.svg", True),
    "nova.svg": ("dash1101/Nova", "server/web/icon.svg", True),
}


def fetch(repo, path):
    p = subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"couldn't read {repo}/{path}: {p.stderr.strip() or 'is gh signed in?'}")
    return base64.b64decode(json.loads(p.stdout)["content"])


def main():
    check = "--check" in sys.argv
    stale = 0
    for name, (repo, path, android) in ICONS.items():
        data = fetch(repo, path)
        targets = [DESKTOP / name] + ([ANDROID / name] if android else [])
        for t in targets:
            if not t.exists() or t.read_bytes() != data:
                stale += 1
                print(("out of date: " if check else "updated: ") + str(t.relative_to(ROOT)))
                if not check:
                    t.parent.mkdir(parents=True, exist_ok=True)
                    t.write_bytes(data)
    print("all icons are current" if not stale else (f"{stale} out of date" if check else f"{stale} updated"))
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
