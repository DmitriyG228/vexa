"""vcs_ingress — the untrusted-event ingress (GitHub webhooks → opaque refs → agent-api).

Public surface (P6): ``create_app``. The helpers (``verify_signature``, ``normalize``,
``subscriptions_for``) are exported for the conformance tests.
"""
from vcs_ingress.app import create_app
from vcs_ingress.normalize import ALLOWED_EVENTS, delivery_record, normalize
from vcs_ingress.subscriptions import VCS_SUBS_HASH, subscriptions_for
from vcs_ingress.verify import signature_of, verify_signature

__all__ = [
    "ALLOWED_EVENTS",
    "VCS_SUBS_HASH",
    "create_app",
    "delivery_record",
    "normalize",
    "signature_of",
    "subscriptions_for",
    "verify_signature",
]
