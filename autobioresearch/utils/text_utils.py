"""
Text processing utilities for paper chunking, JATS XML parsing, and snippet verification.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional


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
    Convert JATS XML to plain readable text.
    Extracts text from body, abstract, and title elements; strips figure/table noise.
    """
    try:
        from lxml import etree

        # Remove figure and table content (distracting for LLM)
        root = etree.fromstring(xml_text.encode(), parser=etree.XMLParser(recover=True))

        for tag in ["fig", "table-wrap", "supplementary-material", "ref-list"]:
            for el in root.iter(tag):
                el.getparent().remove(el)

        # Collect text from useful sections
        sections = []
        for section in root.iter("title", "abstract", "p", "sec"):
            text = "".join(section.itertext()).strip()
            if text:
                sections.append(text)

        return "\n\n".join(sections)

    except Exception:
        # Fallback: strip all XML tags
        text = re.sub(r"<[^>]+>", " ", xml_text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


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
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
