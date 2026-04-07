"""
Text processing utilities for paper chunking, JATS XML parsing, and snippet verification.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

# Pre-compiled patterns used in hot paths (called per paper).
_RE_MULTILINE = re.compile(r"\n{3,}")
_RE_HORIZ_SPACE = re.compile(r"[ \t]+")
_RE_XML_TAGS = re.compile(r"<[^>]+>")
_RE_WHITESPACE = re.compile(r"\s+")


def chunk_text(text: str, max_chars: int = 6000, overlap: int = 200) -> list[tuple[int, int, str]]:
    """
    Split text into overlapping chunks for LLM processing.
    Returns list of (start, end, chunk_text) tuples.
    Splits on paragraph boundaries where possible to avoid cutting mid-sentence.
    """
    if len(text) <= max_chars:
        return [(0, len(text), text)]

    chunks: list[tuple[int, int, str]] = []
    start = 0

    while start < len(text):
        end = min(start + max_chars, len(text))

        # Try to break at a paragraph boundary when not at end of text
        if end < len(text):
            newline_pos = text.rfind("\n\n", start, end)
            if newline_pos > start + max_chars // 2:
                end = newline_pos + 2
            else:
                # Fall back to sentence boundary
                period_pos = text.rfind(". ", start, end)
                if period_pos > start + max_chars // 2:
                    end = period_pos + 2

        chunks.append((start, end, text[start:end]))

        if end >= len(text):
            break

        # Next chunk starts (end - overlap) chars back, but must advance past current start
        next_start = max(end - overlap, start + 1)
        if next_start >= end:
            # Safety: ensure forward progress
            next_start = end
        start = next_start

    return chunks


def clean_jats_xml(xml_text: str) -> str:
    """
    Convert JATS XML to plain readable text for LLM processing.

    Strips all boilerplate (metadata, references, acknowledgements, funding,
    author notes, figure captions, tables) and avoids the duplication bug that
    occurs when collecting both a <sec> container and its <p>/<title> children
    via itertext() — each piece of text is emitted exactly once.
    """
    try:
        from lxml import etree

        root = etree.fromstring(xml_text.encode(), parser=etree.XMLParser(recover=True))

        # ------------------------------------------------------------------
        # 1. Remove entire subtrees that are noise / boilerplate
        # ------------------------------------------------------------------
        _NOISE_TAGS = {
            "ref-list",               # bibliography
            "fig",                    # figure + caption
            "table-wrap",             # tables
            "supplementary-material",
            "ack",                    # acknowledgements
            "fn-group",               # footnotes
            "author-notes",           # author contributions, COI statements
            "funding-group",          # funding sources
            "permissions",            # copyright/license text
            "history",                # submission/revision dates
            "pub-date",               # publication date metadata
            "journal-meta",           # journal name, ISSN, etc.
            "custom-meta-group",      # publisher-specific metadata
            "kwd-group",              # keyword list (redundant with body)
            "counts",                 # figure/table/word counts
            "notes",                  # miscellaneous notes block
            "app-group",              # appendices
            "app",
        }
        for tag in _NOISE_TAGS:
            for el in root.findall(f".//{tag}"):
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)

        # ------------------------------------------------------------------
        # 2. Extract article title and abstract from <front>
        # ------------------------------------------------------------------
        parts: list[str] = []

        for el in root.findall(".//article-title"):
            title_text = "".join(el.itertext()).strip()
            if title_text:
                parts.append(f"TITLE: {title_text}")
            break  # only the first

        for el in root.findall(".//abstract"):
            abstract_text = "".join(el.itertext()).strip()
            if abstract_text:
                parts.append(f"ABSTRACT:\n{abstract_text}")
            break  # only the first

        # ------------------------------------------------------------------
        # 3. Walk <body> collecting section headings + paragraphs only.
        #    We recurse into <sec> containers WITHOUT calling itertext() on
        #    them, which prevents each paragraph appearing multiple times.
        # ------------------------------------------------------------------
        body = root.find(".//body")
        if body is not None:
            _collect_jats_body(body, parts)

        return "\n\n".join(parts)

    except Exception:
        # Fallback: strip all XML tags
        text = _RE_XML_TAGS.sub(" ", xml_text)
        text = _RE_WHITESPACE.sub(" ", text).strip()
        return text


def _strip_ns(tag: str) -> str:
    """Strip XML namespace prefix: '{http://...}p' → 'p'."""
    return tag.split("}")[-1] if "}" in tag else tag


def _collect_jats_body(element, parts: list[str]) -> None:
    """
    Recursively walk a JATS body element, collecting:
      - <title>  → section heading
      - <p>      → paragraph text
      - <sec>    → recurse (container only, no itertext on the sec itself)

    All other tags (disp-formula, boxed-text, list, etc.) are collected as
    paragraphs only if they are direct text-bearing leaves.
    """
    for child in element:
        if not callable(getattr(child, "itertext", None)):
            continue
        tag = _strip_ns(child.tag) if isinstance(child.tag, str) else ""

        if tag == "sec":
            # Container — recurse without emitting the sec's own itertext
            _collect_jats_body(child, parts)
        elif tag == "title":
            text = "".join(child.itertext()).strip()
            if text:
                parts.append(text)
        elif tag == "p":
            text = "".join(child.itertext()).strip()
            if text:
                parts.append(text)
        elif tag in ("list", "def-list"):
            # Emit list items as individual lines
            for item in child.iter("p", "term", "def"):
                text = "".join(item.itertext()).strip()
                if text:
                    parts.append(text)
        # Intentionally skip: disp-formula, inline-formula, xref, ext-link, etc.


def fuzzy_snippet_check(snippet: str, source_text: str, threshold: float = 0.75) -> bool:
    """
    Check whether a snippet plausibly came from source_text using fuzzy matching.
    Returns True if sufficiently similar region is found.
    Efficient for large texts: checks sliding windows of similar length.
    """
    if not snippet or not source_text:
        return False

    snippet = snippet.strip()
    if len(snippet) < 10:
        return True  # too short to verify meaningfully

    # Quick exact check first
    if snippet.lower() in source_text.lower():
        return True

    # Sliding window fuzzy check
    window = len(snippet)
    step = max(window // 2, 50)
    for i in range(0, max(1, len(source_text) - window + 1), step):
        candidate = source_text[i : i + window + 50]  # slight overrun
        ratio = difflib.SequenceMatcher(None, snippet.lower(), candidate.lower()).ratio()
        if ratio >= threshold:
            return True

    return False


def find_snippet_offsets(snippet: str, source_text: str) -> Optional[tuple[int, int]]:
    """
    Find the character offsets of a snippet within source_text.
    Returns (start, end) or None if not found.
    """
    pos = source_text.lower().find(snippet.lower().strip())
    if pos >= 0:
        return pos, pos + len(snippet)
    return None


def normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace, normalize line endings."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _RE_MULTILINE.sub("\n\n", text)
    text = _RE_HORIZ_SPACE.sub(" ", text)
    return text.strip()
