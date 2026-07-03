"""docs_index.py — the "doc" tier: an in-memory lexical index over the shipped docs corpus.

Built once at startup from ``DOCS_ROOT`` (the image vendors ``docs/docs`` → ``/app/docs``).
Deliberately stdlib-only: the corpus is ~37 files / ~288 KB, so a search dependency would be
pure weight (P17). Every ``.mdx`` is split into HEADING SECTIONS — fenced code blocks are
opaque (a ``#`` inside ``` is a shell comment, never a heading) — and scored by plain term
frequency with a heading/title boost, damped by section length and multiplied by query-term
coverage (a section matching every term beats a long section matching one). Results cite
``path`` + ``heading`` — the citation a ``provenance:"doc"`` answer must carry.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TITLE_RE = re.compile(r"""^\s*title:\s*["']?(.*?)["']?\s*$""")

HEADING_BOOST = 3.0
TITLE_BOOST = 2.0
SNIPPET_CHARS = 240


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens — the one tokenizer for corpus, queries and filters."""
    return _TOKEN_RE.findall((text or "").lower())


@dataclass
class Section:
    """One heading-delimited slice of one doc — the unit a citation points at."""

    path: str      # docs-root-relative, e.g. "deployment.mdx"
    heading: str   # nearest heading (the frontmatter title for the preamble)
    title: str     # the doc's frontmatter title (filename stem when absent)
    text: str
    body_tf: Counter
    heading_tf: Counter
    title_tf: Counter
    length: int    # body token count (the damping term)


def _section(path: str, heading: str, title: str, text: str) -> Section:
    body = tokenize(text)
    return Section(
        path=path, heading=heading, title=title, text=text,
        body_tf=Counter(body), heading_tf=Counter(tokenize(heading)),
        title_tf=Counter(tokenize(title)), length=len(body),
    )


def parse_sections(rel_path: str, raw: str) -> List[Section]:
    """Split one ``.mdx`` into sections: frontmatter title, then one section per heading.

    The preamble (text before the first heading) becomes a section headed by the doc title,
    so a doc's intro is citable too. ``#`` lines inside fenced code blocks stay body text.
    """
    lines = raw.splitlines()
    title = Path(rel_path).stem
    start = 0
    if lines and lines[0].strip() == "---":                       # frontmatter
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                for line in lines[1:j]:
                    m = _TITLE_RE.match(line)
                    if m:
                        title = m.group(1)
                start = j + 1
                break

    sections: List[Section] = []
    heading, buf, in_fence = title, [], False

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append(_section(rel_path, heading, title, text))

    for line in lines[start:]:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            flush()
            heading, buf = m.group(2), []
        else:
            buf.append(line)
    flush()
    return sections


def snippet(text: str, query_tokens: List[str], chars: int = SNIPPET_CHARS) -> str:
    """A whitespace-collapsed window around the first query-token hit."""
    flat = re.sub(r"\s+", " ", text).strip()
    low = flat.lower()
    hits = [low.find(t) for t in query_tokens if low.find(t) >= 0]
    pos = min(hits) if hits else 0
    begin = max(0, pos - chars // 3)
    clip = flat[begin:begin + chars].strip()
    return ("…" if begin else "") + clip + ("…" if begin + chars < len(flat) else "")


@dataclass
class DocsIndex:
    """The searchable corpus — ``load`` at startup, ``search``/``get`` per tool call."""

    root: Path
    sections: List[Section]
    files: int

    @classmethod
    def load(cls, root: Path | str) -> "DocsIndex":
        root = Path(root)
        sections: List[Section] = []
        files = 0
        if root.is_dir():
            for p in sorted(root.rglob("*.mdx")):
                rel = p.relative_to(root).as_posix()
                sections.extend(parse_sections(rel, p.read_text(encoding="utf-8", errors="replace")))
                files += 1
        return cls(root=root, sections=sections, files=files)

    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        """TF × coverage, heading/title-boosted, length-damped; deterministic tie-break."""
        q = tokenize(query)
        if not q:
            return []
        uniq = set(q)
        scored: List[tuple[float, Section]] = []
        for s in self.sections:
            raw = sum(
                s.body_tf[t] + HEADING_BOOST * s.heading_tf[t] + TITLE_BOOST * s.title_tf[t]
                for t in q
            )
            if raw <= 0:
                continue
            coverage = sum(1 for t in uniq if s.body_tf[t] or s.heading_tf[t] or s.title_tf[t])
            scored.append((raw * coverage / math.sqrt(s.length + 16), s))
        scored.sort(key=lambda pair: (-pair[0], pair[1].path, pair[1].heading))
        return [
            {
                "provenance": "doc",
                "path": s.path,
                "heading": s.heading,
                "snippet": snippet(s.text, q),
                "score": round(score, 4),
            }
            for score, s in scored[:max_results]
        ]

    def get(self, path: str, heading_prefix: Optional[str] = None) -> Optional[Section]:
        """Resolve one section by exact path (+ case-insensitive heading prefix) — the
        deploy-guide selector primitive."""
        for s in self.sections:
            if s.path == path and (
                heading_prefix is None or s.heading.lower().startswith(heading_prefix.lower())
            ):
                return s
        return None
