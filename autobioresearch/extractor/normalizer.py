"""
Entity normalizer: resolves extracted entity names to canonical DB entities.

Resolution order:
  1. Exact synonym match      — cache → DB entity_synonyms table
  2. External resolver        — UniProt (proteins/genes) or ChEBI (molecules/metabolites)
                                enriches the name set, then retries exact match
  3. Fuzzy match              — difflib against all known synonyms, cross-type for gene/protein
  4. Create new               — last resort; new UUID assigned
"""
from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Optional

import yaml

from autobioresearch.crawlers.entity_resolvers import EntityResolver
from autobioresearch.models import BiologicalEntity, EntityType, ExtractedEntityRaw
from autobioresearch.storage.repositories import EntityRepo

logger = logging.getLogger(__name__)


# Entity types that share a biological identity and should be fuzzy-matched
# across type boundaries. Gene and its protein product are often named
# identically (TP53 / p53) and must resolve to the same entity.
_CROSS_TYPE_GROUPS: list[frozenset[str]] = [
    frozenset({"gene", "protein"}),
]


class EntityNormalizer:
    def __init__(
        self,
        entity_repo: EntityRepo,
        fuzzy_threshold: float = 0.92,
        synonyms_path: str = "config/synonyms.yaml",
        entity_resolver: Optional[EntityResolver] = None,
    ):
        self._repo = entity_repo
        self._threshold = fuzzy_threshold
        self._resolver = entity_resolver   # None → external resolution disabled
        self._synonym_cache: dict[str, str] = {}  # synonym_lower -> entity_id
        # Maps entity_type -> list of (name_lower, entity_id) covering BOTH
        # canonical names and all registered synonyms so fuzzy matching is
        # comprehensive rather than only checking the canonical string.
        self._fuzzy_cache: dict[str, list[tuple[str, str]]] = {}
        self._load_seeded_aliases(synonyms_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(self, raw: ExtractedEntityRaw) -> str:
        """
        Resolve a raw extracted entity to a canonical entity_id.
        Creates a new entity if no match found.
        Returns entity_id.
        """
        names_to_check = [raw.name] + raw.synonyms

        # 1. Exact synonym match (cache first, then DB)
        entity_id = self._exact_match(names_to_check, raw.entity_type.value)
        if entity_id:
            return entity_id

        # 2. External resolver (UniProt / ChEBI) — enriches the synonym set,
        #    then retries exact match with the expanded name list.
        resolved = None
        if self._resolver:
            try:
                resolved = self._resolver.resolve(raw.name, raw.entity_type.value)
            except Exception as e:
                logger.warning(f"Entity resolver error for '{raw.name}': {e}")
            if resolved:
                # Merge resolver synonyms into our working name list (dedup)
                existing_lower = {n.lower() for n in names_to_check}
                extra = [
                    s for s in resolved.synonyms
                    if s.lower() not in existing_lower
                ]
                if extra:
                    names_to_check = names_to_check + extra

                # Retry exact match — one of the resolver's aliases may already
                # be registered in the DB under a different paper's entity.
                entity_id = self._exact_match(names_to_check, raw.entity_type.value)
                if entity_id:
                    self._repo.add_synonyms(entity_id, names_to_check, source="uniprot_resolved")
                    self._update_cache(names_to_check, entity_id, raw.entity_type.value)
                    return entity_id

                # Include accession (e.g. P04637, CHEBI:15355) as a synonym
                if resolved.accession:
                    names_to_check.append(resolved.accession)

        # 3. Fuzzy match against all synonyms of same type (+ gene/protein cross-type)
        entity_id = self._fuzzy_match(raw.name, raw.entity_type)
        if entity_id:
            self._repo.add_synonyms(entity_id, names_to_check)
            self._update_cache(names_to_check, entity_id, entity_type=raw.entity_type.value)
            return entity_id

        # 4. Create new entity — prefer resolver's canonical name over the LLM's
        #    raw string (e.g. "TP53" from UniProt beats "tumor suppressor p53")
        if resolved and resolved.canonical_name:
            raw = ExtractedEntityRaw(
                name=resolved.canonical_name,
                entity_type=raw.entity_type,
                synonyms=list({s for s in names_to_check if s != resolved.canonical_name}),
                organism=resolved.organism or raw.organism,
            )
        return self._create_entity(raw)

    def _exact_match(self, names: list[str], entity_type: str) -> Optional[str]:
        """Check all names against the synonym table. Merges new synonyms on hit."""
        for name in names:
            entity_id = self._lookup_synonym(name)
            if entity_id:
                new_syns = [n for n in names if n.lower() != name.lower()]
                if new_syns:
                    self._repo.add_synonyms(entity_id, new_syns)
                    self._update_cache(new_syns, entity_id, entity_type=entity_type)
                return entity_id
        return None

    def set_repo(self, entity_repo: EntityRepo):
        """Swap in a fresh EntityRepo (new DB connection) while preserving caches."""
        self._repo = entity_repo

    def rebuild_cache(self):
        """Reload synonym->entity_id mapping from DB (call after bulk inserts)."""
        self._synonym_cache.clear()
        self._fuzzy_cache.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _lookup_synonym(self, name: str) -> Optional[str]:
        key = name.lower().strip()
        if key in self._synonym_cache:
            return self._synonym_cache[key]
        entity_id = self._repo.find_by_synonym(key)
        if entity_id:
            self._synonym_cache[key] = entity_id
        return entity_id

    def _fuzzy_match(self, name: str, entity_type: EntityType) -> Optional[str]:
        """
        Fuzzy match `name` against all known names (canonical + synonyms) for
        entities of the given type, and also against cross-type partners
        (gene ↔ protein). Returns entity_id of the best match above threshold.
        """
        # Collect all type keys to search: primary type + any cross-type partners
        type_keys: list[str] = [entity_type.value]
        for group in _CROSS_TYPE_GROUPS:
            if entity_type.value in group:
                type_keys.extend(t for t in group if t != entity_type.value)

        name_lower = name.lower().strip()
        best_ratio = 0.0
        best_id: Optional[str] = None

        for type_key in type_keys:
            if type_key not in self._fuzzy_cache:
                # Load all synonyms for this type — this is richer than canonical
                # names alone and catches aliases that don't appear as the canonical.
                self._fuzzy_cache[type_key] = self._repo.get_all_synonyms_for_type(type_key)

            for candidate, eid in self._fuzzy_cache[type_key]:
                ratio = difflib.SequenceMatcher(None, name_lower, candidate.lower()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_id = eid

        if best_ratio >= self._threshold:
            logger.debug(
                f"Fuzzy matched '{name}' ({entity_type}) -> entity {best_id} "
                f"(ratio={best_ratio:.3f})"
            )
            return best_id
        return None

    def _create_entity(self, raw: ExtractedEntityRaw) -> str:
        entity = BiologicalEntity(
            canonical_name=raw.name,
            display_name=raw.name,
            entity_type=raw.entity_type,
            synonyms=raw.synonyms,
            organism=raw.organism,
        )
        entity_id = self._repo.insert(entity)

        all_names = [raw.name] + raw.synonyms
        self._repo.add_synonyms(entity_id, all_names, source="llm_extracted")
        self._update_cache(all_names, entity_id, entity_type=raw.entity_type.value)
        logger.debug(f"Created new entity: '{raw.name}' ({raw.entity_type}) -> {entity_id}")
        return entity_id

    def _update_cache(self, names: list[str], entity_id: str, entity_type: Optional[str] = None):
        for name in names:
            key = name.lower().strip()
            if not key:
                continue
            self._synonym_cache[key] = entity_id
            # Keep the fuzzy cache live so entities created earlier in a cycle
            # are candidates for papers processed later in the same cycle.
            if entity_type and entity_type in self._fuzzy_cache:
                if (key, entity_id) not in self._fuzzy_cache[entity_type]:
                    self._fuzzy_cache[entity_type].append((key, entity_id))

    def _load_seeded_aliases(self, path: str):
        """Pre-load known_aliases from synonyms.yaml into cache and DB."""
        yaml_path = Path(path)
        if not yaml_path.exists():
            logger.debug(f"Synonyms file not found: {yaml_path}")
            return

        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to load synonyms.yaml: {e}")
            return

        for entry in data.get("known_aliases", []):
            canonical = entry.get("canonical", "").strip()
            aliases = entry.get("aliases", [])
            entity_type_str = entry.get("entity_type", "unknown")
            organism = entry.get("organism")

            if not canonical:
                continue

            # Check if entity already exists
            existing_id = self._lookup_synonym(canonical)
            if existing_id:
                # Add any new aliases
                self._repo.add_synonyms(existing_id, aliases, source="seeded")
                self._update_cache(aliases + [canonical], existing_id, entity_type=entity_type_str)
                continue

            # Create seeded entity
            try:
                entity_type = EntityType(entity_type_str)
            except ValueError:
                entity_type = EntityType.UNKNOWN

            raw = ExtractedEntityRaw(
                name=canonical,
                entity_type=entity_type,
                synonyms=aliases,
                organism=organism,
            )
            entity_id = self._create_entity(raw)
            self._repo.add_synonyms(entity_id, aliases, source="seeded")
            self._update_cache(aliases + [canonical], entity_id)
