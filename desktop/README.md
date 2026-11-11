# Orbit (Rust)

The desktop Orbit in Rust: one small program that serves Orbit's page and API on `127.0.0.1` and shows it in a window
of its own, drawn by the web engine that comes with the system (WebView2 on Windows, WebKit on macOS, WebKitGTK on
Linux) through [Tauri](https://tauri.app). The page is `../orbit/static/index.html`, built into the program, so the look
and every animation are the same as before.

If the web engine is missing, Orbit offers to install it: Microsoft's WebView2 installer on Windows (checked to be
signed by Microsoft before it runs), the system's WebKitGTK package on Linux (the system asks for the password).
Saying no opens Orbit in the browser instead.

## Build

```bash
cargo test                              # unit tests
cargo build --release --features window # the program with its window (Windows, macOS; Linux needs libwebkit2gtk-4.1-dev)
cargo build                             # without the window: the server only (orbit --no-window), or the browser
```

On Linux the released program is two builds in one: the window build is made first and carried inside the program
(`ORBIT_WINDOW_BIN=path/to/window-build cargo build --release`). The program itself doesn't need WebKitGTK to start, so
it can offer to install it, and it unpacks the window build into `~/.cache/orbit` the first time it opens a window.

`ORBIT_VERSION=99.0.0` at build time sets the version (Orbit's own update check builds a newer stand-in that way).

## Checks on real machines

`.github/workflows/rust.yml` builds every system and runs the same checks as the Python Orbit against these programs:
`tools/ci-selfupdate.py` (a real update to a newer build, then a restart, on every system) and
`tools/ci-nova-windows.py --exe` (installs, checks and removes Nova through the program on Windows). It also takes a
screenshot of the window on each system.
