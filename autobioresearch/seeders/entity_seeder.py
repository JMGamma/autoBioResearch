"""
Entity seeder: look up or create entities given a UniProt accession.

Resolution order:
  1. In-memory cache (hot path)
  2. entity_synonyms table (synonym = accession, source="seeded")
  3. entities.external_ids JSON field  ({"uniprot": "P04637"})
  4. Fetch from UniProt REST API → insert new entity
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

import requests

from autobioresearch.models.entity import BiologicalEntity, EntityType
from autobioresearch.storage.repositories import EntityRepo
from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_UNIPROT_ENTRY = "https://rest.uniprot.org/uniprotkb/{accession}?format=json"


class EntitySeeder:
    """
    Resolves UniProt accessions to DB entity IDs, creating new entities as needed.
    Maintains an in-memory cache for the lifetime of the run.
    """

    def __init__(
        self,
        entity_repo: EntityRepo,
        requests_per_second: float = 2.0,
        timeout: int = 15,
        max_retries: int = 3,
    ):
        self._repo = entity_repo
        self._cache: dict[str, Optional[str]] = {}   # accession -> entity_id | None
        self._limiter = RateLimiter(requests_per_second)
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "AutoBioResearch/0.1 (interaction seeder)",
            "Accept": "application/json",
        })

    def get_or_create(self, accession: str) -> Optional[str]:
        """Return entity_id for the given UniProt accession, creating it if necessary.
        Returns None if the accession cannot be resolved."""
        accession = accession.strip()
        if not accession:
            return None

        if accession in self._cache:
            return self._cache[accession]

        # 1. Synonym lookup (lowercased accession stored as synonym)
        entity_id = self._repo.find_by_synonym(accession)
        if entity_id:
            self._cache[accession] = entity_id
            return entity_id

        # 2. External ID JSON field lookup
        entity_id = self._repo.find_by_external_id("uniprot", accession)
        if entity_id:
            # Register accession as synonym for future fast lookups
            self._repo.add_synonyms(entity_id, [accession], source="seeded")
            self._cache[accession] = entity_id
            return entity_id

        # 3. Fetch from UniProt and create
        data = self._fetch_entry(accession)
        if data is None:
            logger.warning(f"EntitySeeder: could not resolve accession {accession}")
            self._cache[accession] = None
            return None

        # UniProt returns the canonical entry even when an isoform accession is
        # queried (e.g. P49759-3 → primaryAccession = P49759). Check whether we
        # already have the canonical entry so we don't attempt a duplicate insert.
        canonical_acc = data.get("primaryAccession", accession)
        if canonical_acc != accession:
            for lookup in (
                self._cache.get(canonical_acc),
                self._repo.find_by_synonym(canonical_acc),
                self._repo.find_by_external_id("uniprot", canonical_acc),
            ):
                if lookup:
                    self._repo.add_synonyms(lookup, [accession], source="seeded")
                    self._cache[accession] = lookup
                    self._cache[canonical_acc] = lookup
                    return lookup

        entity = self._parse_entry(canonical_acc, data)
        entity_id = self._repo.insert(entity)
        all_synonyms = list(entity.synonyms) + [accession, canonical_acc]
        self._repo.add_synonyms(entity_id, all_synonyms, source="seeded")

        self._cache[accession] = entity_id
        self._cache[canonical_acc] = entity_id
        logger.debug(f"EntitySeeder: created entity {entity.canonical_name} ({canonical_acc}) -> {entity_id}")
        return entity_id

    def _fetch_entry(self, accession: str) -> Optional[dict]:
        url = _UNIPROT_ENTRY.format(accession=accession)
        for attempt in range(self._max_retries):
            try:
                self._limiter.acquire()
                resp = self._session.get(url, timeout=self._timeout)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 5))
                    logger.debug(f"UniProt 429 for {accession} — waiting {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code == 404:
                    logger.debug(f"UniProt 404 for {accession} (obsolete/not found)")
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.warning(f"UniProt fetch failed for {accession}: {e}")
                    return None
        return None

    def _parse_entry(self, accession: str, data: dict) -> BiologicalEntity:
        """Parse a UniProt entry JSON (direct accession format) into a BiologicalEntity."""
        synonyms: list[str] = []
        canonical_name = ""

        # Gene names (primary source for canonical name)
        for gene in data.get("genes", []):
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

        # Protein description (fallback canonical name + additional synonyms)
        pd = data.get("proteinDescription", {})
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

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_syns: list[str] = []
        for s in synonyms:
            sl = s.lower()
            if sl not in seen:
                seen.add(sl)
                unique_syns.append(s)

        if not canonical_name:
            canonical_name = unique_syns[0] if unique_syns else accession

        organism = data.get("organism", {}).get("scientificName")

        return BiologicalEntity(
            id=str(uuid.uuid4()),
            canonical_name=canonical_name,
            display_name=canonical_name,
            entity_type=EntityType.PROTEIN,
            synonyms=unique_syns,
            external_ids={"uniprot": accession},
            organism=organism,
            paper_count=0,
        )
