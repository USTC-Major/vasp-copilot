"""Private, fixed-endpoint transport for the optional file reviewer."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit

import httpx


PROTOCOL = "file-review-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def signature(secret, protocol_version, challenge, decision):
    body = {"protocol_version": protocol_version, "challenge": challenge,
            "decision": decision}
    return hmac.new(secret.encode("utf-8"), canonical(body), hashlib.sha256).hexdigest()


def settings(env=None):
    env = os.environ if env is None else env
    if env.get("VASP_REVIEWER_ENABLED", "").lower() != "true":
        return None, "REVIEWER_DISABLED"
    secret = env.get("VASP_REVIEWER_SHARED_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        return None, "REVIEWER_SECRET_MISSING"
    url = env.get("VASP_REVIEWER_URL", "")
    try:
        parts = urlsplit(url)
        valid = (not parts.username and not parts.password and not parts.query
                 and not parts.fragment and not parts.path.rstrip("/")
                 and ((parts.scheme == "https" and bool(parts.hostname))
                      or (parts.scheme == "http" and
                          parts.hostname in {"localhost", "127.0.0.1", "::1", "ai_mode"})))
    except ValueError:
        valid = False
    if not valid:
        return None, "REVIEWER_URL_INVALID"
    return {"secret": secret, "url": url.rstrip("/")}, "READY"


def call(payload, *, configuration=None, http=None):
    configuration = configuration or settings()[0]
    if not configuration:
        raise RuntimeError("REVIEWER_UNCONFIGURED")
    client = http or httpx.Client(timeout=125, follow_redirects=False)
    try:
        response = client.post(configuration["url"] + "/ai/internal/reviewer/review",
                               json=payload,
                               headers={"Authorization": "Bearer " + configuration["secret"]})
        if response.status_code != 200:
            raise RuntimeError("REVIEWER_UNAVAILABLE")
        return response.json()
    finally:
        if http is None:
            client.close()
