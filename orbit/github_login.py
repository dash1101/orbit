"""Sign in with GitHub, so Orbit can read releases from private repositories.

GitHub's device flow: Orbit asks GitHub for a short code, you enter it at github.com/login/device, and Orbit
receives a token. No password ever passes through Orbit, and no client secret is needed. The login belongs to
the NovaLabs Orbit GitHub App, which can only *read* repository contents, and only in repositories it is
installed on.

The token is kept in Orbit's settings file (readable by you only) and only sent to api.github.com.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import net

# The NovaLabs Orbit GitHub App (public identifier, not a secret). Empty: signing in is unavailable.
CLIENT_ID = ""

DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"


class LoginError(Exception):
    pass


def _post(url, fields, timeout=15):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Accept": "application/json", "User-Agent": net.UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(64 << 10) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read(64 << 10) or b"{}")
        except ValueError:
            raise LoginError(f"GitHub answered {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise LoginError("Couldn't reach GitHub. Check your internet connection.") from None


class Login:
    """One sign-in at a time; the window follows it with `state()`."""

    MIN_INTERVAL = 5     # GitHub's own minimum between polls

    def __init__(self, config, client_id=None, post=None, user=None):
        self.config = config
        self.client_id = CLIENT_ID if client_id is None else client_id
        self._post = post or _post
        self._user = user or self._fetch_user
        self._lock = threading.Lock()
        self._pending = None      # {"user_code", "verification_uri", "expires", ...}
        self._error = ""
        self._cancel = False

    @property
    def available(self):
        return bool(self.client_id)

    # ---- the token other parts of Orbit use ---------------------------------------------------
    def token(self):
        return (self.config.get("github") or {}).get("token", "")

    def state(self):
        gh = self.config.get("github") or {}
        with self._lock:
            pending = dict(self._pending) if self._pending else None
            error = self._error
        source, who = "", ""
        if gh.get("token"):
            source, who = "orbit", gh.get("login", "")
        elif net.env_token():
            source = "env"
        elif net.gh_cli_token():
            source = "gh"
        if pending:
            pending.pop("device_code", None)
        return {"available": self.available, "signedIn": bool(source), "source": source, "login": who,
                "pending": pending, "error": error}

    # ---- signing in ---------------------------------------------------------------------------
    def start(self):
        if not self.available:
            raise LoginError("Signing in with GitHub isn't set up in this build of Orbit.")
        with self._lock:
            if self._pending and self._pending["expires"] > time.time():
                return self.state()
        r = self._post(DEVICE_URL, {"client_id": self.client_id})
        if not r.get("device_code"):
            raise LoginError(r.get("error_description") or r.get("error") or "GitHub didn't start the sign-in.")
        with self._lock:
            self._pending = {"user_code": r["user_code"], "verification_uri": r.get("verification_uri", "https://github.com/login/device"),
                             "device_code": r["device_code"], "interval": max(self.MIN_INTERVAL, int(r.get("interval", 5))),
                             "expires": time.time() + int(r.get("expires_in", 900))}
            self._error = ""
            self._cancel = False
        threading.Thread(target=self._poll, daemon=True, name="orbit-github-login").start()
        return self.state()

    def cancel(self):
        with self._lock:
            self._cancel = True
            self._pending = None

    def _poll(self):
        while True:
            with self._lock:
                p = self._pending
                if not p or self._cancel:
                    return
                interval, device_code, expires = p["interval"], p["device_code"], p["expires"]
            time.sleep(interval)
            if time.time() > expires:
                return self._fail("The code expired before it was entered. Start again.")
            try:
                r = self._post(TOKEN_URL, {"client_id": self.client_id, "device_code": device_code,
                                           "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            except LoginError:
                continue                     # a network blip: keep waiting
            err = r.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                with self._lock:
                    if self._pending:
                        new = int(r["interval"]) if "interval" in r else interval + 5
                        self._pending["interval"] = max(self.MIN_INTERVAL, new)
                continue
            if err:
                return self._fail({"expired_token": "The code expired before it was entered. Start again.",
                                   "access_denied": "The sign-in was cancelled on GitHub."}.get(err, r.get("error_description") or err))
            tok = r.get("access_token")
            if not tok:
                return self._fail("GitHub didn't send a token.")
            login = ""
            try:
                login = self._user(tok)
            except Exception:
                pass
            with self._lock:
                if self._cancel:
                    return
                self._pending = None
            self.config.update({"github": {"token": tok, "login": login, "since": int(time.time())}})
            net.forget_token()
            return

    def _fail(self, message):
        with self._lock:
            self._pending = None
            self._error = message

    def sign_out(self):
        self.cancel()
        self.config.update({"github": {}})
        net.forget_token()
        return self.state()

    @staticmethod
    def _fetch_user(token):
        u = net.get_json("https://api.github.com/user", timeout=8,
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        return (u or {}).get("login", "")

    # ---- which repositories this sign-in can read ----------------------------------------------
    def access(self, repos):
        """{repo: "public" | "private" | "no access" | "offline"} with the token Orbit would use."""
        out = {}
        for repo in repos:
            try:
                r = net.get_json(f"https://api.github.com/repos/{repo}", timeout=6, headers=net.github_headers())
                out[repo] = "private" if (r or {}).get("private") else "public"
            except net.HttpError as e:
                out[repo] = "no access" if e.status in (401, 403, 404) else "offline"
        return out
