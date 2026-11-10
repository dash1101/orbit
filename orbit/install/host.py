"""What computer Orbit is running on: system, CPU, Linux flavour, and where per-user programs live.

Everything an install recipe needs to know about the machine goes through a `Host`, so tests can
pretend to be Windows or a Mac (`Host(system="windows", home=tmp, which={...})`).
"""
import os
import platform
import shutil
import sys
from pathlib import Path

SYSTEMS = {"linux": "Linux", "darwin": "macOS", "windows": "Windows"}
ARCHES = {"x86_64": "amd64", "amd64": "amd64", "x64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def _system():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


class Host:
    def __init__(self, system=None, arch=None, home=None, env=None, which=None, systemd=None, distro=None,
                 probe=None):
        self.system = system or _system()
        self.arch = arch or ARCHES.get(platform.machine().lower(), platform.machine().lower() or "unknown")
        self.home = Path(home) if home else Path.home()
        self.env = dict(os.environ if env is None else env)
        self._which = which            # {name: path or None} in tests; None = ask the real PATH
        self._systemd = systemd
        self._distro = distro
        self._probe = probe            # callable(argv) -> (rc, output) in tests

    # ---- tools ------------------------------------------------------------------------
    def which(self, name):
        if self._which is not None:
            return self._which.get(name)
        return shutil.which(name)

    def tool(self, name):
        """Full path of a program when it's on the PATH, else its bare name (the error then says what's missing)."""
        return self.which(name) or name

    def probe(self, argv, timeout=8):
        """Run a command that only looks at things (a version check). Returns (exit code, output)."""
        if self._probe is not None:
            return self._probe([str(a) for a in argv])
        import subprocess
        try:
            from ..proc import hidden
            kw = hidden()
            p = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, env=self.env, **kw)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as e:
            return 127, str(e)

    @property
    def has_display(self):
        if self.system != "linux":
            return True
        return bool(self.env.get("DISPLAY") or self.env.get("WAYLAND_DISPLAY"))

    @property
    def uid(self):
        return os.getuid() if hasattr(os, "getuid") else 0

    @property
    def has_systemd(self):
        if self._systemd is not None:
            return self._systemd
        return self.system == "linux" and Path("/run/systemd/system").is_dir()

    @property
    def debian_like(self):
        """apt + dpkg: install .deb packages so the system's updates keep track of them."""
        return self.system == "linux" and bool(self.which("apt-get")) and bool(self.which("dpkg"))

    @property
    def distro(self):
        if self._distro is not None:
            return self._distro
        if self.system != "linux":
            return ""
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "Linux"

    @property
    def label(self):
        name = self.distro if self.system == "linux" and self.distro else SYSTEMS.get(self.system, self.system)
        cpu = {"amd64": "x86-64", "arm64": "ARM 64-bit"}.get(self.arch, self.arch)
        return f"{name} · {cpu}"

    # ---- places -----------------------------------------------------------------------
    @property
    def local_app_data(self):
        """Per-user program folder root: %LOCALAPPDATA%, ~/Library/Application Support, ~/.local/share."""
        if self.system == "windows":
            return Path(self.env.get("LOCALAPPDATA") or self.home / "AppData" / "Local")
        if self.system == "darwin":
            return self.home / "Library" / "Application Support"
        return Path(self.env.get("XDG_DATA_HOME") or self.home / ".local" / "share")

    @property
    def config_home(self):
        if self.system == "windows":
            return Path(self.env.get("APPDATA") or self.home / "AppData" / "Roaming")
        if self.system == "darwin":
            return self.home / "Library" / "Application Support"
        return Path(self.env.get("XDG_CONFIG_HOME") or self.home / ".config")

    @property
    def bin_dir(self):
        return self.home / ".local" / "bin"

    @property
    def start_menu(self):
        return self.config_home / "Microsoft" / "Windows" / "Start Menu" / "Programs"

    def describe(self):
        return {"system": self.system, "systemName": SYSTEMS.get(self.system, self.system), "arch": self.arch,
                "label": self.label}
