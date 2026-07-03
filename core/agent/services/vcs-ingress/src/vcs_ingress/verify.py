"""verify.py — X-Hub-Signature-256 webhook verification (stdlib only, constant-time).

GitHub signs every webhook body with HMAC-SHA256 over the App's shared secret and sends the hex
digest as ``X-Hub-Signature-256: sha256=<hex>``. This module recomputes it over the RAW body bytes
and compares with ``hmac.compare_digest`` (constant-time — no timing oracle). Fail-closed: a
missing header, a malformed header, or a mismatch all verify False; the caller answers 401 and
nothing is persisted or dispatched.
"""
from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def signature_of(secret: str, body: bytes) -> str:
    """The expected ``X-Hub-Signature-256`` value for ``body`` under ``secret``."""
    return _PREFIX + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """True iff ``header`` is the valid GitHub signature of ``body``. Constant-time, fail-closed."""
    if not secret or not header:
        return False
    return hmac.compare_digest(signature_of(secret, body), header.strip())
