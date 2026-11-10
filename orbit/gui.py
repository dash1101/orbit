"""The desktop window: GTK 3 + WebKit2 on Linux; otherwise a browser in app mode, or a plain browser tab.

Links that leave Orbit (release pages and so on) open in the normal browser, not in the window.
"""
import shutil
import subprocess
import webbrowser


def open_window(url, title="Orbit"):
    try:
        return _gtk(url, title)
    except (ImportError, ValueError):
        pass
    for exe in _app_browsers():
        if exe:
            p = subprocess.Popen([exe, f"--app={url}", "--class=Orbit", "--window-size=1180,760"])
            try:
                p.wait()  # most chromium builds hand off to a running browser and return at once
            except KeyboardInterrupt:
                pass
            return _wait_forever()
    webbrowser.open(url)
    return _wait_forever()


def _app_browsers():
    """Chromium-family browsers that can show Orbit as an app window (no tabs or address bar)."""
    import os
    import sys
    from pathlib import Path
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        rel = ["Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe",
               "BraveSoftware/Brave-Browser/Application/brave.exe"]
        return [str(Path(r, x)) for x in rel for r in roots if r and Path(r, x).exists()]
    if sys.platform == "darwin":
        apps = ["Google Chrome", "Brave Browser", "Microsoft Edge", "Chromium"]
        return [p for p in (f"/Applications/{a}.app/Contents/MacOS/{a}" for a in apps) if Path(p).exists()]
    return [shutil.which(x) for x in ("brave-browser", "chromium", "google-chrome", "microsoft-edge")]


def _wait_forever():
    import time
    print("Orbit is running. Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def _gtk(url, title):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2

    win = Gtk.Window(title=title)
    win.set_default_size(1180, 760)
    win.set_wmclass("orbit", "Orbit")
    try:
        win.set_icon_name("utilities-system-monitor")
    except Exception:
        pass
    view = WebKit2.WebView()
    s = view.get_settings()
    s.set_enable_developer_extras(False)
    s.set_enable_write_console_messages_to_stdout(False)

    def leave(_view, decision, kind):
        # Anything outside Orbit's own address opens in the user's browser instead.
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            target = decision.get_navigation_action().get_request().get_uri() or ""
            if not target.startswith(url):
                from .runner import open_argv
                subprocess.Popen(open_argv(target), start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                decision.ignore()
                return True
        return False

    view.connect("decide-policy", leave)
    view.load_uri(url)
    win.add(view)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    return 0
