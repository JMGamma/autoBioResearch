"""
Semantic Scholar crawler using the public Graph API.
No API key required for low-volume use.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from autobioresearch.crawlers.base import BaseCrawler
from autobioresearch.models import FetchStatus, Paper
from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,title,abstract,authors,year,externalIds,journal,openAccessPdf"
_MAX_RETRIES = 4
_BASE_BACKOFF = 2.0


class SemanticScholarCrawler(BaseCrawler):
    def __init__(
        self,
        requests_per_second: float = 1.0,
        semantic_scholar_api_key: Optional[str] = None,
        timeout: int = 30,
    ):
        self._limiter = RateLimiter(requests_per_second)
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "AutoBioResearch/0.1"
        if semantic_scholar_api_key:
            self._session.headers["x-api-key"] = semantic_scholar_api_key

    def name(self) -> str:
        return "semantic_scholar"

    def _request_with_retry(self, url: str, params: dict) -> requests.Response:
        """GET with exponential backoff on 429 / 5xx, honouring Retry-After."""
        for attempt in range(_MAX_RETRIES + 1):
            self._limiter.acquire()
            resp = self._session.get(url, params=params, timeout=self._timeout)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else _BASE_BACKOFF * (2 ** attempt)
                logger.warning(
                    "Semantic Scholar 429 rate-limit "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES + 1}). "
                    f"Waiting {wait:.1f}s before retry... "
                    f"headers={{Retry-After={resp.headers.get('Retry-After')}, "
                    f"X-RateLimit-Remaining={resp.headers.get('X-RateLimit-Remaining')}, "
                    f"X-RateLimit-Reset={resp.headers.get('X-RateLimit-Reset')}}}"
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                wait = _BASE_BACKOFF * (2 ** attempt)
                logger.warning(
                    "Semantic Scholar "
                    f"{resp.status_code} server error (attempt {attempt + 1}/{_MAX_RETRIES + 1}). "
                    f"Waiting {wait:.1f}s before retry..."
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        resp.raise_for_status()
        return resp

    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        try:
            resp = self._request_with_retry(
                f"{BASE_URL}/paper/search",
                {"query": query, "limit": min(max_results, 100), "fields": FIELDS},
            )
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
