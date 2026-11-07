"""Smoobu API: HMAC authentication (Smoobu announced the end of the old `Api-Key` header for 2026-10-31).

Spec: https://docs.smoobu.com/#hmac-authentication. Canonical string = method, path, query,
timestamp, nonce, SHA-256(body) hex and API key, joined with "\\n", signed with HMAC-SHA256 and the
secret, Base64. Query parameters are sorted and RFC 3986-encoded, and the URL is sent in exactly
that form, so signature and request never drift apart. Every attempt (a retry too) needs fresh
headers: the nonce is single-use, the timestamp is valid for 5 minutes.

Keys: SMOOBU_HMAC_KEY + SMOOBU_HMAC_SECRET in /etc/guest-tv/secrets.env (0640, root:<user>).
"""

import base64
import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import config

KEY_NAME = "SMOOBU_HMAC_KEY"
SECRET_NAME = "SMOOBU_HMAC_SECRET"
SECRETS_FILE = config.ETC / "secrets.env"


def load_credentials() -> tuple[str, str]:
    """(API key, secret) from secrets.env; fails loudly if one is missing."""
    vals: dict[str, str] = {}
    try:
        with open(SECRETS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError as exc:
        raise RuntimeError(f"{SECRETS_FILE} unreadable: {exc}") from exc
    missing = [n for n in (KEY_NAME, SECRET_NAME) if not vals.get(n)]
    if missing:
        raise RuntimeError(f"secrets missing in {SECRETS_FILE}: {', '.join(missing)}")
    return vals[KEY_NAME], vals[SECRET_NAME]


def sign(
    method: str,
    url: str,
    body: bytes,
    key: str,
    secret: str,
    *,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> tuple[str, dict[str, str]]:
    """(URL to send, auth headers) for exactly one attempt."""
    parts = urlsplit(url)
    query = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(parse_qsl(parts.query, keep_blank_values=True))
    )
    timestamp = timestamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = nonce or str(uuid.uuid4())
    canonical = "\n".join((method.upper(), parts.path, query, timestamp, nonce, hashlib.sha256(body).hexdigest(), key))
    signature = base64.b64encode(hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).digest()).decode()
    headers = {"X-API-Key": key, "X-Timestamp": timestamp, "X-Nonce": nonce, "X-Signature": signature}
    return urlunsplit(parts._replace(query=query)), headers
