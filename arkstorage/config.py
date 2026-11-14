"""Configuration: environment variables first, then everything editable from Settings.

Only two things are *required*: ``ARK_REPO_URL`` and ``ARK_GITHUB_TOKEN``. Everything else has a
sensible default and can be changed in the web UI (Settings -> Advanced), which writes
``<data dir>/settings.json`` (plain options) and ``<data dir>/secrets.json`` (mode 0600: token and
passphrase). A value saved in the UI wins over the environment; clearing it falls back to the
environment again.
"""
import json
import os
import re
import secrets
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("ARK_DATA_DIR") or "data").expanduser().resolve()

# key: (kind, default, env names, secret?, restart needed?)
SCHEMA = {
    "repo_url":       ("str", "", ("ARK_REPO_URL", "ARK_REPO"), False, False),
    "token":          ("str", "", ("ARK_GITHUB_TOKEN", "ARK_TOKEN", "GITHUB_TOKEN"), True, False),
    "branch":         ("str", "ark-data", ("ARK_BRANCH",), False, False),
    "encrypt":        ("bool", False, ("ARK_ENCRYPT",), False, False),
    "passphrase":     ("str", "", ("ARK_PASSPHRASE",), True, False),
    "chunk_mb":       ("int", 48, ("ARK_CHUNK_MB",), False, False),
    "cache_mb":       ("int", 1024, ("ARK_CACHE_MB",), False, False),
    "max_upload_mb":  ("int", 20480, ("ARK_MAX_UPLOAD_MB",), False, False),
    "trash":          ("bool", True, ("ARK_TRASH",), False, False),
    "show_hidden":    ("bool", False, ("ARK_SHOW_HIDDEN",), False, False),
    "refresh_seconds": ("int", 10, ("ARK_REFRESH_SECONDS",), False, False),
    "share_hours":    ("int", 24, ("ARK_SHARE_HOURS",), False, False),
    "session_hours":  ("int", 168, ("ARK_SESSION_HOURS",), False, True),
    "commit_name":    ("str", "Ark Storage", ("ARK_COMMIT_NAME",), False, False),
    "commit_email":   ("str", "ark-storage@users.noreply.github.com", ("ARK_COMMIT_EMAIL",), False, False),
    "theme":          ("str", "auto", ("ARK_THEME",), False, False),
}
LIMITS = {"chunk_mb": (1, 95), "cache_mb": (64, 1048576), "max_upload_mb": (0, 10485760),
          "refresh_seconds": (0, 3600), "share_hours": (1, 24 * 365), "session_hours": (1, 24 * 365)}
CHOICES = {"theme": ("auto", "light", "dark")}

_lock = threading.RLock()
_saved = {}


def _parse(kind, raw):
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if kind == "int":
        return int(raw)
    return str(raw).strip()


def _env(key):
    kind, default, names, _secret, _restart = SCHEMA[key]
    for n in names:
        v = os.environ.get(n)
        if v not in (None, ""):
            try:
                return _parse(kind, v)
            except ValueError:
                pass
    return None


def load():
    with _lock:
        _saved.clear()
        for name in ("settings.json", "secrets.json"):
            try:
                _saved.update(json.loads((DATA_DIR / name).read_text("utf-8")))
            except (OSError, ValueError):
                pass


def get(key):
    kind, default, _names, _secret, _restart = SCHEMA[key]
    with _lock:
        if key in _saved and _saved[key] not in (None, ""):
            try:
                return _parse(kind, _saved[key])
            except ValueError:
                pass
        if key in _saved and kind == "bool":
            return _parse(kind, _saved[key])
    env = _env(key)
    return default if env is None else env


def source(key):
    with _lock:
        if key in _saved and _saved[key] not in (None, ""):
            return "settings"
    return "env" if _env(key) is not None else "default"


def validate(key, value):
    """Return the cleaned value or raise ValueError with a human message."""
    if key not in SCHEMA:
        raise ValueError(f"unknown setting: {key}")
    kind = SCHEMA[key][0]
    if value is None or value == "":
        return ""
    v = _parse(kind, value)
    if kind == "int":
        lo, hi = LIMITS.get(key, (0, 10 ** 9))
        if not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
    if key in CHOICES and v not in CHOICES[key]:
        raise ValueError(f"{key} must be one of: {', '.join(CHOICES[key])}")
    if key == "branch" and not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", v):
        raise ValueError("branch may contain letters, digits and . _ / - only")
    if key == "repo_url":
        parse_repo(v)
    return v


def update(changes):
    """Save settings. `changes` maps key -> value ("" or None resets to the environment/default)."""
    clean = {}
    for k, v in changes.items():
        clean[k] = validate(k, v)
    with _lock:
        for k, v in clean.items():
            if v == "" and SCHEMA[k][0] != "bool":
                _saved.pop(k, None)
            elif v == "":
                _saved.pop(k, None)
            else:
                _saved[k] = v
        _write()


def _write():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    plain = {k: v for k, v in _saved.items() if k in SCHEMA and not SCHEMA[k][3]}
    secret = {k: v for k, v in _saved.items() if k in SCHEMA and SCHEMA[k][3]}
    (DATA_DIR / "settings.json").write_text(json.dumps(plain, indent=2), "utf-8")
    p = DATA_DIR / "secrets.json"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(secret))
    os.chmod(p, 0o600)


def public_view():
    """Settings for the UI: secrets are never sent, only whether they are set."""
    out = {}
    for key, (kind, default, names, secret, restart) in SCHEMA.items():
        item = {"kind": kind, "default": default, "source": source(key), "restart": restart,
                "env": names[0], "secret": secret}
        if key in LIMITS:
            item["min"], item["max"] = LIMITS[key]
        if key in CHOICES:
            item["choices"] = list(CHOICES[key])
        if secret:
            item["set"] = bool(get(key))
            item["value"] = ""
        else:
            item["value"] = get(key)
        out[key] = item
    return out


def parse_repo(url):
    """'https://github.com/owner/repo(.git)', 'git@github.com:owner/repo.git' or 'owner/repo' -> (owner, repo)."""
    u = str(url).strip()
    m = (re.fullmatch(r"https?://(?:[^/@]+@)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", u)
         or re.fullmatch(r"git@github\.com:([\w.-]+)/([\w.-]+?)(?:\.git)?", u)
         or re.fullmatch(r"([\w.-]+)/([\w.-]+?)(?:\.git)?", u))
    if not m:
        raise ValueError("repository must look like https://github.com/owner/repo")
    return m.group(1), m.group(2)


def secret_key():
    env = os.environ.get("ARK_SECRET_KEY")
    if env:
        return env
    p = DATA_DIR / "secret.key"
    try:
        return p.read_text().strip()
    except OSError:
        pass
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


load()
