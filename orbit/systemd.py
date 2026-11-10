"""systemd units: read their state (always allowed) and start/stop/enable user units.

System units (like Nova's) are read-only here: changing them needs root, and Orbit never asks for it.
"""
import re

PROPS = ["Id", "Description", "LoadState", "ActiveState", "SubState", "UnitFileState", "Result",
         "ActiveEnterTimestamp", "InactiveEnterTimestamp", "MainPID", "NRestarts"]
USER_VERBS = ("start", "stop", "restart", "enable", "disable")
_UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:\\-]{1,120}\.(service|socket|timer|path|target)$")


def valid_unit(name):
    return isinstance(name, str) and bool(_UNIT_RE.match(name))


def _base(user):
    return ["systemctl", "--user"] if user else ["systemctl"]


def show(runner, unit, user=True):
    if not valid_unit(unit):
        return {"unit": unit, "error": "not a unit name"}
    rc, out = runner.read(_base(user) + ["show", unit, "--no-pager"] + [f"-p{p}" for p in PROPS])
    if rc != 0:
        return {"unit": unit, "error": out.strip() or f"systemctl exit {rc}"}
    props = {}
    for line in out.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            props[k] = v
    return summarize(unit, props, user)


def summarize(unit, p, user=True):
    load, active, sub = p.get("LoadState", ""), p.get("ActiveState", ""), p.get("SubState", "")
    enabled = p.get("UnitFileState", "")
    result = p.get("Result", "")
    if load == "not-found":
        state = "missing"
    elif active == "failed" or (active == "inactive" and result not in ("", "success")):
        state = "failed"
    elif active in ("active", "reloading"):
        state = "running" if sub in ("running", "listening", "waiting", "exited", "mounted", "plugged") else sub or "active"
    elif active == "activating":
        state = "starting"
    else:
        state = "stopped"
    since = p.get("ActiveEnterTimestamp") if active == "active" else p.get("InactiveEnterTimestamp")
    return {
        "unit": unit, "user": user, "description": p.get("Description", ""), "state": state,
        "active": active, "sub": sub, "enabled": enabled, "result": result, "since": since or "",
        "restarts": int(p.get("NRestarts") or 0) if (p.get("NRestarts") or "0").isdigit() else 0,
    }


def act(runner, unit, verb, user=True):
    """start/stop/restart/enable/disable a *user* unit. Returns (ok, message)."""
    if not user:
        return False, "System services need root; use the terminal command instead."
    if verb not in USER_VERBS or not valid_unit(unit):
        return False, "unknown action"
    rc, out = runner.write(_base(True) + [verb, unit], timeout=30)
    return rc == 0, out.strip() or f"{verb} {unit}: done"


def journal(runner, unit, user=True, lines=80):
    if not valid_unit(unit):
        return ""
    argv = ["journalctl", "--no-pager", "-q", "-o", "short-iso", "-n", str(int(lines))]
    argv += ["--user-unit", unit] if user else ["-u", unit]
    rc, out = runner.read(argv, timeout=8)
    if rc != 0 and not out.strip():
        return f"(journalctl exit {rc})"
    return out
