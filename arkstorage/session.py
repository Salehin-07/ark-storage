"""Signed-cookie sessions as a tiny pure-ASGI middleware (no per-request thread, no BaseHTTPMiddleware).

The cookie holds ``{"u": user, "csrf": token, "p": remember}`` signed with the server secret
(itsdangerous). "Remember me" gives the cookie a lifetime, otherwise it ends with the browser session;
either way the signature itself expires after ``session_hours``.
"""
import os

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.datastructures import MutableHeaders

from . import config

COOKIE = "ark_session"


class Session(dict):
    """A dict that remembers whether it changed. ``permanent`` decides the cookie lifetime."""

    def __init__(self, data=None):
        super().__init__(data or {})
        self.dirty = False
        self.permanent = bool(self.get("p", False))

    def _touch(self):
        self.dirty = True

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._touch()

    def pop(self, *a):
        self._touch()
        return super().pop(*a)

    def clear(self):
        super().clear()
        self._touch()


def _cookies(scope):
    for k, v in scope["headers"]:
        if k == b"cookie":
            out = {}
            for part in v.decode("latin-1").split(";"):
                name, _, val = part.strip().partition("=")
                if name:
                    out[name] = val
            return out
    return {}


class SessionMiddleware:
    def __init__(self, app, secret):
        self.app = app
        self.ser = URLSafeTimedSerializer(secret, salt="ark-session")
        self.secure = os.environ.get("ARK_COOKIE_SECURE", "").lower() in ("1", "true", "yes", "on")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hours = config.get("session_hours")
        raw = _cookies(scope).get(COOKIE)
        data = {}
        if raw:
            try:
                data = self.ser.loads(raw, max_age=hours * 3600)
            except BadSignature:
                data = {}
        sess = scope["ark_session"] = Session(data if isinstance(data, dict) else {})
        secure = self.secure or scope.get("scheme") == "https"

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and sess.dirty:
                flags = "; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")
                if not sess:
                    value = f"{COOKIE}=; Max-Age=0{flags}"
                else:
                    sess["p"] = bool(sess.permanent)
                    age = f"; Max-Age={hours * 3600}" if sess.permanent else ""
                    value = f"{COOKIE}={self.ser.dumps(dict(sess))}{age}{flags}"
                MutableHeaders(scope=message).append("set-cookie", value)
            await send(message)

        await self.app(scope, receive, send_wrapper)
