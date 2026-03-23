"""
UniProt binary interaction fetcher.

Fetches IntAct-curated binary protein-protein interactions for Swiss-Prot
reviewed human proteins via the UniProt REST API.

Each interaction yields a RawUniProtInteraction. The set is de-duplicated
across pages (UniProt reports each pair from both proteins' pages).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"
_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


@dataclass
class RawUniProtInteraction:
    acc_a: str           # UniProt accession of interactant one
    acc_b: str           # UniProt accession of interactant two
    intact_id_a: str     # IntAct EBI ID for interactant one (used as virtual paper ID)
    intact_id_b: str     # IntAct EBI ID for interactant two
    n_experiments: int   # number of supporting IntAct experiments


def _experiments_to_confidence(n: int) -> tuple[float, str]:
    """Return (confidence_score, confidence_label) based on experiment count."""
    if n >= 5:
        return 0.90, "high"
    if n >= 3:
        return 0.80, "high"
    if n >= 2:
        return 0.70, "high"
    return 0.55, "medium"


class UniProtInteractionFetcher:
    """
    Streams de-duplicated binary interactions from UniProt/IntAct for
    Swiss-Prot reviewed human proteins.
    """

    def __init__(
        self,
        requests_per_second: float = 2.0,
        timeout: int = 30,
        max_retries: int = 3,
        page_size: int = 500,
    ):
        self._limiter = RateLimiter(requests_per_second)
        self._timeout = timeout
        self._max_retries = max_retries
        self._page_size = page_size
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "AutoBioResearch/0.1 (interaction seeder)",
            "Accept": "application/json",
        })

    def iter_interactions(self) -> Iterator[RawUniProtInteraction]:
        """Yield de-duplicated RawUniProtInteraction records."""
        seen: set[frozenset] = set()

        url: Optional[str] = _SEARCH_URL
        params: Optional[dict] = {
            "query": "reviewed:true AND organism_id:9606 AND cc_interaction:*",
            "fields": "accession,cc_interaction",
            "format": "json",
            "size": str(self._page_size),
        }
        page = 0

        while url:
            page += 1
            logger.info(f"[UniProt] Fetching page {page}...")
            data = self._fetch_page(url, params)
            if data is None:
                logger.error(f"[UniProt] Failed to fetch page {page}, stopping.")
                break

            results = data.get("results", [])
            logger.info(f"[UniProt] Page {page}: {len(results)} entries")

            for entry in results:
                acc_a = entry.get("primaryAccession", "")
                if not acc_a:
                    continue

                for comment in entry.get("comments", []):
                    if comment.get("commentType") != "INTERACTION":
                        continue
                    for ix in comment.get("interactions", []):
                        if ix.get("organismDiffer"):
                            continue  # skip inter-species

                        one = ix.get("interactantOne", {})
                        two = ix.get("interactantTwo", {})
                        b = two.get("uniProtKBAccession", "")
                        if not b:
                            continue

                        if acc_a == b:
                            continue  # skip self-interactions

                        pair_key = frozenset({acc_a, b})
                        if pair_key in seen:
                            continue
                        seen.add(pair_key)

                        yield RawUniProtInteraction(
                            acc_a=acc_a,
                            acc_b=b,
                            intact_id_a=one.get("intActId", f"noid-{acc_a}"),
                            intact_id_b=two.get("intActId", f"noid-{b}"),
                            n_experiments=ix.get("numberOfExperiments", 1),
                        )

            # Follow pagination via Link header
            link_header = data.get("_link_header", "")
            match = _LINK_RE.search(link_header)
            if match:
                url = match.group(1)
                params = None   # next URL already contains all params
            else:
                break

        logger.info(f"[UniProt] Done. Seen {len(seen)} unique pairs across {page} pages.")

    def _fetch_page(self, url: str, params: Optional[dict]) -> Optional[dict]:
        for attempt in range(self._max_retries):
            try:
                self._limiter.acquire()
                resp = self._session.get(url, params=params, timeout=self._timeout)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 10))
                    logger.debug(f"UniProt 429 — waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                # Attach Link header so iter_interactions can follow pagination
                data["_link_header"] = resp.headers.get("Link", "")
                return data
            except requests.RequestException as e:
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"UniProt page fetch failed: {e}")
                    return None
        return None
