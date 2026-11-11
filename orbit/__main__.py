"""`python3 -m orbit` — open the Orbit window (or just serve it with --no-window)."""
import argparse
import sys
import threading

from . import __version__
from .runner import Runner
from .server import Hub, serve


def main(argv=None):
    p = argparse.ArgumentParser(prog="orbit", description="One place for all your apps.")
    p.add_argument("--port", type=int, default=8790, help="local port (default 8790; 0 = any free port)")
    p.add_argument("--no-window", action="store_true", help="only run the server; open the printed address yourself")
    p.add_argument("--dry-run", action="store_true",
                   help="read everything, change nothing: actions and settings are recorded instead of applied")
    p.add_argument("--version", action="version", version=f"orbit {__version__}")
    p.add_argument("--write-version", metavar="FILE", help=argparse.SUPPRESS)   # for Orbit's own updater
    a = p.parse_args(argv)
    if a.write_version:
        # The updater starts a downloaded Orbit this way to make sure it runs and is the version it should be.
        # A file, not the terminal: Orbit for Windows has no console to print to.
        with open(a.write_version, "w", encoding="utf-8") as f:
            f.write(f"orbit {__version__}\n")
        return 0

    import os
    restarted = os.environ.pop("ORBIT_RESTARTED", "") == "1"
    if not restarted and _switch_to_waiting_update(a.port):
        return 0
    from .install.recipes.orbit import cleanup_old
    threading.Thread(target=_cleanup_after_update, args=(cleanup_old,), daemon=True).start()

    hub = Hub(runner=Runner(dry_run=a.dry_run))
    try:
        httpd, port = _serve(hub, a.port, wait=15 if restarted else 0)
    except OSError as e:
        if a.port and not a.no_window:
            # Already open? Then just show another window onto it.
            url = f"http://127.0.0.1:{a.port}/"
            from .net import get_json, HttpError
            try:
                if get_json(url + "api/hub", timeout=1).get("name") == "Orbit":
                    from .gui import open_window
                    return open_window(url)
            except HttpError:
                pass
        sys.exit(f"orbit: can't listen on port {a.port}: {e}")
    url = f"http://127.0.0.1:{port}/"
    print(f"Orbit {__version__} on {url}" + ("  (dry run: nothing will be changed)" if a.dry_run else ""), flush=True)
    if a.no_window:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from .gui import open_window
    return open_window(url, hub=hub)


def _switch_to_waiting_update(port):
    """An update got ready but Orbit was closed instead of restarted: switch to it now, before anything else, and
    start it. Not while another Orbit runs from this program (it still reads its own file)."""
    import os
    import subprocess
    import sys
    from .install.recipes.orbit import running_program, switch, waiting
    exe = running_program()
    if not exe or not waiting(exe):
        return False
    from .net import port_open
    if port and port_open("127.0.0.1", port):
        return False
    try:
        if not switch(exe):
            return False
    except OSError:
        return False
    if os.name == "nt":
        subprocess.Popen([str(exe)] + sys.argv[1:], creationflags=0x00000008, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    os.execv(str(exe), [str(exe)] + sys.argv[1:])


def _serve(hub, port, wait=0):
    """Listen on the port; after a restart, wait (up to `wait` seconds) for the Orbit before to let go of it."""
    import time
    end = time.time() + wait
    while True:
        try:
            return serve(hub, port)
        except OSError:
            if time.time() >= end:
                raise
            time.sleep(0.3)


def _cleanup_after_update(cleanup):
    """The program an update replaced is deleted once it has ended (on Windows that can take a moment)."""
    import time
    for _ in range(20):
        if cleanup():
            return
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
