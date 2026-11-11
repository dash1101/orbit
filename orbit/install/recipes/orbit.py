"""Orbit itself: the standalone program updates itself (Linux, Windows and macOS).

The new program comes from Orbit's own GitHub release and is checked against the release's SHA256SUMS. It then has to
start and say which version it is, so a broken or wrong download never takes the old one's place. It waits next to
the running program (`orbit.new`, with `orbit.new.ok` saying it passed) until Orbit restarts.

The switch happens only when Orbit restarts (or the next time it starts): a running standalone Orbit keeps reading
parts of itself from its own file, so that file can't change under it. At the switch the old program is renamed
(`orbit.old`; Windows allows renaming a running program), the new one takes its name, and the new one starts. If
anything goes wrong while switching, the old one is put back. The new Orbit deletes `orbit.old` once it runs.

On macOS what's replaced is the whole app (Orbit.app, a folder): the new one is copied out of the release's .dmg as
`.Orbit-new.app`, and at the switch the old one becomes `.Orbit-old.app` (hidden in Finder; an app's folder has to
end in .app for it to start). When Orbit runs from its source folder, Orbit
says how to update instead.
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


def unit(exe):
    """What an update replaces: the program file, or on macOS the whole app it's inside (…/Orbit.app)."""
    exe = Path(exe)
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents" and exe.parents[2].suffix == ".app":
        return exe.parents[2]
    return exe


def _remove(path):
    import shutil
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def cleanup_old(exe=None):
    """Delete the previous program left by an update (`orbit.old`), once the new one runs. Quietly."""
    exe = exe or running_program()
    if not exe:
        return True
    old = _beside(unit(exe), ".old")
    try:
        _remove(old)
        return True
    except OSError:
        return False  # Windows: the old one may still be closing; try again in a moment


def _beside(path, suffix):
    """The new or old copy next to `path` (suffix ".new", ".old" or ".new.ok"). An app's folder must keep ending
    in .app to start, so for a Mac app these are hidden names: .Orbit-new.app, .Orbit-old.app, .Orbit-new.ok."""
    if path.suffix == ".app":
        s = path.stem
        return path.with_name({".new": f".{s}-new.app", ".old": f".{s}-old.app", ".new.ok": f".{s}-new.ok"}[suffix])
    return path.with_name(path.name + suffix)


def waiting(exe=None):
    """The version of a checked new Orbit waiting next to this one for a restart, or ""."""
    exe = exe or running_program()
    if not exe:
        return ""
    u = unit(exe)
    try:
        said = _beside(u, ".new.ok").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return said[len("orbit "):] if said.startswith("orbit ") and _beside(u, ".new").exists() else ""


def switch(exe=None):
    """Put the waiting new Orbit in this one's place. Returns True when it did; raises OSError (with the old one
    back in place) when it couldn't. Call it only right before starting the new one: see the module's notes."""
    exe = exe or running_program()
    if not exe or not waiting(exe):
        return False
    u = unit(exe)
    new, old = _beside(u, ".new"), _beside(u, ".old")
    try:
        _remove(old)
    except OSError:
        pass
    os.replace(u, old)
    try:
        os.replace(new, u)
    except OSError:
        os.replace(old, u)            # put the old one back
        raise
    try:
        _beside(u, ".new.ok").unlink()
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
        if self.host.system == "darwin":
            return f"orbit-{tag}-macos-" + ("apple-silicon" if self.host.arch == "arm64" else "intel") + ".dmg"
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
        if not exe:
            return [need("Running as the standalone program", False,
                         "This Orbit runs from its Python source, so it can't replace itself. Update the source "
                         "(`git pull`) and run `./install.sh` again, or use the standalone program from the releases "
                         "page, which updates itself.")]
        if not self.asset("v0"):
            return [need("A standalone program for this computer", False,
                         f"There's no standalone Orbit for {self.host.label} yet.")]
        where = unit(exe).parent
        writable = os.access(where, os.W_OK)
        return [need("Orbit's folder can be written to", writable,
                     f"Orbit can't replace itself in {where}. Download the new version from the releases "
                     "page instead.", detail=str(where))]

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
                f"Keep it next to this one ({unit(exe).name if exe else 'orbit'}) until you restart Orbit: then it takes "
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
        u = unit(exe)
        new = _beside(u, ".new")
        s.vars["new"] = new
        want = s.vars["rel"]["tag"].lstrip("vV")
        s.detail(f"starting it once · it should say {want}")
        if s.dry_run:
            s.pause(0.4)
            s.log(f"dry run: would copy it to {new} and run it once to check it says orbit {want}")
            return
        import shutil
        _remove(new)
        if u != exe:          # macOS: the app comes out of the release's disk image
            mnt = s.tmp / "image"
            mnt.mkdir(exist_ok=True)
            s.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mnt, s.vars["file"]],
                  quiet=True, fail="Couldn't open the downloaded disk image.")
            try:
                if not (mnt / u.name).is_dir():
                    raise Failed(f"The disk image doesn't contain {u.name}.")
                s.run(["ditto", mnt / u.name, new], quiet=True, fail="Couldn't copy Orbit out of the disk image.")
            finally:
                s.run(["hdiutil", "detach", mnt, "-force"], ok=range(0, 256), quiet=True)
            new_exe = new / exe.relative_to(u)
        else:
            shutil.copyfile(s.vars["file"], new)
            os.chmod(new, 0o755)
            new_exe = new
        fd, out = tempfile.mkstemp(prefix="orbit-version-")
        os.close(fd)
        said, code, problem = "", None, ""
        try:
            from ...proc import hidden
            p = subprocess.run([str(new_exe), "--write-version", out], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=120, **hidden())
            code = p.returncode
            printed = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
            if code != 0 and printed:
                for line in printed.splitlines()[-15:]:
                    s.log("  " + line)
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
                _remove(new)
            except OSError:
                pass
            raise Failed(f"The download didn't start as Orbit {want}, so it won't replace this one.",
                         "Nothing was replaced. Try again later, or download it from the releases page.")
        _beside(u, ".new.ok").write_text(f"orbit {want}\n", encoding="utf-8")
        s.log(f"Orbit {want} is ready next to this one; it takes over when Orbit restarts.")

    def finish(self, sess, op, opts):
        rel = sess.vars.get("rel") or {}
        v = rel.get("tag", "").lstrip("vV")
        return {"headline": f"Orbit {v} is ready.",
                "body": "Restart Orbit to switch to it: its window closes and opens again on the new version. "
                        "(If you just close Orbit, it switches the next time it starts.)",
                "next": [{"id": "restart", "label": "restart orbit", "kind": "restart", "primary": True}]}
