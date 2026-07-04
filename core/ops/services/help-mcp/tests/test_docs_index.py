"""L2 unit — the stdlib lexical index: mdx parsing (frontmatter, headings, code-fence
immunity), relevance GOLDENS (query → expected top section) on the tmp fixture corpus, and a
smoke pass over the REAL shipped corpus (offline — files in this repo)."""
import pytest

from help_mcp.docs_index import DocsIndex, parse_sections, tokenize

from conftest import CORPUS, repo_docs_root


# ── parsing ───────────────────────────────────────────────────────────────────────────────────
def test_frontmatter_title_and_preamble_section():
    sections = parse_sections("install.mdx", CORPUS["install.mdx"])
    # the preamble (text before the first heading) is citable, headed by the doc title
    assert sections[0].heading == "Install"
    assert sections[0].title == "Install"
    assert "Docker Compose" in sections[0].text


def test_headings_split_sections():
    sections = parse_sections("install.mdx", CORPUS["install.mdx"])
    assert [s.heading for s in sections] == ["Install", "Quick start (Docker Compose)", "Air-gapped"]


def test_hash_inside_code_fence_is_not_a_heading():
    sections = parse_sections("install.mdx", CORPUS["install.mdx"])
    headings = {s.heading for s in sections}
    assert not any(h.startswith("not a heading") for h in headings)
    # the fenced line stays body text of the section it sits in
    quick = next(s for s in sections if s.heading.startswith("Quick start"))
    assert "not a heading" in quick.text


def test_doc_without_frontmatter_uses_filename_stem():
    sections = parse_sections("plain.mdx", "Just some text without any heading.")
    assert sections[0].title == "plain"
    assert sections[0].heading == "plain"


def test_tokenize_is_lowercase_alnum():
    assert tokenize("Docker-Compose, v0.12!") == ["docker", "compose", "v0", "12"]


# ── relevance goldens (the fixture corpus) ────────────────────────────────────────────────────
GOLDENS = [
    ("docker compose quick start", "install.mdx", "Quick start (Docker Compose)"),
    ("air-gapped registry", "install.mdx", "Air-gapped"),
    ("api key", "auth.mdx", "API keys"),
    ("send a bot to a meeting", "bots.mdx", "Send a bot"),
    ("websocket transcript stream", "how-to/stream.mdx", "WebSocket stream"),
]


@pytest.fixture
def index(docs_root) -> DocsIndex:
    return DocsIndex.load(docs_root)


@pytest.mark.parametrize("query,path,heading", GOLDENS)
def test_relevance_goldens(index, query, path, heading):
    top = index.search(query, max_results=1)[0]
    assert (top["path"], top["heading"]) == (path, heading)


def test_results_are_scored_descending_and_capped(index):
    results = index.search("bot meeting", max_results=2)
    assert len(results) <= 2
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_no_match_and_empty_query_return_nothing(index):
    assert index.search("zzz qqq nonexistent") == []
    assert index.search("") == []
    assert index.search("!!! ---") == []


def test_snippet_contains_a_query_token(index):
    top = index.search("admin token", max_results=1)[0]
    assert "admin" in top["snippet"].lower()


def test_get_resolves_path_and_heading_prefix(index):
    s = index.get("install.mdx", "Quick start")
    assert s is not None and s.heading == "Quick start (Docker Compose)"
    assert index.get("install.mdx", "No such heading") is None
    assert index.get("missing.mdx") is None


# ── the REAL shipped corpus (offline smoke) ───────────────────────────────────────────────────
def test_real_corpus_loads_and_answers():
    index = DocsIndex.load(repo_docs_root())
    assert index.files >= 30          # the shipped corpus (37 files at seal time; may grow)
    top = index.search("docker compose quick start", max_results=1)[0]
    assert top["path"] == "deployment.mdx"
    assert top["provenance"] == "doc"


def test_missing_root_degrades_to_empty_index(tmp_path):
    index = DocsIndex.load(tmp_path / "nope")
    assert index.files == 0
    assert index.search("anything") == []
