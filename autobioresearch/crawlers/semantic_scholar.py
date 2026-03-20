"""
Semantic Scholar crawler using the public Graph API.
No API key required for low-volume use.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from autobioresearch.crawlers.base import BaseCrawler
from autobioresearch.models import FetchStatus, Paper
from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,title,abstract,authors,year,externalIds,journal,openAccessPdf"


class SemanticScholarCrawler(BaseCrawler):
    def __init__(self, requests_per_second: float = 1.0, timeout: int = 30):
        self._limiter = RateLimiter(requests_per_second)
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "AutoBioResearch/0.1"

    def name(self) -> str:
        return "semantic_scholar"

    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        self._limiter.acquire()
        try:
            resp = self._session.get(
                f"{BASE_URL}/paper/search",
                params={"query": query, "limit": min(max_results, 100), "fields": FIELDS},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Semantic Scholar search failed for '{query}': {e}")
            return []

        papers: list[Paper] = []
        for item in data.get("data", []):
            paper = self._parse_item(item)
            if paper:
                papers.append(paper)
        return papers

    def _parse_item(self, item: dict) -> Optional[Paper]:
        s2_id = item.get("paperId")
        if not s2_id:
            return None

        external = item.get("externalIds") or {}
        doi = external.get("DOI")
        pmid = external.get("PubMed")

        # Prefer pmid-prefixed ID if available to avoid duplicating PubMed papers
        if pmid:
            paper_id = f"pmid:{pmid}"
            source = "pubmed"  # treat as pubmed so dedup works
        else:
            paper_id = f"s2:{s2_id}"
            source = "semantic_scholar"

        abstract = item.get("abstract")
        title = item.get("title")
        year = item.get("year")

        authors = [
            a.get("name", "") for a in (item.get("authors") or []) if a.get("name")
        ]

        journal_info = item.get("journal") or {}
        journal = journal_info.get("name")

        open_access = item.get("openAccessPdf") or {}
        has_oa_pdf = bool(open_access.get("url"))

        return Paper(
            id=paper_id,
            source=source,
            title=title,
            abstract=abstract,
            authors=authors,
            journal=journal,
            year=year,
            doi=doi,
            fetch_status=FetchStatus.ABSTRACT_ONLY,
        )
