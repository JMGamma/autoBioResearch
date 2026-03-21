"""
External entity resolution via UniProt and ChEBI.

UniProt  — proteins and genes (Swiss-Prot manually reviewed, mammalian filter)
ChEBI    — small molecules and metabolites (EBI OLS4 search)

Both resolvers:
  - Cache results in memory to avoid redundant API calls across papers
  - Return None gracefully on network failure (never crash the normalizer)
  - Use independent rate limiters (no shared budget with NCBI/PubMed)
  - Require no API keys
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mammalian taxon IDs — used to filter UniProt results to relevant organisms
# ---------------------------------------------------------------------------
_MAMMALIAN_TAXON_IDS: frozenset[int] = frozenset({
    9606,    # Homo sapiens
    10090,   # Mus musculus
    10116,   # Rattus norvegicus
    9913,    # Bos taurus
    9823,    # Sus scrofa
    9615,    # Canis lupus familiaris
    9544,    # Macaca mulatta
    9986,    # Oryctolagus cuniculus
    10036,   # Mesocricetus auratus
    9685,    # Felis catus
    9796,    # Equus caballus
    9483,    # Callithrix jacchus
    9940,    # Ovis aries
    30521,   # Macaca fascicularis
})


@dataclass
class ResolvedEntity:
    """Canonical identity returned by an external resolver."""
    canonical_name: str           # official symbol / preferred name
    accession: str                # UniProt ID (P04637) or ChEBI ID (CHEBI:15355)
    synonyms: list[str] = field(default_factory=list)
    organism: Optional[str] = None
    source: str = ""              # "uniprot" | "chebi"


# ---------------------------------------------------------------------------
# UniProt resolver  (proteins + genes)
# ---------------------------------------------------------------------------

_UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"
_UNIPROT_FIELDS = "accession,gene_names,protein_name,organism_name,organism_id"


class UniProtResolver:
    """
    Resolves a protein/gene name to its UniProt canonical symbol and all known
    synonyms. Searches Swiss-Prot (reviewed=true) first; falls back to TrEMBL
    if no reviewed mammalian entry is found.

    Results are cached by lowercased query name for the lifetime of the process.
    """

    def __init__(
        self,
        requests_per_second: float = 3.0,
        timeout: int = 10,
        max_retries: int = 2,
    ):
        self._limiter = RateLimiter(requests_per_second)
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "AutoBioResearch/0.1 (entity resolution)",
            "Accept": "application/json",
        })
        # cache: query_lower -> ResolvedEntity | None
        self._cache: dict[str, Optional[ResolvedEntity]] = {}

    def resolve(self, name: str) -> Optional[ResolvedEntity]:
        """
        Look up `name` in UniProt. Returns a ResolvedEntity with the official
        symbol and all synonyms, or None if not found.
        """
        key = name.lower().strip()
        if key in self._cache:
            return self._cache[key]

        result = (
            self._search(name, reviewed=True)
            or self._search(name, reviewed=False)
        )
        self._cache[key] = result
        if result:
            logger.debug(
                f"UniProt resolved '{name}' -> {result.accession} "
                f"({result.canonical_name}, {len(result.synonyms)} synonyms)"
            )
        return result

    def _search(self, name: str, reviewed: bool) -> Optional[ResolvedEntity]:
        """Run one UniProt search pass and return the best mammalian hit."""
        # Try exact gene name first, then broader protein name search
        for query in (
            f'gene_exact:"{name}" AND reviewed:{"true" if reviewed else "false"}',
            f'protein_name:"{name}" AND reviewed:{"true" if reviewed else "false"}',
        ):
            result = self._fetch(query)
            if result:
                return result
        return None

    def _fetch(self, query: str) -> Optional[ResolvedEntity]:
        params = {
            "query": query,
            "fields": _UNIPROT_FIELDS,
            "format": "json",
            "size": "10",   # fetch up to 10, pick best mammalian
        }
        for attempt in range(self._max_retries + 1):
            try:
                self._limiter.acquire()
                resp = self._session.get(
                    _UNIPROT_SEARCH, params=params, timeout=self._timeout
                )
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 5))
                    logger.debug(f"UniProt 429 — waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return self._parse(resp.json())
            except requests.RequestException as e:
                if attempt < self._max_retries:
                    time.sleep(2 ** attempt)
                else:
                    logger.debug(f"UniProt request failed after retries: {e}")
                    return None
        return None

    def _parse(self, data: dict) -> Optional[ResolvedEntity]:
        results = data.get("results", [])
        # Prefer mammalian entries; among those prefer human
        mammalian = [
            r for r in results
            if r.get("organism", {}).get("taxonId") in _MAMMALIAN_TAXON_IDS
        ]
        candidates = mammalian or results
        if not candidates:
            return None

        # Pick human (9606) first, then any mammal
        entry = next(
            (r for r in candidates if r.get("organism", {}).get("taxonId") == 9606),
            candidates[0],
        )

        accession = entry.get("primaryAccession", "")
        organism_name = entry.get("organism", {}).get("scientificName")

        synonyms: list[str] = []
        canonical_name = ""

        # Gene names
        for gene in entry.get("genes", []):
            gn = gene.get("geneName", {}).get("value", "")
            if gn:
                if not canonical_name:
                    canonical_name = gn
                synonyms.append(gn)
            for syn in gene.get("synonyms", []):
                v = syn.get("value", "")
                if v:
                    synonyms.append(v)
            for orf in gene.get("orfNames", []):
                v = orf.get("value", "")
                if v:
                    synonyms.append(v)

        # Protein names
        pd = entry.get("proteinDescription", {})
        rec = pd.get("recommendedName", {})
        full = rec.get("fullName", {}).get("value", "")
        if full:
            if not canonical_name:
                canonical_name = full
            synonyms.append(full)
        for short in rec.get("shortNames", []):
            v = short.get("value", "")
            if v:
                synonyms.append(v)
        for alt in pd.get("alternativeNames", []):
            v = alt.get("fullName", {}).get("value", "")
            if v:
                synonyms.append(v)
            for short in alt.get("shortNames", []):
                sv = short.get("value", "")
                if sv:
                    synonyms.append(sv)

        if not canonical_name and not synonyms:
            return None

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_syns: list[str] = []
        for s in synonyms:
            sl = s.lower()
            if sl not in seen:
                seen.add(sl)
                unique_syns.append(s)

        return ResolvedEntity(
            canonical_name=canonical_name or unique_syns[0],
            accession=accession,
            synonyms=unique_syns,
            organism=organism_name,
            source="uniprot",
        )


# ---------------------------------------------------------------------------
# ChEBI resolver  (small molecules + metabolites)
# ---------------------------------------------------------------------------

_CHEBI_OLS_SEARCH = "https://www.ebi.ac.uk/ols4/api/search"


class ChEBIResolver:
    """
    Resolves a small molecule / metabolite name to its ChEBI canonical label
    and synonyms via the EBI OLS4 API.

    Tries an exact match first, then a relaxed search if nothing is found.
    Results are cached for the lifetime of the process.
    """

    def __init__(
        self,
        requests_per_second: float = 3.0,
        timeout: int = 10,
        max_retries: int = 2,
    ):
        self._limiter = RateLimiter(requests_per_second)
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "AutoBioResearch/0.1 (entity resolution)",
            "Accept": "application/json",
        })
        self._cache: dict[str, Optional[ResolvedEntity]] = {}

    def resolve(self, name: str) -> Optional[ResolvedEntity]:
        key = name.lower().strip()
        if key in self._cache:
            return self._cache[key]

        result = (
            self._search(name, exact=True)
            or self._search(name, exact=False)
        )
        self._cache[key] = result
        if result:
            logger.debug(
                f"ChEBI resolved '{name}' -> {result.accession} "
                f"({result.canonical_name}, {len(result.synonyms)} synonyms)"
            )
        return result

    def _search(self, name: str, exact: bool) -> Optional[ResolvedEntity]:
        params = {
            "q": name,
            "ontology": "chebi",
            "rows": "5",
            "exact": "true" if exact else "false",
            "fieldList": "id,label,synonym,obo_id",
        }
        for attempt in range(self._max_retries + 1):
            try:
                self._limiter.acquire()
                resp = self._session.get(
                    _CHEBI_OLS_SEARCH, params=params, timeout=self._timeout
                )
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 5))
                    logger.debug(f"ChEBI OLS 429 — waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return self._parse(resp.json())
            except requests.RequestException as e:
                if attempt < self._max_retries:
                    time.sleep(2 ** attempt)
                else:
                    logger.debug(f"ChEBI request failed after retries: {e}")
                    return None
        return None

    def _parse(self, data: dict) -> Optional[ResolvedEntity]:
        docs = data.get("response", {}).get("docs", [])
        if not docs:
            return None

        # Prefer the entry whose label most closely matches (OLS returns scored)
        entry = docs[0]
        label = entry.get("label", "")
        obo_id = entry.get("obo_id", entry.get("id", ""))
        raw_synonyms = entry.get("synonym", [])

        if not label:
            return None

        synonyms: list[str] = [label]
        for s in (raw_synonyms if isinstance(raw_synonyms, list) else [raw_synonyms]):
            if s and s.lower() != label.lower():
                synonyms.append(s)

        return ResolvedEntity(
            canonical_name=label,
            accession=obo_id,
            synonyms=synonyms,
            source="chebi",
        )


# ---------------------------------------------------------------------------
# Unified facade used by the normalizer
# ---------------------------------------------------------------------------

class EntityResolver:
    """
    Routes resolution to UniProt (proteins, genes) or ChEBI (molecules,
    metabolites) based on entity type. Returns None for types we don't
    have an external resolver for (pathways, diseases, cell types, etc.).
    """

    _UNIPROT_TYPES = frozenset({"protein", "gene"})
    _CHEBI_TYPES = frozenset({"molecule", "metabolite"})

    def __init__(
        self,
        requests_per_second: float = 3.0,
        timeout: int = 10,
    ):
        self._uniprot = UniProtResolver(requests_per_second, timeout)
        self._chebi = ChEBIResolver(requests_per_second, timeout)

    def resolve(self, name: str, entity_type: str) -> Optional[ResolvedEntity]:
        """
        Resolve `name` using the appropriate external database for `entity_type`.
        Returns None if the type has no resolver or the lookup fails.
        """
        if entity_type in self._UNIPROT_TYPES:
            return self._uniprot.resolve(name)
        if entity_type in self._CHEBI_TYPES:
            return self._chebi.resolve(name)
        return None
