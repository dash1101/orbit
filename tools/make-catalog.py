"""Orbit's app catalog: packaging/catalog.json, which every Orbit (desktop and Android, 0.6.0 and newer) reads from this
repository's default branch (through api.github.com, at most every 12 hours, kept for when GitHub can't be reached).

It carries what can change without a new Orbit: each app's name, description, colour and icon (inlined SVG), its
repository, and on Android its package ids, which release file is the app and its checksum list. So an app that is
renamed, gets a new icon or comes to Android shows up in the Orbit people already have. Signing keys stay built into
Orbit: a catalog can't change how a release is checked.

    python3 tools/make-catalog.py           # writes packaging/catalog.json
    python3 tools/make-catalog.py --check   # exits 1 when it's out of date
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "catalog.json"
ICONS = ROOT / "web"

APPS = [
    {"id": "ytm-starcast", "name": "YTM Starcast",
     "blurb": "YouTube Music companion: a remote for your phone, your Discord status and a better YouTube Music",
     "repo": "dash1101/ytm-starcast", "color": "#38d9c4", "icon": "apps/ytm-starcast.svg",
     "android": {
         # YTM Starcast's phone app keeps the id it had as Pocketfader (so it updates in place)
         "pkgs": ["app.novalabs.pocketfader", "app.pocketfader.client"],
         "apk": ["ytm-starcast-android.apk", "re:ytm-starcast-android-v[^/]*\\.apk"], "sums": "SHA256SUMS.txt",
         "note": "The phone app talks to YTM Starcast on your computer: install that with Orbit for desktop.",
         "extra": "Includes your Discord status (formerly YTM Beacon): its controls are in YTM Starcast's app."}},
    {"id": "nova", "name": "Nova", "blurb": "Secure remote for your home server",
     "repo": "dash1101/Nova", "color": "#5ec4b6", "icon": "apps/nova.svg",
     "android": {
         "pkgs": ["app.novalabs.nova"], "apk": ["nova-universal.apk", "re:.*-universal\\.apk"], "sums": "SHA256SUMS",
         "note": "The Nova app pairs with a Nova server on Linux or Windows: Nova Server on that computer (or sudo nova add) shows the code.",
         "extra": "Comes with five home-screen widgets: status, live stats, quick actions, inbox and storage. In Nova: Menu → Home-screen widgets."}},
    {"id": "ytdl-ui", "name": "YTDL-UI",
     "blurb": "Download video and audio from YouTube and 1,800+ other sites, in your browser (yt-dlp and ffmpeg)",
     "repo": "JustAyden/YTDL-UI", "color": "#22d3ee", "icon": "apps/ytdl-ui.svg"},
    {"id": "xmas-tui", "name": "xmas-tui",
     "blurb": "A cozy, glowing Christmas village as your wallpaper: walk around it on a computer, or set it as a live wallpaper on Android",
     "repo": "dash1101/xmas-tui", "color": "#e0483e", "icon": "apps/xmas-tui.svg",
     "android": {
         # the live wallpaper, from 1.6.0 on (Android 7 or later)
         "pkgs": ["io.github.dash1101.xmastui"], "apk": ["xmas-tui-android.apk"], "sums": "SHA256SUMS",
         "note": "A live wallpaper: open it and tap Set as live wallpaper. Its settings fit the snowy dock to your launcher.",
         "extra": "Needs Android 7 or later."}},
    {"id": "rpcortex-studio", "name": "RPCortex Studio",
     "blurb": "Control an RPCortex device from your computer or phone: files, packages, firmware, logs and a terminal",
     "repo": "dash1101/rpcortex-studio", "color": "#2dd4be", "icon": "apps/rpcortex-studio.svg",
     "android": {
         "pkgs": ["app.novalabs.rpcstudio"], "apk": ["rpcortex-studio-android.apk"], "sums": "SHA256SUMS",
         "note": "Plug the board into the phone's USB-C port (or through an OTG adapter) and Android offers to open RPCortex Studio.",
         "extra": "Needs Android 8 or later and a phone that can host USB devices."}},
    {"id": "orbit", "name": "Orbit", "blurb": "This app",
     "repo": "dash1101/orbit", "color": "#f0668e", "icon": "icon.svg",
     "android": {
         "pkgs": ["app.novalabs.orbit"], "apk": ["orbit-android.apk"], "sums": "SHA256SUMS",
         "note": "Orbit updates itself from its GitHub releases."}},
]


def build():
    apps = []
    for a in APPS:
        a = dict(a)
        a["icon"] = (ICONS / a["icon"]).read_text(encoding="utf-8").strip()
        apps.append(a)
    return json.dumps({"version": 1, "apps": apps}, ensure_ascii=False, indent=1) + "\n"


def main():
    text = build()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("packaging/catalog.json is out of date: run tools/make-catalog.py")
            sys.exit(1)
        return
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text) // 1024} KB)")


if __name__ == "__main__":
    main()
