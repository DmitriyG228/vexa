"""L2 unit — X-Hub-Signature-256 verification is constant-time stdlib hmac, fail-closed."""
from __future__ import annotations

from vcs_ingress import signature_of, verify_signature

SECRET = "s3cr3t"
BODY = b'{"action":"opened"}'


def test_valid_signature_accepts():
    assert verify_signature(SECRET, BODY, signature_of(SECRET, BODY)) is True


def test_tampered_body_rejects():
    sig = signature_of(SECRET, BODY)
    assert verify_signature(SECRET, BODY + b" ", sig) is False


def test_missing_header_rejects():
    assert verify_signature(SECRET, BODY, None) is False
    assert verify_signature(SECRET, BODY, "") is False


def test_wrong_secret_rejects():
    assert verify_signature(SECRET, BODY, signature_of("other", BODY)) is False


def test_malformed_header_rejects():
    assert verify_signature(SECRET, BODY, "sha1=deadbeef") is False
    assert verify_signature(SECRET, BODY, "sha256=") is False


def test_empty_secret_fails_closed():
    # No configured secret can never verify anything — the app answers 503 before this, but the
    # primitive itself must also refuse.
    assert verify_signature("", BODY, signature_of("", BODY)) is False
