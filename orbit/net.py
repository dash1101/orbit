"""Small JSON HTTP client. Server-side requests send no Origin header (apps treat them like curl)."""
import json
import socket
import ssl
import time
import urllib.error
import urllib.request

UA = "orbit-hub"


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _ctx(insecure):
    if not insecure:
        return None
    # Only used for Nova's public /ping on a self-signed certificate: nothing secret is sent or trusted.
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def request(method, url, body=None, timeout=2.0, headers=None, insecure=False, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx(insecure)) as r:
            payload = r.read(4 << 20)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(64 << 10) or b"{}").get("error") or e.reason
        except (ValueError, AttributeError):
            msg = e.reason
        raise HttpError(e.code, str(msg)) from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise HttpError(0, str(getattr(e, "reason", e))) from None
    if raw:
        return payload.decode("utf-8", "replace")
    return json.loads(payload or b"null")


def get_json(url, **kw):
    return request("GET", url, **kw)


def post_json(url, body, **kw):
    return request("POST", url, body=body, **kw)


def port_open(host, port, timeout=0.3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Releases:
    """Newest GitHub release per repo, cached for 12 hours. Pre-releases count (most of these apps only have those)."""

    TTL = 12 * 3600

    def __init__(self, fetch=None):
        self._fetch = fetch or self._github
        self._cache = {}

    @staticmethod
    def _github(repo):
        items = get_json(f"https://api.github.com/repos/{repo}/releases?per_page=10", timeout=6,
                         headers=github_headers())
        rel = newest_release(items)
        if rel:
            return {"tag": rel.get("tag_name", ""), "url": rel.get("html_url", ""),
                    "published": rel.get("published_at", ""), "prerelease": bool(rel.get("prerelease"))}
        return None

    def latest(self, repo, force=False):
        hit = self._cache.get(repo)
        if hit and not force and time.time() - hit[0] < self.TTL:
            return hit[1]
        try:
            info = {"ok": True, "release": self._fetch(repo)}
        except HttpError as e:
            info = {"ok": False, "error": "not found (private repository?)" if e.status == 404 else str(e) or "offline"}
        except (ValueError, TypeError):
            info = {"ok": False, "error": "unexpected reply"}
        info["checked"] = time.time()
        self._cache[repo] = (time.time(), info)
        return info

    def cached(self, repo):
        hit = self._cache.get(repo)
        return hit[1] if hit else None


def newest_release(items):
    """The highest-versioned non-draft release. (GitHub's list isn't reliably newest-first.)"""
    rels = [r for r in items or [] if isinstance(r, dict) and not r.get("draft")]
    if not rels:
        return None
    return max(rels, key=lambda r: (version_tuple(r.get("tag_name")), str(r.get("published_at") or "")))


_token = [None]


def github_token():
    """This computer's GitHub sign-in (GITHUB_TOKEN/GH_TOKEN, or the `gh` command's), cached for 10 minutes."""
    if _token[0] is None or time.time() - _token[0][0] > 600:
        import os
        import shutil
        import subprocess
        tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if not tok and shutil.which("gh"):
            try:
                p = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5,
                                   stdin=subprocess.DEVNULL)
                tok = p.stdout.strip() if p.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                tok = ""
        _token[0] = (time.time(), tok)
    return _token[0][1]


def github_headers():
    """api.github.com headers, signed in when this computer is, so private repositories' releases show too."""
    h = {"Accept": "application/vnd.github+json"}
    tok = github_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def version_tuple(v):
    """'v0.1.4' / '0.9.13~alpha' / '0.1.0a9' / 'v0.1.0-alpha.9' -> comparable tuple."""
    import re
    s = str(v or "").strip().lstrip("vV")
    m = re.match(r"(\d+(?:\.\d+)*)(.*)", s)
    if not m:
        return ()
    nums = (tuple(int(x) for x in m.group(1).split(".")) + (0, 0, 0, 0))[:4]
    rest = m.group(2).lower()
    pre = re.search(r"(alpha|beta|rc|a|b)[.\-]?(\d*)", rest)
    if pre:
        rank = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2}[pre.group(1)]
        return nums + (0, rank, int(pre.group(2) or 0))
    if rest.strip("~-.") and not pre:
        return nums + (0, 0, 0)  # an unrecognised suffix counts as a pre-release
    return nums + (1, 0, 0)


def is_newer(latest, current):
    a, b = version_tuple(latest), version_tuple(current)
    return bool(a and b and a > b)
