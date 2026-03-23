"""
SIGNOR interaction fetcher.

Downloads the human SIGNOR interaction dataset and yields
RawSignorInteraction records.

SIGNOR (SIGnaling Network Open Resource) is a manually curated causal
interaction database. Every record has explicit direction (A acts on B),
a mechanism (phosphorylation, binding, etc.), and an effect qualifier
(up-regulates / down-regulates / form complex). This maps cleanly onto the
schema's interaction_type + direction + effect fields.

Download URL: https://signor.uniroma2.it/getData.php?organism=9606
Format: tab-delimited, no header row, 29 columns.

Column layout (0-indexed):
  0  ENTITYA name
  1  TYPEA      (protein | complex | mirna | chemical | phenotype | ...)
  2  IDA        (UniProt accession or SIGNOR internal ID)
  3  DATABASEA  (UNIPROT | SIGNOR | ...)
  4  ENTITYB name
  5  TYPEB
  6  IDB
  7  DATABASEB
  8  EFFECT     (up-regulates activity | down-regulates | form complex | ...)
  9  MECHANISM  (phosphorylation | binding | transcriptional regulation | ...)
  12 TAX_ID
  13 CELL_DATA
  14 TISSUE_DATA
  21 PAPERPUBMEDID
  22 DIRECT     (t | f)
  26 SIGNOR_ID  (unique record ID, e.g. SIGNOR-109555)
  27 SCORE      (float 0–1, manual curation confidence)
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

_SIGNOR_URL = "https://signor.uniroma2.it/getData.php?organism=9606"

def _strip_accession(raw: str) -> str:
    """Normalise a SIGNOR accession field to a bare UniProt ID.

    SIGNOR stores plain accessions (P04637) but occasionally appends isoform
    suffixes (-2, -3) or proteolytic-chain annotations (-PRO_xxxxxxx). Strip
    everything after the first hyphen so the ID resolves cleanly via UniProt.
    """
    acc = raw.strip().upper()
    hyphen = acc.find("-")
    return acc[:hyphen] if hyphen != -1 else acc


# ---------------------------------------------------------------------------
# Mechanism → (interaction_type, effect) — used for the effect column value
# ---------------------------------------------------------------------------
_MECHANISM_MAP: dict[str, tuple[str, str]] = {
    "phosphorylation":                  ("post_translational", "phosphorylates"),
    "dephosphorylation":                ("post_translational", "dephosphorylates"),
    "ubiquitination":                   ("post_translational", "ubiquitinates"),
    "deubiquitination":                 ("post_translational", "deubiquitinates"),
    "acetylation":                      ("post_translational", "acetylates"),
    "deacetylation":                    ("post_translational", "deacetylates"),
    "methylation":                      ("post_translational", "methylates"),
    "demethylation":                    ("post_translational", "demethylates"),
    "sumoylation":                      ("post_translational", "sumoylates"),
    "neddylation":                      ("post_translational", "neddylates"),
    "glycosylation":                    ("post_translational", "glycosylates"),
    "cleavage":                         ("post_translational", "cleaves"),
    "palmitoylation":                   ("post_translational", "modifies"),
    "lipid modification":               ("post_translational", "modifies"),
    "s-nitrosylation":                  ("post_translational", "modifies"),
    "oxidation":                        ("post_translational", "modifies"),
    "hydroxylation":                    ("post_translational", "modifies"),
    "stabilization":                    ("post_translational", "stabilizes"),
    "destabilization":                  ("post_translational", "destabilizes"),
    "binding":                          ("direct_binding",    "binds"),
    "transcriptional regulation":       ("transcriptional",   "regulates"),
    "post transcriptional regulation":  ("translational",     "regulates"),
    "relocalization":                   ("transport",         "localizes"),
    "small molecule catalysis":         ("enzymatic",         "catalyzes"),
    "guanine nucleotide exchange factor": ("enzymatic",       "catalyzes"),
    "gtpase-activating protein":        ("enzymatic",         "catalyzes"),
    "chemical activation":              ("signaling",         "activates"),
    "chemical inhibition":              ("signaling",         "inhibits"),
    "miRNA":                            ("translational",     "regulates"),
}

# For generic mechanisms, refine the effect using the EFFECT field
_GENERIC_MECHANISMS = frozenset({
    "binding", "transcriptional regulation", "post transcriptional regulation",
    "relocalization", "chemical activation", "chemical inhibition", "miRNA",
})

# EFFECT field → activation sense (used to refine generic mechanism effects)
def _effect_sense(effect: str) -> Optional[str]:
    """Return 'activates', 'inhibits', 'binds', or None from SIGNOR EFFECT string."""
    el = effect.lower()
    if "form complex" in el:
        return "binds"
    if "up-regulates" in el:
        return "activates"
    if "down-regulates" in el:
        return "inhibits"
    return None


@dataclass
class RawSignorInteraction:
    acc_a: str              # UniProt accession of entity A
    acc_b: str              # UniProt accession of entity B
    signor_id: str          # SIGNOR record ID — used as virtual paper ID suffix
    interaction_type: str   # maps to InteractionType enum value
    effect: str             # maps to Interaction.effect
    direction: str          # A_to_B | undirected
    mechanism: str          # raw SIGNOR mechanism field
    raw_effect: str         # raw SIGNOR effect field
    score: float            # manual curation confidence score (0–1)
    cell_data: str = ""
    tissue_data: str = ""
    direct: bool = True


class SignorInteractionFetcher:
    """
    Downloads and parses the SIGNOR human interaction dataset.
    Only yields records where both interactors are UniProt proteins.
    """

    def __init__(
        self,
        url: str = _SIGNOR_URL,
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

    def iter_interactions(self) -> Iterator[RawSignorInteraction]:
        resp = self._open_stream()
        if resp is None:
            logger.error("[SIGNOR] Could not download interaction file.")
            return

        logger.info(f"[SIGNOR] Streaming {self._url}")
        line_count = 0
        skip_count = 0

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line_count += 1

            result = self._parse_line(raw_line)
            if result is None:
                skip_count += 1
                continue
            yield result

            if line_count % 5_000 == 0:
                logger.info(f"[SIGNOR] Processed {line_count} lines (skipped {skip_count})...")

        resp.close()
        logger.info(f"[SIGNOR] Done. {line_count} lines, {skip_count} skipped.")

    def _parse_line(self, line: str) -> Optional[RawSignorInteraction]:
        cols = line.split("\t")
        if len(cols) < 28:
            return None

        type_a = cols[1].strip().lower()
        type_b = cols[5].strip().lower()
        db_a   = cols[3].strip().upper()
        db_b   = cols[7].strip().upper()

        # Only handle protein-protein with UniProt IDs
        if type_a != "protein" or type_b != "protein":
            return None
        if db_a != "UNIPROT" or db_b != "UNIPROT":
            return None

        acc_a = _strip_accession(cols[2])
        acc_b = _strip_accession(cols[6])
        if not acc_a or not acc_b or acc_a == acc_b:
            return None

        raw_effect    = cols[8].strip()
        mechanism_raw = cols[9].strip().lower()
        cell_data     = cols[13].strip()
        tissue_data   = cols[14].strip()
        direct        = cols[22].strip().lower() == "t"
        signor_id     = cols[26].strip()
        score_raw     = cols[27].strip()

        if not signor_id:
            return None

        try:
            score = float(score_raw) if score_raw else 0.7
        except ValueError:
            score = 0.7

        itype, effect = self._map_type_effect(mechanism_raw, raw_effect)
        direction = "undirected" if itype == "direct_binding" else "A_to_B"

        return RawSignorInteraction(
            acc_a=acc_a,
            acc_b=acc_b,
            signor_id=signor_id,
            interaction_type=itype,
            effect=effect,
            direction=direction,
            mechanism=mechanism_raw,
            raw_effect=raw_effect,
            score=min(max(score, 0.0), 1.0),
            cell_data=cell_data,
            tissue_data=tissue_data,
            direct=direct,
        )

    def _map_type_effect(self, mechanism: str, raw_effect: str) -> tuple[str, str]:
        """Return (interaction_type, effect) from mechanism + effect fields."""
        base = _MECHANISM_MAP.get(mechanism)

        if base is None:
            # Unknown mechanism — fall back to effect-based classification
            sense = _effect_sense(raw_effect)
            if sense == "binds":
                return "direct_binding", "binds"
            elif sense == "activates":
                return "signaling", "activates"
            elif sense == "inhibits":
                return "signaling", "inhibits"
            return "unknown", "binds"

        itype, effect = base

        # For generic mechanisms, refine the effect using the EFFECT qualifier
        if mechanism in _GENERIC_MECHANISMS:
            sense = _effect_sense(raw_effect)
            if sense == "activates":
                effect = "activates"
            elif sense == "inhibits":
                effect = "inhibits"
            elif sense == "binds":
                itype, effect = "direct_binding", "binds"

        return itype, effect

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
                    logger.error(f"[SIGNOR] Download failed: {e}")
                    return None
        return None
