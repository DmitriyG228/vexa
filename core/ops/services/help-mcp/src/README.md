# src — help-mcp service source root

Holds the `help_mcp` Python package (the `pythonpath` for tests, per `pyproject.toml`).
The package is the front door; import `create_app` from `help_mcp`, never a deep module
path (P6).
