"""Orbit itself: the standalone program updates itself (Linux and Windows).

The new program comes from Orbit's own GitHub release and is checked against the release's SHA256SUMS. It then has to
start and say which version it is, so a broken or wrong download never takes the old one's place. It waits next to
the running program (`orbit.new`, with `orbit.new.ok` saying it passed) until Orbit restarts.

The switch happens only when Orbit restarts (or the next time it starts): a running standalone Orbit keeps reading
parts of itself from its own file, so that file can't change under it. At the switch the old program is renamed
(`orbit.old`; Windows allows renaming a running program), the new one takes its name, and the new one starts. If
anything goes wrong while switching, the old one is put back. The new Orbit deletes `orbit.old` once it runs.

On macOS (an app bundle inside a .dmg) and when Orbit runs from its source folder, Orbit says how to update instead.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ... import __version__
from ..engine import Failed, Step
from .base import Recipe, need

REPO = "dash1101/orbit"


def running_program():
    """The standalone program Orbit is running from, or None when it runs from its source folder."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable)


def cleanup_old(exe=None):
    """Delete the previous program left by an update (`orbit.old`), once the new one runs. Quietly."""
    exe = exe or running_program()
    if not exe:
        return True
    old = exe.with_name(exe.name + ".old")
    try:
        if old.exists():
            old.unlink()
        return True
    except OSError:
        return False  # Windows: the old one may still be closing; try again in a moment


def _beside(exe, suffix):
    return exe.with_name(exe.name + suffix)


def waiting(exe=None):
    """The version of a checked new Orbit waiting next to this one for a restart, or ""."""
    exe = exe or running_program()
    if not exe:
        return ""
    try:
        said = _beside(exe, ".new.ok").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return said[len("orbit "):] if said.startswith("orbit ") and _beside(exe, ".new").is_file() else ""


def switch(exe=None):
    """Put the waiting new Orbit in this one's place. Returns True when it did; raises OSError (with the old one
    back in place) when it couldn't. Call it only right before starting the new one: see the module's notes."""
    exe = exe or running_program()
    if not exe or not waiting(exe):
        return False
    new, old = _beside(exe, ".new"), _beside(exe, ".old")
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)          # put the old one back
        raise
    try:
        _beside(exe, ".new.ok").unlink()
    except OSError:
        pass
    return True


class OrbitRecipe(Recipe):
    id = "orbit"
    name = "Orbit"
    repo = REPO
    blurb = "This app: one place for all your apps"
    glyph = "OR"
    color = "#f0668e"
    self_app = True
    PLATFORMS = {
        "linux": ("full", ""),
        "windows": ("full", ""),
        "darwin": ("full", ""),
    }
    program = None            # tests point this at a stand-in program; None = the one running
    version = __version__

    def exe(self):
        return Path(self.program) if self.program else running_program()

    def asset(self, tag):
        if self.host.system == "windows":
            return f"orbit-{tag}-windows-x64.exe"
        if self.host.system == "linux" and self.host.arch == "amd64":
            return f"orbit-{tag}-linux-x86_64"
        return None

    # ---- what's here --------------------------------------------------------------------
    def detect(self):
        exe = self.exe()
        return {"installed": True, "version": self.version, "pending": waiting(exe) if exe else "",
                "where": str(exe) if exe else str(Path(__file__).resolve().parents[3]),
                "how": "the standalone program" if exe else "from its source folder"}

    def can_uninstall(self):
        return False, "Orbit doesn't remove itself: close it and delete the program file."

    def requirements(self, op):
        if op == "uninstall":
            return []
        exe = self.exe()
        if self.host.system == "darwin":
            return [need("Updating itself", False, "On a Mac, download the new .dmg from Orbit's releases page and "
                                                   "drag Orbit onto Applications again.")]
        if not exe:
            return [need("Running as the standalone program", False,
                         "This Orbit runs from its source folder: update it with `git pull` there.")]
        if not self.asset("v0"):
            return [need("A standalone program for this computer", False,
                         f"There's no standalone Orbit for {self.host.label} yet.")]
        writable = os.access(exe.parent, os.W_OK)
        return [need("Orbit's folder can be written to", writable,
                     f"Orbit can't replace itself in {exe.parent}. Download the new version from the releases "
                     "page instead.", detail=str(exe.parent))]

    def assets(self, rel):
        name = self.asset(rel["tag"])
        return [name, "SHA256SUMS"] if name else []

    def title(self, op, rel):
        new = (rel or {}).get("tag", "").lstrip("vV")
        return f"Update Orbit to {new}" if new and new != self.version else "Reinstall Orbit"

    def summary(self, op, opts, rel):
        tag = rel["tag"] if rel else "the newest release"
        exe = self.exe()
        return [f"Download Orbit {tag} from GitHub: {self.asset(tag) if rel else 'the program for this computer'}",
                "Check it against the release's checksums",
                "Start it once to make sure it runs and is the version it should be",
                f"Keep it next to this one ({exe.name if exe else 'orbit'}) until you restart Orbit: then it takes "
                "this one's place, and the old one is kept until the new one has started"]

    def steps(self, op, opts):
        return [
            Step("find", "Find the latest release", self._find, 0.5),
            Step("download", "Download", self._download, 4),
            Step("verify", "Check the download", self._verify, 0.5),
            Step("try", "Make sure it runs", self._try, 1),
        ]

    # ---- steps ------------------------------------------------------------------------------
    def _find(self, s):
        s.vars["rel"] = rel = s.release(self.repo)
        name = self.asset(rel["tag"])
        s.asset(rel, name, self.repo)
        s.asset(rel, "SHA256SUMS", self.repo)
        s.vars["name"] = name
        s.detail(rel["tag"])

    def _download(self, s):
        s.vars["file"] = s.download(s.vars["rel"], s.vars["name"], self.repo)

    def _verify(self, s):
        sums = s.fetch_text(s.vars["rel"], "SHA256SUMS", self.repo)
        s.verify(s.vars["file"], sums, s.vars["name"])

    def _try(self, s):
        """Copy it next to the running program and start it once: it has to write its version back."""
        exe = self.exe()
        new = exe.with_name(exe.name + ".new")
        s.vars["new"] = new
        want = s.vars["rel"]["tag"].lstrip("vV")
        s.detail(f"starting it once · it should say {want}")
        if s.dry_run:
            s.pause(0.4)
            s.log(f"dry run: would copy it to {new} and run it once to check it says orbit {want}")
            return
        import shutil
        shutil.copyfile(s.vars["file"], new)
        os.chmod(new, 0o755)
        fd, out = tempfile.mkstemp(prefix="orbit-version-")
        os.close(fd)
        said, code, problem = "", None, ""
        try:
            from ...proc import hidden
            p = subprocess.run([str(new), "--write-version", out], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, **hidden())
            code = p.returncode
            said = Path(out).read_text(encoding="utf-8").strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            problem = str(e)
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
        s.log(f"The new program says: {said or '(nothing)'}" + (f" (exit {code})" if code is not None else "")
              + (f" · {problem}" if problem else ""))
        if code != 0 or said != f"orbit {want}":
            try:
                new.unlink()
            except OSError:
                pass
            raise Failed(f"The download didn't start as Orbit {want}, so it won't replace this one.",
                         "Nothing was replaced. Try again later, or download it from the releases page.")
        _beside(exe, ".new.ok").write_text(f"orbit {want}\n", encoding="utf-8")
        s.log(f"Orbit {want} is ready next to this one; it takes over when Orbit restarts.")

    def finish(self, sess, op, opts):
        rel = sess.vars.get("rel") or {}
        v = rel.get("tag", "").lstrip("vV")
        return {"headline": f"Orbit {v} is ready.",
                "body": "Restart Orbit to switch to it: its window closes and opens again on the new version. "
                        "(If you just close Orbit, it switches the next time it starts.)",
                "next": [{"id": "restart", "label": "restart orbit", "kind": "restart", "primary": True}]}
