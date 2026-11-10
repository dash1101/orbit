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
    a = p.parse_args(argv)

    hub = Hub(runner=Runner(dry_run=a.dry_run))
    try:
        httpd, port = serve(hub, a.port)
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


if __name__ == "__main__":
    sys.exit(main())
