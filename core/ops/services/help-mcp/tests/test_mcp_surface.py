"""L1 — the MCP surface: the /mcp mount exists, exactly the 5 help tools are exposed, and
every tool description (the docstring the caller's agent reads) discloses question retention —
the privacy stance is USER-FACING, not just a README paragraph."""

EXPECTED_TOOLS = {
    "search_vexa_docs",
    "get_deploy_guide",
    "check_known_issues",
    "escalate",
    "review_question_log",
}


def test_mcp_mounted_and_tools_match(app_factory):
    app = app_factory()
    mcp = app.state.mcp
    assert {t.name for t in mcp.tools} == EXPECTED_TOOLS
    # The MCP transport is mounted on the app at /mcp.
    assert any(getattr(r, "path", "") == "/mcp" for r in app.routes)


def test_every_tool_description_discloses_retention(app_factory):
    """The retention disclosure lives in the docstrings — the MCP tool descriptions themselves."""
    app = app_factory()
    for tool in app.state.mcp.tools:
        text = (tool.description or "").lower()
        assert "retained" in text or "retention" in text, f"{tool.name} does not disclose retention"


def test_maintainer_tool_is_marked(app_factory):
    """review_question_log must say it is maintainer-facing, not an end-user answer surface."""
    app = app_factory()
    tool = next(t for t in app.state.mcp.tools if t.name == "review_question_log")
    assert "maintainer" in (tool.description or "").lower()
