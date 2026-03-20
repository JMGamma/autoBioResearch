"""
PubMed crawler using NCBI Entrez E-utilities.
No API key required for up to 3 req/s; set NCBI_API_KEY for 10 req/s.

429 handling: both _get and _get_xml retry with exponential backoff, honouring
the Retry-After header that NCBI includes on rate-limit responses.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from autobioresearch.crawlers.base import BaseCrawler
from autobioresearch.models import FetchStatus, Paper
from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

_MAX_RETRIES = 4
_BASE_BACKOFF = 2.0   # seconds — doubles each attempt: 2, 4, 8, 16


class PubMedCrawler(BaseCrawler):
    def __init__(
        self,
        requests_per_second: float = 3.0,
        ncbi_api_key: Optional[str] = None,
        timeout: int = 30,
    ):
        rps = 10.0 if ncbi_api_key else requests_per_second
        self._limiter = RateLimiter(rps)
        self._api_key = ncbi_api_key
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "AutoBioResearch/0.1 (mailto:contact@example.com)"

    def name(self) -> str:
        return "pubmed"

    def _params(self, extra: dict) -> dict:
        p = {"retmode": "json", **extra}
        if self._api_key:
            p["api_key"] = self._api_key
        return p

    def _request_with_retry(self, url: str, params: dict) -> requests.Response:
        """GET with exponential backoff on 429 / 5xx, honouring Retry-After."""
        for attempt in range(_MAX_RETRIES + 1):
            self._limiter.acquire()
            resp = self._session.get(url, params=params, timeout=self._timeout)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else _BASE_BACKOFF * (2 ** attempt)
                logger.warning(
                    f"PubMed 429 rate-limit (attempt {attempt + 1}/{_MAX_RETRIES + 1}). "
                    f"Waiting {wait:.1f}s before retry..."
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                wait = _BASE_BACKOFF * (2 ** attempt)
                logger.warning(
                    f"PubMed {resp.status_code} server error (attempt {attempt + 1}/{_MAX_RETRIES + 1}). "
                    f"Waiting {wait:.1f}s before retry..."
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        # Final attempt after all retries exhausted
        resp.raise_for_status()
        return resp

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        return self._request_with_retry(url, params).json()

    def _get_xml(self, endpoint: str, params: dict) -> str:
        url = f"{BASE_URL}/{endpoint}"
        params = {k: v for k, v in params.items() if k != "retmode"}
        return self._request_with_retry(url, params).text

    def search_ids(self, query: str, max_results: int = 50) -> list[str]:
        """Return list of PMIDs matching the query."""
        try:
            data = self._get("esearch.fcgi", self._params({
                "db": "pubmed",
                "term": query,
                "retmax": str(max_results),
                "usehistory": "n",
            }))
            return data.get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            logger.warning(f"PubMed esearch failed for '{query}': {e}")
            return []

    def fetch_details(self, pmids: list[str]) -> list[Paper]:
        """Batch fetch paper metadata for a list of PMIDs."""
        if not pmids:
            return []

        # Fetch in batches of 200
        papers: list[Paper] = []
        batch_size = 200
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i : i + batch_size]
            try:
                xml_text = self._get_xml("efetch.fcgi", {
                    "db": "pubmed",
                    "id": ",".join(batch),
                    "rettype": "abstract",
                    "retmode": "xml",
                })
                papers.extend(self._parse_pubmed_xml(xml_text))
            except Exception as e:
                logger.warning(f"PubMed efetch failed for batch starting {batch[0]}: {e}")

        return papers

    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        """Search and fetch details in one call."""
        pmids = self.search_ids(query, max_results)
        if not pmids:
            return []
        return self.fetch_details(pmids)

    def _parse_pubmed_xml(self, xml_text: str) -> list[Paper]:
        papers: list[Paper] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"PubMed XML parse error: {e}")
            return []

        for article in root.findall(".//PubmedArticle"):
            try:
                paper = self._parse_article(article)
                if paper:
                    papers.append(paper)
            except Exception as e:
                logger.debug(f"Failed to parse PubmedArticle: {e}")

        return papers

    def _parse_article(self, article_el) -> Optional[Paper]:
        mc = article_el.find("MedlineCitation")
        if mc is None:
            return None

        # PMID
        pmid_el = mc.find("PMID")
        if pmid_el is None or not pmid_el.text:
            return None
        paper_id = f"pmid:{pmid_el.text.strip()}"

        art = mc.find("Article")
        if art is None:
            return None

        # Title
        title_el = art.find("ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else None

        # Abstract
        abstract_parts = []
        for text_el in art.findall(".//AbstractText"):
            label = text_el.get("Label", "")
            text = "".join(text_el.itertext()).strip()
            if label:
                abstract_parts.append(f"{label}: {text}")
            elif text:
                abstract_parts.append(text)
        abstract = "\n\n".join(abstract_parts) if abstract_parts else None

        # Authors
        authors = []
        for author in art.findall(".//Author"):
            last = author.findtext("LastName", "")
            fore = author.findtext("ForeName", "")
            if last:
                authors.append(f"{last}, {fore}".strip(", "))

        # Journal
        journal = art.findtext(".//Journal/Title") or art.findtext(".//Journal/ISOAbbreviation")

        # Year
        year = None
        pub_date = art.find(".//PubDate")
        if pub_date is not None:
            year_el = pub_date.find("Year")
            if year_el is not None and year_el.text:
                try:
                    year = int(year_el.text.strip())
                except ValueError:
                    pass

        # DOI
        doi = None
        for id_el in article_el.findall(".//ArticleId"):
            if id_el.get("IdType") == "doi":
                doi = id_el.text.strip() if id_el.text else None
                break

        # PMC ID (for full-text fetching)
        pmc_id = None
        for id_el in article_el.findall(".//ArticleId"):
            if id_el.get("IdType") == "pmc":
                raw = id_el.text.strip() if id_el.text else ""
                pmc_id = raw.replace("PMC", "") if raw.startswith("PMC") else raw
                break

        fetch_status = FetchStatus.FULL_TEXT_AVAILABLE if pmc_id else FetchStatus.ABSTRACT_ONLY

        return Paper(
            id=paper_id,
            source="pubmed",
            title=title,
            abstract=abstract,
            authors=authors,
            journal=journal,
            year=year,
            doi=doi,
            pmc_id=pmc_id,
            fetch_status=fetch_status,
        )
