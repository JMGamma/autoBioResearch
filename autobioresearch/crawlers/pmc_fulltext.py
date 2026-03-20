"""
PMC (PubMed Central) full-text fetcher.
Fetches JATS XML transiently — full text is NEVER stored to the database.
Only available for open-access articles with a PMC ID.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from autobioresearch.utils.rate_limiter import RateLimiter
from autobioresearch.utils.text_utils import clean_jats_xml, normalize_whitespace

logger = logging.getLogger(__name__)

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PMCFullTextFetcher:
    def __init__(
        self,
        requests_per_second: float = 2.0,
        ncbi_api_key: Optional[str] = None,
        timeout: int = 60,
    ):
        self._limiter = RateLimiter(requests_per_second)
        self._api_key = ncbi_api_key
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "AutoBioResearch/0.1"

    def fetch(self, pmc_id: str) -> Optional[str]:
        """
        Fetch full text for a PMC article.
        Returns plain text (never stored; caller must use and discard).
        Returns None if the article is not available in PMC Open Access.
        """
        self._limiter.acquire()
        params = {
            "db": "pmc",
            "id": pmc_id,
            "rettype": "xml",
            "retmode": "xml",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        try:
            resp = self._session.get(EFETCH_URL, params=params, timeout=self._timeout)
            resp.raise_for_status()
            xml_text = resp.text

            # Quick check: did we get actual article content?
            if "<body" not in xml_text and "<article" not in xml_text:
                logger.debug(f"PMC {pmc_id}: no article body in response")
                return None

            text = clean_jats_xml(xml_text)
            text = normalize_whitespace(text)
            logger.debug(f"PMC {pmc_id}: fetched {len(text):,} chars of full text")
            return text if len(text) > 200 else None

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.debug(f"PMC {pmc_id}: not found in open access (404)")
            else:
                logger.warning(f"PMC {pmc_id}: HTTP error {e}")
            return None
        except Exception as e:
            logger.warning(f"PMC {pmc_id}: fetch failed: {e}")
            return None
