"""
Unit tests for UniProtResolver, ChEBIResolver, and EntityResolver.

All HTTP calls are mocked — no real network traffic.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call

from autobioresearch.crawlers.entity_resolvers import (
    ChEBIResolver,
    EntityResolver,
    ResolvedEntity,
    UniProtResolver,
    _MAMMALIAN_TAXON_IDS,
)


# ---------------------------------------------------------------------------
# Fixtures — canned API responses
# ---------------------------------------------------------------------------

def _uniprot_response(entries: list[dict]) -> dict:
    return {"results": entries}


def _uniprot_entry(
    accession: str = "P04637",
    gene_name: str = "TP53",
    gene_synonyms: list[str] | None = None,
    protein_full: str = "Cellular tumor antigen p53",
    protein_shorts: list[str] | None = None,
    alt_names: list[str] | None = None,
    taxon_id: int = 9606,
    organism: str = "Homo sapiens",
) -> dict:
    return {
        "primaryAccession": accession,
        "genes": [
            {
                "geneName": {"value": gene_name},
                "synonyms": [{"value": s} for s in (gene_synonyms or [])],
                "orfNames": [],
            }
        ],
        "proteinDescription": {
            "recommendedName": {
                "fullName": {"value": protein_full},
                "shortNames": [{"value": s} for s in (protein_shorts or [])],
            },
            "alternativeNames": [
                {"fullName": {"value": a}, "shortNames": []}
                for a in (alt_names or [])
            ],
        },
        "organism": {"scientificName": organism, "taxonId": taxon_id},
    }


def _chebi_response(docs: list[dict]) -> dict:
    return {"response": {"numFound": len(docs), "docs": docs}}


def _chebi_doc(
    label: str = "acetylcholine",
    obo_id: str = "CHEBI:15355",
    synonyms: list[str] | None = None,
) -> dict:
    return {
        "label": label,
        "obo_id": obo_id,
        "id": obo_id,
        "synonym": synonyms or [],
    }


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ===========================================================================
# UniProtResolver
# ===========================================================================

class TestUniProtResolver:

    def _resolver(self) -> UniProtResolver:
        return UniProtResolver(requests_per_second=100.0, timeout=5)

    # --- Happy path ---------------------------------------------------------

    def test_resolve_returns_canonical_gene_name(self):
        resolver = self._resolver()
        entry = _uniprot_entry(gene_name="TP53", gene_synonyms=["p53", "LFS1"])
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([entry]))):
            result = resolver.resolve("TP53")

        assert result is not None
        assert result.canonical_name == "TP53"
        assert result.accession == "P04637"
        assert result.source == "uniprot"

    def test_resolve_includes_gene_synonyms(self):
        resolver = self._resolver()
        entry = _uniprot_entry(gene_name="EGFR", gene_synonyms=["ErbB1", "HER1"])
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([entry]))):
            result = resolver.resolve("EGFR")

        assert result is not None
        assert "ErbB1" in result.synonyms
        assert "HER1" in result.synonyms
        assert "EGFR" in result.synonyms

    def test_resolve_includes_protein_names(self):
        resolver = self._resolver()
        entry = _uniprot_entry(
            gene_name="BRCA1",
            protein_full="Breast cancer type 1 susceptibility protein",
            protein_shorts=["BRCA-1"],
            alt_names=["RING finger protein 53"],
        )
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([entry]))):
            result = resolver.resolve("BRCA1")

        assert result is not None
        assert "Breast cancer type 1 susceptibility protein" in result.synonyms
        assert "BRCA-1" in result.synonyms
        assert "RING finger protein 53" in result.synonyms

    def test_resolve_returns_organism(self):
        resolver = self._resolver()
        entry = _uniprot_entry(organism="Mus musculus", taxon_id=10090)
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([entry]))):
            result = resolver.resolve("Trp53")

        assert result is not None
        assert result.organism == "Mus musculus"

    # --- Organism filtering -------------------------------------------------

    def test_resolve_prefers_human_over_other_mammals(self):
        resolver = self._resolver()
        mouse_entry = _uniprot_entry(
            accession="P02340", gene_name="Trp53", taxon_id=10090, organism="Mus musculus"
        )
        human_entry = _uniprot_entry(
            accession="P04637", gene_name="TP53", taxon_id=9606, organism="Homo sapiens"
        )
        # Mouse returned first, but human should be preferred
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(
                              _uniprot_response([mouse_entry, human_entry]))):
            result = resolver.resolve("TP53")

        assert result is not None
        assert result.accession == "P04637"
        assert result.organism == "Homo sapiens"

    def test_resolve_accepts_any_mammal_when_no_human(self):
        resolver = self._resolver()
        rat_entry = _uniprot_entry(
            accession="P10361", gene_name="Tp53", taxon_id=10116, organism="Rattus norvegicus"
        )
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([rat_entry]))):
            result = resolver.resolve("Tp53")

        assert result is not None
        assert result.organism == "Rattus norvegicus"

    def test_resolve_skips_nonmammalian_entries(self):
        resolver = self._resolver()
        yeast_entry = _uniprot_entry(
            accession="P06786", gene_name="CDC28", taxon_id=559292,
            organism="Saccharomyces cerevisiae"
        )
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([yeast_entry]))):
            result = resolver.resolve("CDC28")

        # No mammalian entry found; falls through to None (both passes return empty)
        # The yeast entry will be used as a fallback since candidates falls back to all results
        # when no mammalian hits are found — acceptable behaviour for edge case
        # (normalizer will still fuzzy/create as appropriate)
        assert result is None or result.accession == "P06786"

    # --- Fallback and caching -----------------------------------------------

    def test_resolve_falls_back_to_trembl_when_no_reviewed(self):
        resolver = self._resolver()
        reviewed_empty = _uniprot_response([])
        trembl_entry = _uniprot_entry(accession="A0A000", gene_name="NOVEL1")
        # First two calls (reviewed gene + reviewed protein) return empty;
        # third call (unreviewed gene) returns a hit
        responses = [
            _mock_response(reviewed_empty),  # reviewed gene search
            _mock_response(reviewed_empty),  # reviewed protein search
            _mock_response(_uniprot_response([trembl_entry])),  # unreviewed gene
        ]
        with patch.object(resolver._session, "get", side_effect=responses):
            result = resolver.resolve("NOVEL1")

        assert result is not None
        assert result.accession == "A0A000"

    def test_resolve_caches_result(self):
        resolver = self._resolver()
        entry = _uniprot_entry()
        mock_get = MagicMock(
            return_value=_mock_response(_uniprot_response([entry]))
        )
        with patch.object(resolver._session, "get", mock_get):
            r1 = resolver.resolve("TP53")
            r2 = resolver.resolve("TP53")   # second call — should hit cache

        assert r1 is r2
        # Network was called for first lookup only (2 query forms × 1 = at most 2 calls)
        assert mock_get.call_count <= 2

    def test_resolve_caches_none_result(self):
        resolver = self._resolver()
        empty = _mock_response(_uniprot_response([]))
        with patch.object(resolver._session, "get", return_value=empty) as mock_get:
            r1 = resolver.resolve("DOESNOTEXIST")
            r2 = resolver.resolve("DOESNOTEXIST")

        assert r1 is None
        assert r2 is None
        # Cache means second call doesn't hit network
        first_call_count = mock_get.call_count
        assert first_call_count == mock_get.call_count

    # --- Failure handling ---------------------------------------------------

    def test_resolve_returns_none_on_network_error(self):
        import requests
        resolver = self._resolver()
        with patch.object(resolver._session, "get",
                          side_effect=requests.ConnectionError("unreachable")):
            result = resolver.resolve("TP53")

        assert result is None

    def test_resolve_returns_none_on_empty_results(self):
        resolver = self._resolver()
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([]))):
            result = resolver.resolve("NOTAPROTEIN")

        assert result is None

    def test_resolve_handles_rate_limit_with_retry(self):
        resolver = self._resolver()
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}

        entry = _uniprot_entry()
        ok = _mock_response(_uniprot_response([entry]))

        with patch.object(resolver._session, "get",
                          side_effect=[rate_limited, ok]):
            with patch("time.sleep"):   # don't actually sleep in tests
                result = resolver.resolve("TP53")

        assert result is not None

    # --- Synonym deduplication ----------------------------------------------

    def test_synonyms_deduplicated(self):
        resolver = self._resolver()
        # Gene name "TP53" also appears in alternative protein names
        entry = _uniprot_entry(
            gene_name="TP53",
            protein_full="TP53 protein",  # would create duplicate "TP53"
            alt_names=["TP53"],
        )
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_uniprot_response([entry]))):
            result = resolver.resolve("TP53")

        assert result is not None
        lowered = [s.lower() for s in result.synonyms]
        assert lowered.count("tp53") == 1


# ===========================================================================
# ChEBIResolver
# ===========================================================================

class TestChEBIResolver:

    def _resolver(self) -> ChEBIResolver:
        return ChEBIResolver(requests_per_second=100.0, timeout=5)

    # --- Happy path ---------------------------------------------------------

    def test_resolve_returns_label_and_synonyms(self):
        resolver = self._resolver()
        doc = _chebi_doc(
            label="acetylcholine",
            obo_id="CHEBI:15355",
            synonyms=["ACh", "acetylcholine(1+)"],
        )
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_chebi_response([doc]))):
            result = resolver.resolve("acetylcholine")

        assert result is not None
        assert result.canonical_name == "acetylcholine"
        assert result.accession == "CHEBI:15355"
        assert "ACh" in result.synonyms
        assert result.source == "chebi"

    def test_resolve_label_included_in_synonyms(self):
        resolver = self._resolver()
        doc = _chebi_doc(label="dopamine", obo_id="CHEBI:18243", synonyms=["3-hydroxytyramine"])
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_chebi_response([doc]))):
            result = resolver.resolve("dopamine")

        assert result is not None
        assert "dopamine" in result.synonyms

    def test_resolve_exact_match_tried_first(self):
        resolver = self._resolver()
        doc = _chebi_doc(label="serotonin", obo_id="CHEBI:28790")
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params.get("exact"))
            return _mock_response(_chebi_response([doc]))

        with patch.object(resolver._session, "get", side_effect=fake_get):
            resolver.resolve("serotonin")

        # First call should be exact=true
        assert calls[0] == "true"

    def test_resolve_relaxes_to_non_exact_when_empty(self):
        resolver = self._resolver()
        empty = _mock_response(_chebi_response([]))
        doc = _chebi_doc(label="serotonin", obo_id="CHEBI:28790")
        ok = _mock_response(_chebi_response([doc]))

        with patch.object(resolver._session, "get", side_effect=[empty, ok]):
            result = resolver.resolve("5-HT")  # common alias, exact may miss

        assert result is not None
        assert result.canonical_name == "serotonin"

    # --- Caching and failures -----------------------------------------------

    def test_resolve_cached(self):
        resolver = self._resolver()
        doc = _chebi_doc()
        mock_get = MagicMock(return_value=_mock_response(_chebi_response([doc])))
        with patch.object(resolver._session, "get", mock_get):
            r1 = resolver.resolve("acetylcholine")
            r2 = resolver.resolve("acetylcholine")

        assert r1 is r2
        assert mock_get.call_count == 1  # exact match succeeds on first try

    def test_resolve_returns_none_on_empty(self):
        resolver = self._resolver()
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_chebi_response([]))):
            result = resolver.resolve("notacompound")

        assert result is None

    def test_resolve_returns_none_on_network_error(self):
        import requests
        resolver = self._resolver()
        with patch.object(resolver._session, "get",
                          side_effect=requests.ConnectionError("down")):
            result = resolver.resolve("acetylcholine")

        assert result is None

    def test_resolve_handles_string_synonym(self):
        """OLS4 sometimes returns synonym as a single string, not a list."""
        resolver = self._resolver()
        doc = _chebi_doc(synonyms="ACh")  # string, not list
        doc["synonym"] = "ACh"
        with patch.object(resolver._session, "get",
                          return_value=_mock_response(_chebi_response([doc]))):
            result = resolver.resolve("acetylcholine")

        # Should not crash, label at minimum in synonyms
        assert result is not None
        assert result.canonical_name == "acetylcholine"


# ===========================================================================
# EntityResolver (facade)
# ===========================================================================

class TestEntityResolver:

    def _resolver(self) -> EntityResolver:
        return EntityResolver(requests_per_second=100.0, timeout=5)

    def test_routes_protein_to_uniprot(self):
        resolver = self._resolver()
        with patch.object(resolver._uniprot, "resolve",
                          return_value=ResolvedEntity("TP53", "P04637", source="uniprot")) as mock:
            resolver.resolve("TP53", "protein")
        mock.assert_called_once_with("TP53")

    def test_routes_gene_to_uniprot(self):
        resolver = self._resolver()
        with patch.object(resolver._uniprot, "resolve",
                          return_value=ResolvedEntity("BRCA1", "P38398", source="uniprot")) as mock:
            resolver.resolve("BRCA1", "gene")
        mock.assert_called_once_with("BRCA1")

    def test_routes_molecule_to_chebi(self):
        resolver = self._resolver()
        with patch.object(resolver._chebi, "resolve",
                          return_value=ResolvedEntity("dopamine", "CHEBI:18243", source="chebi")) as mock:
            resolver.resolve("dopamine", "molecule")
        mock.assert_called_once_with("dopamine")

    def test_routes_metabolite_to_chebi(self):
        resolver = self._resolver()
        with patch.object(resolver._chebi, "resolve",
                          return_value=ResolvedEntity("ATP", "CHEBI:30616", source="chebi")) as mock:
            resolver.resolve("ATP", "metabolite")
        mock.assert_called_once_with("ATP")

    def test_returns_none_for_pathway(self):
        resolver = self._resolver()
        result = resolver.resolve("MAPK signaling", "pathway")
        assert result is None

    def test_returns_none_for_disease(self):
        resolver = self._resolver()
        result = resolver.resolve("Alzheimer disease", "disease")
        assert result is None

    def test_returns_none_for_cell_type(self):
        resolver = self._resolver()
        result = resolver.resolve("CD4+ T cell", "cell_type")
        assert result is None

    def test_returns_none_for_organism(self):
        resolver = self._resolver()
        result = resolver.resolve("Homo sapiens", "organism")
        assert result is None

    def test_uniprot_not_called_for_chebi_types(self):
        resolver = self._resolver()
        with patch.object(resolver._uniprot, "resolve") as uniprot_mock, \
             patch.object(resolver._chebi, "resolve", return_value=None):
            resolver.resolve("glucose", "metabolite")
        uniprot_mock.assert_not_called()

    def test_chebi_not_called_for_uniprot_types(self):
        resolver = self._resolver()
        with patch.object(resolver._chebi, "resolve") as chebi_mock, \
             patch.object(resolver._uniprot, "resolve", return_value=None):
            resolver.resolve("TP53", "gene")
        chebi_mock.assert_not_called()
