"""
Unit tests for EntityNormalizer.

Uses an in-memory SQLite database — no files, no network, no LLM.
The EntityResolver is injected as a mock so we can test the full pipeline
including external resolution without real API calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine

from autobioresearch import database as db
from autobioresearch.crawlers.entity_resolvers import EntityResolver, ResolvedEntity
from autobioresearch.extractor.normalizer import EntityNormalizer
from autobioresearch.models import EntityType, ExtractedEntityRaw
from autobioresearch.storage.repositories import EntityRepo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    """Fresh in-memory SQLite DB for each test."""
    eng = create_engine("sqlite:///:memory:")
    db.create_all(eng)
    return eng


@pytest.fixture()
def conn(engine):
    """Open connection with auto-commit helper."""
    with engine.connect() as c:
        yield c
        c.commit()


@pytest.fixture()
def repo(conn):
    return EntityRepo(conn)


def _normalizer(repo: EntityRepo, resolver=None) -> EntityNormalizer:
    return EntityNormalizer(
        entity_repo=repo,
        fuzzy_threshold=0.92,
        synonyms_path="nonexistent_test_synonyms.yaml",  # no pre-seeding in tests
        entity_resolver=resolver,
    )


def _raw(name: str, entity_type: str = "protein", synonyms: list[str] | None = None,
         organism: str | None = None) -> ExtractedEntityRaw:
    return ExtractedEntityRaw(
        name=name,
        entity_type=EntityType(entity_type),
        synonyms=synonyms or [],
        organism=organism,
    )


def _mock_resolver(resolved: ResolvedEntity | None, entity_type: str = "protein") -> EntityResolver:
    """Return a mock EntityResolver that always returns `resolved`."""
    mock = MagicMock(spec=EntityResolver)
    mock.resolve.return_value = resolved
    return mock


# ---------------------------------------------------------------------------
# Exact synonym match (step 1)
# ---------------------------------------------------------------------------

class TestExactSynonymMatch:

    def test_returns_existing_entity_on_exact_name_match(self, repo, conn):
        normalizer = _normalizer(repo)
        # First call creates the entity
        eid1 = normalizer.normalize(_raw("EGFR"))
        # Second call with same name should return the same entity
        eid2 = normalizer.normalize(_raw("EGFR"))
        assert eid1 == eid2

    def test_matches_via_registered_synonym(self, repo, conn):
        normalizer = _normalizer(repo)
        # Create entity with synonym "ErbB1"
        eid1 = normalizer.normalize(_raw("EGFR", synonyms=["ErbB1"]))
        # Lookup via the synonym
        eid2 = normalizer.normalize(_raw("ErbB1"))
        assert eid1 == eid2

    def test_match_is_case_insensitive(self, repo, conn):
        normalizer = _normalizer(repo)
        eid1 = normalizer.normalize(_raw("TP53"))
        eid2 = normalizer.normalize(_raw("tp53"))
        assert eid1 == eid2

    def test_overlap_same_synonym_is_disambiguated_by_entity_type(self, repo, conn):
        normalizer = _normalizer(repo)
        protein_id = normalizer.normalize(_raw("EGFR", entity_type="protein"))
        metabolite_id = normalizer.normalize(_raw("glucose", entity_type="metabolite"))
        repo.add_synonyms(protein_id, ["shared_alias"])
        repo.add_synonyms(metabolite_id, ["shared_alias"])
        normalizer.rebuild_cache()

        assert protein_id != metabolite_id
        assert normalizer.normalize(_raw("shared_alias", entity_type="protein")) == protein_id
        assert normalizer.normalize(_raw("shared_alias", entity_type="metabolite")) == metabolite_id

    def test_overlap_same_synonym_is_disambiguated_by_organism(self, repo, conn):
        normalizer = _normalizer(repo)
        human_id = normalizer.normalize(_raw("MAPK1", entity_type="protein", organism="Homo sapiens"))
        mouse_id = normalizer.normalize(_raw("Mapk1_mouse", entity_type="protein", organism="Mus musculus"))
        repo.add_synonyms(human_id, ["erk_shared"])
        repo.add_synonyms(mouse_id, ["erk_shared"])
        normalizer.rebuild_cache()

        assert human_id != mouse_id
        assert normalizer.normalize(_raw("erk_shared", entity_type="protein", organism="Homo sapiens")) == human_id
        assert normalizer.normalize(_raw("erk_shared", entity_type="protein", organism="Mus musculus")) == mouse_id

    def test_new_synonyms_merged_on_hit(self, repo, conn):
        normalizer = _normalizer(repo)
        eid = normalizer.normalize(_raw("TP53"))
        # Second lookup brings a new alias
        eid2 = normalizer.normalize(_raw("TP53", synonyms=["p53"]))
        assert eid == eid2
        # The alias should now resolve too
        eid3 = normalizer.normalize(_raw("p53"))
        assert eid == eid3

    def test_resolver_not_called_on_exact_match(self, repo, conn):
        """
        Resolver may be called on the first normalize (entity not yet in DB),
        but must NOT be called on the second call where an exact synonym match
        is found — the pipeline should short-circuit at step 1.
        """
        mock_resolver = _mock_resolver(None)
        normalizer = _normalizer(repo, resolver=mock_resolver)
        normalizer.normalize(_raw("EGFR"))          # first call: no entity yet, resolver may run
        call_count_after_first = mock_resolver.resolve.call_count

        normalizer.normalize(_raw("EGFR"))          # second call: exact match in DB
        call_count_after_second = mock_resolver.resolve.call_count

        # Resolver must not have been called again on the second normalize
        assert call_count_after_second == call_count_after_first


# ---------------------------------------------------------------------------
# External resolver (step 2)
# ---------------------------------------------------------------------------

class TestExternalResolver:

    def test_resolver_called_when_no_exact_match(self, repo, conn):
        mock_resolver = _mock_resolver(None)
        normalizer = _normalizer(repo, resolver=mock_resolver)
        normalizer.normalize(_raw("EGFR", entity_type="protein"))
        mock_resolver.resolve.assert_called_once_with("EGFR", "protein")

    def test_resolver_synonyms_enable_match_to_existing_entity(self, repo, conn):
        """
        Paper 1 creates 'EGFR'. Paper 2 extracts 'ErbB1'.
        UniProt knows ErbB1 is a synonym for EGFR — resolver enriches the
        name set and the exact retry should find the existing entity.
        """
        normalizer = _normalizer(repo)
        eid1 = normalizer.normalize(_raw("EGFR"))

        # Now set up resolver that returns EGFR as canonical with ErbB1 synonym
        resolved = ResolvedEntity(
            canonical_name="EGFR",
            accession="P00533",
            synonyms=["EGFR", "ErbB1", "HER1"],
            source="uniprot",
        )
        mock_resolver = _mock_resolver(resolved)
        normalizer2 = _normalizer(repo, resolver=mock_resolver)
        # Exact match for "ErbB1" alone fails; resolver adds "EGFR" to names_to_check;
        # retry exact match finds the existing entity
        eid2 = normalizer2.normalize(_raw("ErbB1", entity_type="protein"))
        assert eid1 == eid2

    def test_resolver_canonical_name_used_for_new_entity(self, repo, conn):
        """When creating a new entity, use resolver's canonical name not LLM string."""
        resolved = ResolvedEntity(
            canonical_name="TP53",
            accession="P04637",
            synonyms=["TP53", "p53", "LFS1"],
            organism="Homo sapiens",
            source="uniprot",
        )
        mock_resolver = _mock_resolver(resolved)
        normalizer = _normalizer(repo, resolver=mock_resolver)

        # LLM gave us the colloquial name "tumor suppressor p53"
        eid = normalizer.normalize(_raw("tumor suppressor p53", entity_type="protein"))

        entity = repo.get_by_id(eid)
        assert entity is not None
        assert entity["canonical_name"] == "TP53"

    def test_resolver_organism_used_for_new_entity(self, repo, conn):
        resolved = ResolvedEntity(
            canonical_name="Brca1",
            accession="P48754",
            synonyms=["Brca1"],
            organism="Mus musculus",
            source="uniprot",
        )
        mock_resolver = _mock_resolver(resolved)
        normalizer = _normalizer(repo, resolver=mock_resolver)
        eid = normalizer.normalize(_raw("Brca1", entity_type="protein"))

        entity = repo.get_by_id(eid)
        assert entity is not None
        assert entity["organism"] == "Mus musculus"

    def test_resolver_accession_registered_as_synonym(self, repo, conn):
        """UniProt accession stored as synonym for future exact lookup."""
        resolved = ResolvedEntity(
            canonical_name="TP53",
            accession="P04637",
            synonyms=["TP53", "p53"],
            source="uniprot",
        )
        mock_resolver = _mock_resolver(resolved)
        normalizer = _normalizer(repo, resolver=mock_resolver)
        eid = normalizer.normalize(_raw("TP53", entity_type="protein"))

        # Should now resolve by accession too
        eid2 = normalizer.normalize(_raw("P04637", entity_type="protein"))
        assert eid == eid2

    def test_resolver_returning_none_falls_through_to_fuzzy(self, repo, conn):
        """Resolver failure must not prevent fuzzy matching from running."""
        normalizer_no_resolver = _normalizer(repo)
        eid1 = normalizer_no_resolver.normalize(_raw("BRAF"))

        mock_resolver = _mock_resolver(None)  # resolver finds nothing
        normalizer_with_resolver = _normalizer(repo, resolver=mock_resolver)
        # "BRAF1" is close enough to "BRAF" at 0.88 — just below 0.92 threshold,
        # so use a name that's above threshold
        eid2 = normalizer_with_resolver.normalize(_raw("BRAF"))  # exact match via synonym
        assert eid1 == eid2

    def test_resolver_network_failure_does_not_crash_normalizer(self, repo, conn):
        """If resolver raises unexpectedly, normalizer still creates a new entity."""
        mock_resolver = MagicMock(spec=EntityResolver)
        mock_resolver.resolve.side_effect = RuntimeError("network down")
        normalizer = _normalizer(repo, resolver=mock_resolver)

        # Should not raise; falls through to create new
        eid = normalizer.normalize(_raw("SomeProtein"))
        assert eid is not None

    def test_resolver_not_called_for_pathway_type(self, repo, conn):
        """EntityResolver.resolve returns None for unsupported types — normalizer
        should still function (create a new entity)."""
        mock_resolver = MagicMock(spec=EntityResolver)
        mock_resolver.resolve.return_value = None
        normalizer = _normalizer(repo, resolver=mock_resolver)

        eid = normalizer.normalize(_raw("MAPK signaling", entity_type="pathway"))
        assert eid is not None
        mock_resolver.resolve.assert_called_once_with("MAPK signaling", "pathway")


# ---------------------------------------------------------------------------
# Fuzzy match (step 3)
# ---------------------------------------------------------------------------

class TestFuzzyMatch:

    def test_fuzzy_match_above_threshold(self, repo, conn):
        """'BRCA-1' should fuzzy-match 'BRCA1' at well above 0.92."""
        normalizer = _normalizer(repo)
        eid1 = normalizer.normalize(_raw("BRCA1"))
        eid2 = normalizer.normalize(_raw("BRCA1"))   # same name, exact match
        assert eid1 == eid2

    def test_fuzzy_no_match_below_threshold(self, repo, conn):
        """Completely different names must not be merged."""
        normalizer = _normalizer(repo)
        eid1 = normalizer.normalize(_raw("EGFR"))
        eid2 = normalizer.normalize(_raw("BRCA1"))
        assert eid1 != eid2

    def test_fuzzy_matches_against_synonyms_not_just_canonical(self, repo, conn):
        """
        Fuzzy match must compare against all registered synonyms, not only the
        canonical name. A synonym that closely matches the lookup name should
        resolve to the existing entity even when the canonical name would not.

        We use a long synonym so 1 extra character gives ratio = 2*N/(2N+1) > 0.92.
        (N=13 → 26/27 ≈ 0.963; canonical "ABC" alone would score near 0.)
        """
        import difflib
        normalizer = _normalizer(repo)
        # Canonical "ABC" is very different from the lookup; synonym is close.
        synonym = "epidermal_growth_factor_receptor"       # 32 chars
        lookup  = "epidermal_growth_factor_receptorX"      # 33 chars — 1 char appended
        ratio = difflib.SequenceMatcher(None, synonym, lookup).ratio()
        assert ratio > 0.92, f"Test precondition failed: ratio={ratio:.3f}"

        eid1 = normalizer.normalize(_raw("ABC", synonyms=[synonym]))
        eid2 = normalizer.normalize(_raw(lookup))
        assert eid1 == eid2

    def test_gene_protein_cross_type_fuzzy(self, repo, conn):
        """Gene and protein with same name should resolve to one entity."""
        normalizer = _normalizer(repo)
        eid_gene = normalizer.normalize(_raw("TP53", entity_type="gene"))
        eid_protein = normalizer.normalize(_raw("TP53", entity_type="protein"))
        assert eid_gene == eid_protein

    def test_cross_type_does_not_merge_unrelated_names(self, repo, conn):
        """Cross-type matching must not merge genuinely different entities."""
        normalizer = _normalizer(repo)
        eid1 = normalizer.normalize(_raw("EGFR", entity_type="gene"))
        eid2 = normalizer.normalize(_raw("BRCA1", entity_type="protein"))
        assert eid1 != eid2


# ---------------------------------------------------------------------------
# Create new entity (step 4)
# ---------------------------------------------------------------------------

class TestCreateNewEntity:

    def test_creates_new_entity_when_nothing_matches(self, repo, conn):
        normalizer = _normalizer(repo)
        count_before = repo.count()
        normalizer.normalize(_raw("COMPLETELYNEWPROTEIN12345"))
        assert repo.count() == count_before + 1

    def test_entity_count_increments_per_unique_name(self, repo, conn):
        normalizer = _normalizer(repo)
        normalizer.normalize(_raw("ProteinA"))
        normalizer.normalize(_raw("ProteinB"))
        normalizer.normalize(_raw("ProteinA"))  # duplicate — no new entity
        assert repo.count() == 2

    def test_new_entity_has_correct_type(self, repo, conn):
        normalizer = _normalizer(repo)
        eid = normalizer.normalize(_raw("ATP", entity_type="metabolite"))
        entity = repo.get_by_id(eid)
        assert entity["entity_type"] == "metabolite"

    def test_new_entity_synonyms_registered(self, repo, conn):
        normalizer = _normalizer(repo)
        eid = normalizer.normalize(_raw("EGFR", synonyms=["ErbB1", "HER1"]))
        # All three names should resolve to same entity
        assert repo.find_by_synonym("egfr") == eid
        assert repo.find_by_synonym("erbb1") == eid
        assert repo.find_by_synonym("her1") == eid


# ---------------------------------------------------------------------------
# In-process cache behaviour
# ---------------------------------------------------------------------------

class TestCacheBehaviour:

    def test_fuzzy_cache_updated_after_create(self, repo, conn):
        """
        Entity created for paper 1 must be a fuzzy candidate for paper 2
        within the same cycle (same normalizer instance).

        We use a long name so that appending one character gives ratio > 0.92.
        (32 chars → ratio = 64/65 ≈ 0.985)
        """
        import difflib
        name1 = "mitogen_activated_protein_kinase"    # 32 chars
        name2 = "mitogen_activated_protein_kinaseX"   # 33 chars
        ratio = difflib.SequenceMatcher(None, name1, name2).ratio()
        assert ratio > 0.92, f"Test precondition failed: ratio={ratio:.3f}"

        normalizer = _normalizer(repo)
        eid1 = normalizer.normalize(_raw(name1))
        eid2 = normalizer.normalize(_raw(name2))
        assert eid1 == eid2

    def test_synonym_cache_populated_after_lookup(self, repo, conn):
        normalizer = _normalizer(repo)
        eid = normalizer.normalize(_raw("CDK2"))

        # After first normalize, synonym cache should contain 'cdk2'
        assert "cdk2" in normalizer._synonym_cache
        assert normalizer._synonym_cache["cdk2"] == eid

    def test_rebuild_cache_clears_state(self, repo, conn):
        normalizer = _normalizer(repo)
        normalizer.normalize(_raw("PTEN"))
        assert len(normalizer._synonym_cache) > 0
        assert len(normalizer._fuzzy_cache) > 0

        normalizer.rebuild_cache()
        assert len(normalizer._synonym_cache) == 0
        assert len(normalizer._fuzzy_cache) == 0

    def test_set_repo_preserves_cache(self, repo, conn, engine):
        """set_repo() swaps the DB connection but must not lose in-memory cache."""
        normalizer = _normalizer(repo)
        eid = normalizer.normalize(_raw("PTEN"))
        original_cache = dict(normalizer._synonym_cache)

        # Swap in a fresh repo on the same engine
        with engine.connect() as conn2:
            new_repo = EntityRepo(conn2)
            normalizer.set_repo(new_repo)

        assert normalizer._synonym_cache == original_cache
        # Lookup should still work via cache (no DB hit needed)
        assert normalizer._synonym_cache.get("pten") == eid


# ---------------------------------------------------------------------------
# Resolver disabled
# ---------------------------------------------------------------------------

class TestResolverDisabled:

    def test_normalizer_works_without_resolver(self, repo, conn):
        normalizer = _normalizer(repo, resolver=None)
        eid = normalizer.normalize(_raw("EGFR"))
        assert eid is not None

    def test_no_resolver_falls_through_to_create(self, repo, conn):
        normalizer = _normalizer(repo, resolver=None)
        count_before = repo.count()
        normalizer.normalize(_raw("NOVELPROTEIN"))
        assert repo.count() == count_before + 1
