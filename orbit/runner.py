"""Running commands, with a dry-run mode that records changes instead of making them.

Read-only commands (`systemctl show`, `journalctl`) always run. Anything that changes the system
(`systemctl restart`, an updater, opening a window) goes through `write`/`spawn`, which only record
what they would have done when dry-run is on.
"""
import shutil
import subprocess
import sys
import threading
import time


class Runner:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.recorded = []  # [(time, kind, argv)] of everything dry-run skipped
        self._lock = threading.Lock()

    def _record(self, kind, argv):
        with self._lock:
            self.recorded.append((time.time(), kind, list(argv)))
            del self.recorded[:-200]

    @staticmethod
    def available(name):
        return shutil.which(name) is not None

    def read(self, argv, timeout=5):
        """Run a command that only looks at things. Returns (exit code, combined output)."""
        try:
            from .proc import hidden
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                               **hidden())
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except FileNotFoundError:
            return 127, f"{argv[0]}: not installed"
        except subprocess.TimeoutExpired:
            return 124, f"{argv[0]}: took longer than {timeout} s"

    def write(self, argv, timeout=120):
        """Run a command that changes something. Dry-run records it and reports success."""
        if self.dry_run:
            self._record("run", argv)
            return 0, "dry run: would run " + " ".join(argv)
        return self.read(argv, timeout=timeout)

    def spawn(self, argv):
        """Start a program that changes something and leave it running. Dry-run records it instead."""
        if self.dry_run:
            self._record("spawn", argv)
            return True
        return self.launch(argv)

    def open_url(self, url):
        """Open a web address in the default browser. On Windows through the shell (ShellExecute), the way
        Windows itself opens links; elsewhere with `open` / `xdg-open`."""
        if sys.platform == "win32":
            try:
                import os
                os.startfile(url)
                return True
            except OSError:
                return False
        return self.launch(open_argv(url))

    def open_file(self, path):
        """Open a file the way double-clicking it would (a Start-menu shortcut, a folder). Windows and macOS/Linux."""
        if sys.platform == "win32":
            try:
                import os
                os.startfile(path)
                return True
            except OSError:
                return False
        return self.launch(open_argv(path))

    def launch(self, argv):
        """Open something for the person to look at (a browser, a window, a terminal). Changes nothing,
        so it happens in dry-run too."""
        try:
            kw = {"creationflags": 0x00000008} if sys.platform == "win32" else {"start_new_session": True}
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **kw)
            return True
        except OSError:
            return False


def terminal_argv(command, title="Orbit"):
    """argv that opens a terminal window running `command` (a list) and keeps it open afterwards."""
    if sys.platform == "win32":
        line = subprocess.list2cmdline(command)
        return ["cmd", "/c", "start", title, "cmd", "/k", line]
    if sys.platform == "darwin":
        shell = " ".join(_quote(c) for c in command)
        script = shell.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                "-e", 'tell application "Terminal" to activate']
    shell = " ".join(_quote(c) for c in command) + '; echo; read -r -p "Press Enter to close" _'
    if shutil.which("konsole"):
        return ["konsole", "-p", f"tabtitle={title}", "-e", "bash", "-c", shell]
    if shutil.which("gnome-terminal"):
        return ["gnome-terminal", "--title", title, "--", "bash", "-c", shell]
    if shutil.which("x-terminal-emulator"):
        return ["x-terminal-emulator", "-e", "bash", "-c", shell]
    if shutil.which("xterm"):
        return ["xterm", "-T", title, "-e", "bash", "-c", shell]
    return None


def open_argv(url):
    """argv that opens a web address in the default browser."""
    if sys.platform == "win32":
        return ["rundll32", "url.dll,FileProtocolHandler", url]
    if sys.platform == "darwin":
        return ["open", url]
    return ["xdg-open", url]


def _quote(s):
    import shlex
    return shlex.quote(s)
