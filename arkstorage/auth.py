"""Single-account login (default ark / ark, changeable in Settings)."""
import json
import os
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

from . import config

_lock = threading.Lock()
_fails = {}          # ip -> (count, first_fail_ts)
LIMIT, WINDOW = 6, 300


def _path():
    return config.DATA_DIR / "auth.json"


def load():
    with _lock:
        try:
            return json.loads(_path().read_text("utf-8"))
        except (OSError, ValueError):
            pass
        user = os.environ.get("ARK_USERNAME") or "ark"
        pw = os.environ.get("ARK_PASSWORD") or "ark"
        data = {"username": user, "hash": generate_password_hash(pw),
                "default": pw == "ark" and not os.environ.get("ARK_PASSWORD")}
        _save(data)
        return data


def _save(data):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _path()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data))


def locked_out(ip):
    c, ts = _fails.get(ip, (0, 0))
    if c >= LIMIT and time.time() - ts < WINDOW:
        return int(WINDOW - (time.time() - ts))
    if c and time.time() - ts >= WINDOW:
        _fails.pop(ip, None)
    return 0


def verify(username, password, ip):
    wait = locked_out(ip)
    if wait:
        return False, wait
    d = load()
    ok = username == d["username"] and check_password_hash(d["hash"], password or "")
    if ok:
        _fails.pop(ip, None)
        return True, 0
    c, ts = _fails.get(ip, (0, time.time()))
    _fails[ip] = (c + 1, ts if c else time.time())
    return False, 0


def change(current, new_username=None, new_password=None):
    d = load()
    if not check_password_hash(d["hash"], current or ""):
        raise ValueError("Current password is wrong")
    if new_username:
        if not 1 <= len(new_username) <= 64:
            raise ValueError("Username must be 1-64 characters")
        d["username"] = new_username
    if new_password:
        if len(new_password) < 4:
            raise ValueError("Password must be at least 4 characters")
        d["hash"] = generate_password_hash(new_password)
        d["default"] = False
    _save(d)
    return d
