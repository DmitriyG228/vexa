"""help_mcp — the vexa help companion's front door: ``create_app`` (P6)."""
from .app import PROVENANCE_RULE, RETENTION_NOTE, create_app

__all__ = ["create_app", "PROVENANCE_RULE", "RETENTION_NOTE"]
