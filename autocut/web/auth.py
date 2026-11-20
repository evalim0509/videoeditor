"""Password login for the web app (it's reachable over the network, so everything needs auth).

- One password per installation, stored as a salted scrypt hash in data/auth.json.
- If no password is set when the server starts, a random passphrase is generated, printed to the
  console and written to data/FIRST_LOGIN.txt (deleted once the password is changed).
- Sessions are HMAC-signed tokens in an HttpOnly cookie (30 days), also accepted as a Bearer token.
- Failed logins are rate-limited per client IP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

from ..config import get_settings

COOKIE = "autocut_session"
SESSION_DAYS = 30
MAX_FAILURES = 8
FAILURE_WINDOW = 600  # seconds
WORDS = ("amber anchor apple arrow aspen bamboo basil beacon birch bison bloom breeze canyon cedar "
         "cherry cliff clover cobalt comet coral cosmos crane delta dune ember fable falcon fern "
         "fjord flint forest frost galaxy garnet glacier harbor hazel heron indigo iris island ivory "
         "jade jasmine juniper kayak kiwi lagoon lantern lemon lotus lunar maple marble meadow mint "
         "monsoon nectar nova oasis ocean olive onyx orbit orchid pebble pepper pine pixel planet "
         "plum prairie quartz quill raven reef river robin saffron sage sierra silver sparrow "
         "spruce summit sunset tango thistle tiger topaz tulip tundra velvet violet willow zephyr").split()


def _auth_file() -> Path:
    return get_settings().home / "auth.json"


def _first_login_file() -> Path:
    return get_settings().home / "FIRST_LOGIN.txt"


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def _load() -> dict:
    f = _auth_file()
    return json.loads(f.read_text()) if f.exists() else {}


def _save(d: dict) -> None:
    f = _auth_file()
    f.write_text(json.dumps(d))
    try:
        f.chmod(0o600)
    except OSError:
        pass


def has_password() -> bool:
    return "hash" in _load()


def set_password(password: str) -> None:
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    d = _load()
    salt = secrets.token_bytes(16)
    d.update({"salt": salt.hex(), "hash": _hash(password, salt), "changed": time.time()})
    d["secret"] = secrets.token_hex(32)  # rotating the secret logs out every old session
    _save(d)
    _first_login_file().unlink(missing_ok=True)


def ensure_password() -> str | None:
    """Create a random passphrase if none exists. Returns it (only when newly created)."""
    if has_password():
        return None
    phrase = "-".join(secrets.choice(WORDS) for _ in range(4))
    set_password(phrase)
    _first_login_file().write_text(
        "autocut first login\n===================\n\n"
        f"Password: {phrase}\n\n"
        "Change it in the app (Settings → Change password); this file is then deleted.\n")
    return phrase


def check_password(password: str) -> bool:
    d = _load()
    if "hash" not in d:
        return False
    return hmac.compare_digest(_hash(password, bytes.fromhex(d["salt"])), d["hash"])


def _secret() -> bytes:
    d = _load()
    if "secret" not in d:
        d["secret"] = secrets.token_hex(32)
        _save(d)
    return bytes.fromhex(d["secret"])


def make_token(days: int = SESSION_DAYS) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"exp": int(time.time() + days * 86400), "n": secrets.token_hex(8)}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    try:
        return json.loads(base64.urlsafe_b64decode(payload))["exp"] > time.time()
    except Exception:
        return False


class RateLimiter:
    def __init__(self):
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            f = [t for t in self._fails.get(ip, []) if now - t < FAILURE_WINDOW]
            self._fails[ip] = f
            return len(f) >= MAX_FAILURES

    def fail(self, ip: str) -> None:
        with self._lock:
            self._fails.setdefault(ip, []).append(time.time())

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


limiter = RateLimiter()
