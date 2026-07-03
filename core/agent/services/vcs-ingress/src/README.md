# src — vcs-ingress service source root

Holds the `vcs_ingress` Python package (the `pythonpath` for tests, per `pyproject.toml`).
The package is the front door; import `create_app` from `vcs_ingress`, never a deep module
path (P6).
