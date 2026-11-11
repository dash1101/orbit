"""Terminals inside Orbit: an app's command (`sudo nova status`, `sudo nova doctor`) runs here, in a real
pseudo-terminal, and the window shows its output and sends what you type — sudo's password prompt and
interactive questions work as they would in a terminal window.

Only commands that an app adapter defines can be started (the web page picks an action, never a
command line). On Windows, which has no pseudo-terminals, commands run with plain pipes.
"""
import os
import signal
import subprocess
import sys
import threading
import time
import uuid

MAX_BUFFER = 1 << 20      # keep the last 1 MB of output per terminal
MAX_SESSIONS = 12


class Session:
    def __init__(self, argv, title, cols=100, rows=30, env=None, cleanup=(), mask=(), shown=None):
        self.id = uuid.uuid4().hex[:12]
        self.argv, self.title = list(argv), title
        self.started, self.ended = time.time(), None
        self.code = None
        self._buf = bytearray()
        self._base = 0            # bytes dropped from the front of _buf
        self._lock = threading.Lock()
        self._cleanup = list(cleanup)
        self.shown = shown or " ".join(_mask(a, mask) for a in self.argv)
        env = dict(os.environ if env is None else env)
        env.update(TERM="xterm-256color", COLUMNS=str(cols), LINES=str(rows), PYTHONUNBUFFERED="1")
        env.pop("NO_COLOR", None)
        if sys.platform == "win32":
            self._fd = None
            self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, env=env, creationflags=0x08000000)
            reader = self._read_pipe
        else:
            import pty
            master, slave = pty.openpty()
            _winsize(slave, rows, cols)
            try:
                self.proc = subprocess.Popen(self.argv, stdin=slave, stdout=slave, stderr=slave, env=env,
                                             preexec_fn=_take_terminal, close_fds=True)
            finally:
                os.close(slave)
            self._fd = master
            reader = self._read_pty
        self._append(f"\x1b[2m$ {self.shown}\x1b[0m\r\n".encode())
        threading.Thread(target=reader, daemon=True, name=f"orbit-term-{self.id}").start()

    # ---- output
    def _append(self, data):
        with self._lock:
            self._buf += data
            if len(self._buf) > MAX_BUFFER:
                cut = len(self._buf) - MAX_BUFFER
                del self._buf[:cut]
                self._base += cut

    def _read_pty(self):
        while True:
            try:
                data = os.read(self._fd, 65536)
            except OSError:          # EIO: the program ended and closed the terminal
                break
            if not data:
                break
            self._append(data)
        self._finish()

    def _read_pipe(self):
        for chunk in iter(lambda: self.proc.stdout.read1(65536), b""):
            self._append(chunk)
        self._finish()

    def _finish(self):
        self.code = self.proc.wait()
        self.ended = time.time()
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        word = "finished" if self.code == 0 else f"stopped with code {self.code}"
        self._append(f"\r\n\x1b[2m── {word} ──\x1b[0m\r\n".encode())
        for path in self._cleanup:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ---- input
    def write(self, text):
        if self.ended:
            return False
        data = text.encode("utf-8")
        try:
            if self._fd is not None:
                os.write(self._fd, data)
            else:
                self.proc.stdin.write(data.replace(b"\r", b"\r\n") if os.name == "nt" else data)
                self.proc.stdin.flush()
            return True
        except OSError:
            return False

    def interrupt(self):
        """Ctrl+C: the same signal a terminal sends."""
        if self.ended:
            return
        if self._fd is not None:
            try:
                os.write(self._fd, b"\x03")
                return
            except OSError:
                pass
        try:
            self.proc.send_signal(signal.CTRL_C_EVENT if os.name == "nt" else signal.SIGINT)
        except (OSError, ValueError, AttributeError):
            pass

    def stop(self):
        if self.ended:
            return
        try:
            if os.name != "nt":
                os.killpg(self.proc.pid, signal.SIGHUP)   # like closing a terminal window
            else:
                self.proc.terminate()
        except OSError:
            pass

    def resize(self, cols, rows):
        if self._fd is not None and not self.ended:
            _winsize(self._fd, rows, cols)

    def snapshot(self, offset=0):
        with self._lock:
            start = max(0, offset - self._base)
            data = bytes(self._buf[start:])
            end = self._base + len(self._buf)
        return {"id": self.id, "title": self.title, "command": self.shown, "running": self.ended is None,
                "code": self.code, "started": self.started, "ended": self.ended,
                "offset": end, "reset": offset < self._base,
                "data": data.decode("utf-8", "replace")}


class Terminals:
    def __init__(self):
        self._s = {}
        self._lock = threading.Lock()

    def start(self, argv, title, **kw):
        with self._lock:
            done = sorted((s for s in self._s.values() if s.ended), key=lambda s: s.started)
            while len(self._s) >= MAX_SESSIONS and done:
                self._s.pop(done.pop(0).id, None)
            if len(self._s) >= MAX_SESSIONS:
                raise RuntimeError("too many terminals open; close one first")
        s = Session(argv, title, **kw)
        with self._lock:
            self._s[s.id] = s
        return s

    def get(self, sid):
        with self._lock:
            s = self._s.get(sid)
        if not s:
            raise KeyError(sid)
        return s

    def close(self, sid):
        s = self.get(sid)
        s.stop()
        with self._lock:
            self._s.pop(sid, None)

    def list(self):
        with self._lock:
            return [{"id": s.id, "title": s.title, "running": s.ended is None, "started": s.started}
                    for s in sorted(self._s.values(), key=lambda s: s.started)]

    def stop_all(self):
        for s in list(self._s.values()):
            s.stop()


def _take_terminal():
    """In the child: become a session leader with the pseudo-terminal as its controlling terminal,
    so sudo can ask for the password on it and Ctrl+C reaches the whole program."""
    import fcntl
    import termios
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _winsize(fd, rows, cols):
    try:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except (OSError, ImportError):
        pass


def _mask(arg, secrets):
    for s in secrets:
        if s and s in arg:
            arg = arg.replace(s, "••••••")
    return arg
