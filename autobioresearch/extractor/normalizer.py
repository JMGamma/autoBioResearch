"""
Entity normalizer: resolves extracted entity names to canonical DB entities.
Strategy: exact synonym match → seeded alias match → fuzzy match → create new.
"""
from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Optional

import yaml

from autobioresearch.models import BiologicalEntity, EntityType, ExtractedEntityRaw
from autobioresearch.storage.repositories import EntityRepo

logger = logging.getLogger(__name__)


class EntityNormalizer:
    def __init__(
        self,
        entity_repo: EntityRepo,
        fuzzy_threshold: float = 0.92,
        synonyms_path: str = "config/synonyms.yaml",
    ):
        self._repo = entity_repo
        self._threshold = fuzzy_threshold
        self._synonym_cache: dict[str, str] = {}  # synonym_lower -> entity_id
        self._canonical_cache: dict[str, list[tuple[str, str]]] = {}  # entity_type -> [(name, id)]
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
        for name in names_to_check:
            entity_id = self._lookup_synonym(name)
            if entity_id:
                # Merge any new synonyms into the existing entity
                new_syns = [n for n in names_to_check if n.lower() != name.lower()]
                if new_syns:
                    self._repo.add_synonyms(entity_id, new_syns)
                    self._update_cache(new_syns, entity_id)
                return entity_id

        # 2. Fuzzy match against canonical names of same type
        entity_id = self._fuzzy_match(raw.name, raw.entity_type)
        if entity_id:
            self._repo.add_synonyms(entity_id, names_to_check)
            self._update_cache(names_to_check, entity_id)
            return entity_id

        # 3. Create new entity
        return self._create_entity(raw)

    def rebuild_cache(self):
        """Reload synonym->entity_id mapping from DB (call after bulk inserts)."""
        self._synonym_cache.clear()
        self._canonical_cache.clear()

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
        type_key = entity_type.value
        if type_key not in self._canonical_cache:
            self._canonical_cache[type_key] = self._repo.get_all_canonical_names(type_key)

        name_lower = name.lower().strip()
        best_ratio = 0.0
        best_id: Optional[str] = None

        for canonical, eid in self._canonical_cache[type_key]:
            ratio = difflib.SequenceMatcher(None, name_lower, canonical.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_id = eid

        if best_ratio >= self._threshold:
            logger.debug(f"Fuzzy matched '{name}' -> entity {best_id} (ratio={best_ratio:.3f})")
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
        self._update_cache(all_names, entity_id)

        # Update canonical cache
        type_key = raw.entity_type.value
        if type_key in self._canonical_cache:
            self._canonical_cache[type_key].append((raw.name, entity_id))

        logger.debug(f"Created new entity: '{raw.name}' ({raw.entity_type}) -> {entity_id}")
        return entity_id

    def _update_cache(self, names: list[str], entity_id: str):
        for name in names:
            self._synonym_cache[name.lower().strip()] = entity_id

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
                self._update_cache(aliases + [canonical], existing_id)
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
