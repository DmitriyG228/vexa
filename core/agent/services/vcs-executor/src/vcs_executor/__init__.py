"""vcs_executor — the credentialed executor for human-approved proposal.v1 actions.

Public surface (P6): ``create_app`` only. The pipeline modules (``consumer`` · ``policy`` ·
``github_app`` · ``actions``) are internal seams, reachable for the conformance tests but never a
cross-package import surface — this package is self-contained and consumes proposal.v1 by schema
path, never by importing agent-api code.
"""
from .app import create_app

__all__ = ["create_app"]
