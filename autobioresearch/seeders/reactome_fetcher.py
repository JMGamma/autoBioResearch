"""
Reactome interaction fetcher.

Streams the Reactome homo sapiens PSIMITAB interaction file and yields
RawReactomeInteraction records, one per valid line.

File format: PSIMITAB 2.5 tab-delimited.
Download URL: https://reactome.org/download/current/homo_sapiens.interactions.tab-delimited.txt
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

import requests

from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_REACTOME_URL = (
    "https://reactome.org/download/current/"
    "homo_sapiens.interactions.tab-delimited.txt"
)

# Regex to parse MI terms like: psi-mi:"MI:0915"(physical association)
_MI_RE = re.compile(r'psi-mi:"(MI:\d+)"\((.+?)\)')

# Strip UniProt prefix and isoform suffix: uniprotkb:P04637-2 -> P04637
_ACC_RE = re.compile(r'^uniprotkb:([A-Z0-9]+)(?:-\d+)?$', re.IGNORECASE)

# Map PSI-MI term -> (interaction_type, effect, direction)
_MI_MAP: dict[str, tuple[str, str, str]] = {
    "MI:0407": ("direct_binding",        "binds",             "undirected"),
    "MI:0915": ("proximal_association",  "binds",             "undirected"),
    "MI:0914": ("proximal_association",  "binds",             "undirected"),
    "MI:0217": ("post_translational",    "phosphorylates",    "A_to_B"),
    "MI:0194": ("post_translational",    "cleaves",           "A_to_B"),
    "MI:0220": ("post_translational",    "ubiquitinates",     "A_to_B"),
    "MI:0701": ("post_translational",    "dephosphorylates",  "A_to_B"),
    "MI:0213": ("post_translational",    "methylates",        "A_to_B"),
    "MI:0945": ("post_translational",    "acetylates",        "A_to_B"),
    "MI:0414": ("enzymatic",             "catalyzes",         "A_to_B"),
    "MI:0190": ("metabolic",             "reacts_with",       "undirected"),
    "MI:0569": ("transcriptional",       "regulates",         "A_to_B"),
    "MI:0571": ("post_translational",    "cleaves",           "A_to_B"),
}
_DEFAULT_MI = ("unknown", "binds", "undirected")


@dataclass
class RawReactomeInteraction:
    acc_a: str
    acc_b: str
    mi_term: str
    mi_label: str
    pathway_id: str
    interaction_type: str
    effect: str
    direction: str
    publication_ids: list[str] = field(default_factory=list)


def _parse_accession(raw: str) -> Optional[str]:
    """Extract bare UniProt accession from a PSIMITAB interactor field."""
    raw = raw.strip()
    m = _ACC_RE.match(raw)
    return m.group(1).upper() if m else None


def _parse_mi(raw: str) -> tuple[str, str]:
    """Extract (mi_term, mi_label) from a PSIMITAB interaction type field."""
    m = _MI_RE.search(raw)
    if m:
        return m.group(1), m.group(2)
    return "", raw.strip()


def _parse_pathway_id(raw: str) -> str:
    """Extract Reactome pathway ID from interaction AC field."""
    # Field looks like: reactome:R-HSA-5633007
    for token in raw.split("|"):
        token = token.strip()
        if token.startswith("reactome:"):
            return token[len("reactome:"):]
    return raw.split("|")[0].strip()


class ReactomeInteractionFetcher:
    """
    Streams the Reactome human interaction file and yields
    RawReactomeInteraction records. Skips non-UniProt interactors and
    lines that cannot be parsed.
    """

    def __init__(
        self,
        url: str = _REACTOME_URL,
        requests_per_second: float = 1.0,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        self._url = url
        self._limiter = RateLimiter(requests_per_second)
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "AutoBioResearch/0.1 (interaction seeder)",
        })

    def iter_interactions(self) -> Iterator[RawReactomeInteraction]:
        """Stream and parse the Reactome TSV file line by line."""
        resp = self._open_stream()
        if resp is None:
            logger.error("[Reactome] Could not download interaction file.")
            return

        logger.info(f"[Reactome] Streaming {self._url}")
        line_count = 0
        skip_count = 0

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or raw_line.startswith("#"):
                continue
            line_count += 1

            result = self._parse_line(raw_line)
            if result is None:
                skip_count += 1
                continue
            yield result

            if line_count % 10_000 == 0:
                logger.info(f"[Reactome] Processed {line_count} lines (skipped {skip_count})...")

        resp.close()
        logger.info(f"[Reactome] Done. {line_count} lines, {skip_count} skipped.")

    def _parse_line(self, line: str) -> Optional[RawReactomeInteraction]:
        cols = line.split("\t")
        if len(cols) < 14:
            return None

        acc_a = _parse_accession(cols[0])
        acc_b = _parse_accession(cols[1])
        if not acc_a or not acc_b:
            return None
        if acc_a == acc_b:
            return None  # skip self-interactions

        mi_term, mi_label = _parse_mi(cols[11])
        pathway_id = _parse_pathway_id(cols[13])

        itype, effect, direction = _MI_MAP.get(mi_term, _DEFAULT_MI)

        # Publication IDs (col 8, pipe-separated, e.g. "pubmed:12345|pubmed:67890")
        pub_ids: list[str] = []
        for token in cols[8].split("|"):
            token = token.strip()
            if token.startswith("pubmed:"):
                pub_ids.append(token[len("pubmed:"):])

        return RawReactomeInteraction(
            acc_a=acc_a,
            acc_b=acc_b,
            mi_term=mi_term,
            mi_label=mi_label,
            pathway_id=pathway_id,
            interaction_type=itype,
            effect=effect,
            direction=direction,
            publication_ids=pub_ids,
        )

    def _open_stream(self) -> Optional[requests.Response]:
        for attempt in range(self._max_retries):
            try:
                self._limiter.acquire()
                resp = self._session.get(self._url, stream=True, timeout=self._timeout)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 10))
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[Reactome] Download failed: {e}")
                    return None
        return None
