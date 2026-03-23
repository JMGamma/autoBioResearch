from __future__ import annotations

from sqlalchemy import create_engine, select

from autobioresearch import database as db
from autobioresearch.config import AppConfig
from autobioresearch import main as main_module
from autobioresearch.main import run_fetch_phase
from autobioresearch.metrics import compute_score
from autobioresearch.extractor.verifier import InteractionVerifier
from autobioresearch.extractor.evidence_normalizer import EvidenceNormalizer
from autobioresearch.planner import QueryPlanner
from autobioresearch.conflict.resolver import ConflictResolver
from autobioresearch.conflict.adjudicator import EvidenceAdjudicator
from autobioresearch.models import (
    BiologicalEntity,
    Conflict,
    ConflictStatus,
    ConflictType,
    FetchStatus,
    Interaction,
    InteractionContext,
    InteractionType,
    EvidenceRecord,
    EvidenceType,
    Paper,
    LiteralClaimRecord,
    QueryType,
    SearchQuery,
    ExtractedInteractionRaw,
)
from autobioresearch.storage.repositories import Repositories


class _NoopSemanticScholar:
    def search(self, query: str, max_results: int = 50):
        return []


class _StubLLM:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    def call_with_tool(self, system: str, user: str, tool: dict, tool_function: dict):
        self.calls.append({"system": system, "user": user, "tool": tool["name"]})
        return self.result


class _RecordingPubMed:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int = 50):
        self.calls.append((query, max_results))
        if self.should_fail:
            raise RuntimeError("boom")
        return [
            Paper(
                id="pmid:1",
                source="pubmed",
                title="Test paper",
                abstract="A" * 100,
                fetch_status=FetchStatus.ABSTRACT_ONLY,
            )
        ]


def _config() -> AppConfig:
    return AppConfig(
        llm_api_type="openai_compatible",
        llm_base_url="http://localhost:11434/v1",
        queries_per_cycle=10,
        papers_per_query=7,
        crawler_threads=1,
    )


def _repos():
    engine = create_engine("sqlite:///:memory:")
    db.create_all(engine)
    conn = engine.connect()
    return engine, conn, Repositories(conn)


def test_first_shutdown_signal_requests_graceful_stop(caplog):
    main_module._shutdown = False
    main_module._shutdown_reason = "normal_exit"
    main_module._shutdown_signal_count = 0
    main_module._current_cycle = 3
    main_module._current_phase = "extract"

    with caplog.at_level("WARNING"):
        main_module._handle_sigint(main_module.signal.SIGINT, None)

    assert main_module._shutdown is True
    assert main_module._shutdown_reason == "signal:SIGINT"
    assert main_module._shutdown_signal_count == 1
    assert "Shutdown requested via SIGINT during cycle=3 phase=extract" in caplog.text


def test_second_shutdown_signal_forces_exit(monkeypatch, caplog):
    main_module._shutdown = True
    main_module._shutdown_reason = "signal:SIGINT"
    main_module._shutdown_signal_count = 1
    main_module._current_cycle = 3
    main_module._current_phase = "sleep"

    forced = {}

    def _fake_exit(code: int):
        forced["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr(main_module.os, "_exit", _fake_exit)

    with caplog.at_level("ERROR"):
        try:
            main_module._handle_sigint(main_module.signal.SIGINT, None)
        except SystemExit as exc:
            assert exc.code == 1

    assert main_module._shutdown_reason == "forced_signal:SIGINT"
    assert forced["code"] == 1
    assert "Second shutdown signal received; forcing immediate exit" in caplog.text


def test_investigating_conflicts_still_count_toward_score():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="A", display_name="A", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="B", display_name="B", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)

        interaction = Interaction(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            interaction_type=InteractionType.SIGNALING,
            direction="A_to_B",
            effect="activates",
        )
        interaction_id, _ = repos.interactions.upsert(interaction)
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=interaction_id,
                paper_id="pmid:1",
                evidence_type=EvidenceType.IN_VITRO,
                confidence="high",
                confidence_score=0.8,
                context=InteractionContext(organism="Homo sapiens"),
                snippet="evidence",
            )
        )
        repos.interactions.update_composite_confidence(interaction_id)

        repos.conflicts.insert(
            Conflict(
                interaction_a_id=interaction_id,
                interaction_b_id=interaction_id,
                conflict_type=ConflictType.AMBIGUOUS,
                conflict_axis="effect",
                status="investigating",
                penalty_weight=0.5,
            )
        )
        conn.commit()

        score, stats = compute_score(repos, _config())

        assert stats["n_unresolved_conflicts"] == 1
        assert stats["weighted_conflict_sum"] == 0.5
        assert score < 2.0
    finally:
        conn.close()
        engine.dispose()


def test_interaction_upsert_distinguishes_direction():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="A", display_name="A", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="B", display_name="B", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)

        first_id, first_new = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="activates",
            )
        )
        second_id, second_new = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="B_to_A",
                effect="activates",
            )
        )

        assert first_new is True
        assert second_new is True
        assert first_id != second_id
        assert repos.interactions.count() == 2
    finally:
        conn.close()
        engine.dispose()


def test_low_interaction_entities_count_both_interaction_sides():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="A", display_name="A", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="B", display_name="B", entity_type="protein")
        entity_c = BiologicalEntity(canonical_name="C", display_name="C", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)
        repos.entities.insert(entity_c)

        repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.DIRECT_BINDING,
                direction="undirected",
                effect="binds",
            )
        )
        repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_c.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="activates",
            )
        )
        conn.commit()

        low_coverage_ids = {
            row["id"] for row in repos.entities.get_low_interaction_entities(2, limit=10)
        }

        assert entity_b.id not in low_coverage_ids
        assert entity_a.id in low_coverage_ids
        assert entity_c.id in low_coverage_ids
    finally:
        conn.close()
        engine.dispose()


def test_fetch_phase_marks_failed_queries_and_uses_configured_papers_per_query():
    engine, conn, repos = _repos()
    try:
        query = SearchQuery(
            query_text="tp53",
            source_api="pubmed",
            query_type=QueryType.INITIAL,
            origin="seed",
        )
        repos.queries.insert(query)
        conn.commit()

        pubmed = _RecordingPubMed(should_fail=True)
        stats = run_fetch_phase(repos, _config(), pubmed, _NoopSemanticScholar())
        conn.commit()

        failed_status = conn.execute(
            select(db.search_queries.c.status).where(db.search_queries.c.id == query.id)
        ).scalar_one()

        assert failed_status == "failed"
        assert pubmed.calls == [("tp53", 7)]
        assert stats["queries_failed"] == 1
        assert stats["papers_fetched_total"] == 0
    finally:
        conn.close()
        engine.dispose()


def test_reopened_conflicts_count_as_unresolved_after_new_evidence():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="A", display_name="A", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="B", display_name="B", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)

        interaction = Interaction(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            interaction_type=InteractionType.SIGNALING,
            direction="A_to_B",
            effect="activates",
        )
        interaction_id, _ = repos.interactions.upsert(interaction)

        repos.conflicts.insert(
            Conflict(
                interaction_a_id=interaction_id,
                interaction_b_id=interaction_id,
                conflict_type=ConflictType.AMBIGUOUS,
                conflict_axis="effect",
                status="investigating",
                penalty_weight=0.5,
            )
        )
        conn.commit()

        reopened = repos.conflicts.reopen_for_interactions([interaction_id])
        conn.commit()

        status = conn.execute(select(db.conflicts.c.status)).scalar_one()
        score, stats = compute_score(repos, _config())

        assert reopened == 1
        assert status == "reopened"
        assert stats["n_unresolved_conflicts"] == 1
        assert stats["weighted_conflict_sum"] == 0.5
    finally:
        conn.close()
        engine.dispose()


def test_metrics_log_persists_phase_one_telemetry_fields():
    engine, conn, repos = _repos()
    try:
        repos.metrics.log(
            cycle=1,
            n_entities=2,
            n_interactions=3,
            n_evidence=4,
            n_unresolved_conflicts=1,
            weighted_conflict_sum=0.5,
            score=4.0,
            penalty=2.0,
            papers_processed=5,
            papers_fetched_total=6,
            papers_fetched_new=2,
            papers_extraction_failed=1,
            new_entities=1,
            new_interactions=2,
            new_evidence=3,
            evidence_accepted=3,
            claims_verified=2,
            claims_needing_review=1,
            new_conflicts=1,
            conflicts_reopened=1,
            conflicts_resolved=0,
            queries_generated=4,
            queries_with_new_papers=2,
            query_linked_evidence=3,
        )
        conn.commit()

        latest = repos.metrics.get_latest()

        assert latest["papers_fetched_total"] == 6
        assert latest["papers_fetched_new"] == 2
        assert latest["papers_extraction_failed"] == 1
        assert latest["evidence_accepted"] == 3
        assert latest["claims_verified"] == 2
        assert latest["claims_needing_review"] == 1
        assert latest["conflicts_reopened"] == 1
        assert latest["queries_generated"] == 4
        assert latest["queries_with_new_papers"] == 2
        assert latest["query_linked_evidence"] == 3
    finally:
        conn.close()
        engine.dispose()


def test_interaction_verifier_heuristic_marks_supported_claim_verified():
    verifier = InteractionVerifier(_config(), llm=None)

    interaction = ExtractedInteractionRaw(
        entity_a="TP53",
        entity_b="MDM2",
        interaction_type=InteractionType.SIGNALING,
        direction="A_to_B",
        effect="activates",
        evidence_type=EvidenceType.IN_VITRO,
        organism="Homo sapiens",
        tissue_cell_type="HEK293",
        condition="hypoxia",
        confidence="high",
        confidence_score=0.9,
        snippet="In Homo sapiens HEK293 cells under hypoxia, TP53 activates MDM2 signaling.",
    )

    verified = verifier.verify_interaction(
        "pmid:1",
        "Title",
        "In Homo sapiens HEK293 cells under hypoxia, TP53 activates MDM2 signaling.",
        interaction,
    )

    assert verified.verification_status == "verified"
    assert verified.verification_score is not None


def test_interaction_verifier_heuristic_flags_missing_support_for_review():
    verifier = InteractionVerifier(_config(), llm=None)

    interaction = ExtractedInteractionRaw(
        entity_a="TP53",
        entity_b="MDM2",
        interaction_type=InteractionType.SIGNALING,
        direction="A_to_B",
        effect="inhibits",
        evidence_type=EvidenceType.IN_VITRO,
        organism="Homo sapiens",
        confidence="high",
        confidence_score=0.9,
        snippet="TP53 and MDM2 were discussed in the manuscript.",
    )

    verified = verifier.verify_interaction(
        "pmid:1",
        "Title",
        "TP53 and MDM2 were discussed in the manuscript.",
        interaction,
    )

    assert verified.verification_status == "needs_review"
    assert "effect=inhibits" in verified.verification_notes


def test_query_planner_prioritizes_conflict_reduction_work():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="A", display_name="A", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="B", display_name="B", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)

        interaction = Interaction(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            interaction_type=InteractionType.SIGNALING,
            direction="A_to_B",
            effect="activates",
        )
        interaction_id, _ = repos.interactions.upsert(interaction)
        conflict = Conflict(
            interaction_a_id=interaction_id,
            interaction_b_id=interaction_id,
            conflict_type=ConflictType.AMBIGUOUS,
            conflict_axis="effect",
            status=ConflictStatus.OPEN,
            penalty_weight=1.0,
        )
        repos.conflicts.insert(conflict)

        generic = SearchQuery(
            query_text="generic biology query",
            source_api="pubmed",
            query_type=QueryType.INITIAL,
            origin="seed",
        )
        conflict_query = SearchQuery(
            query_text="A B controversy",
            source_api="pubmed",
            query_type=QueryType.CONFLICT_RESOLUTION,
            origin=f"conflict_id:{conflict.id}",
            target_kind="conflict",
            target_id=conflict.id,
        )
        repos.queries.insert(generic)
        repos.queries.insert(conflict_query)
        conn.commit()

        planner = QueryPlanner()
        planner.plan_pending_queries(repos, _config())
        conn.commit()

        pending = repos.queries.get_pending(limit=2)

        assert pending[0]["id"] == conflict_query.id
        assert pending[0]["planning_score"] > pending[1]["planning_score"]
    finally:
        conn.close()
        engine.dispose()


def test_targeted_query_generation_creates_metadata_rich_queries():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="TP53", display_name="TP53", entity_type="protein", paper_count=8)
        entity_b = BiologicalEntity(canonical_name="MDM2", display_name="MDM2", entity_type="protein", paper_count=2)
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)

        interaction = Interaction(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            interaction_type=InteractionType.SIGNALING,
            direction="A_to_B",
            effect="activates",
        )
        interaction_id, _ = repos.interactions.upsert(interaction)
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=interaction_id,
                paper_id="pmid:seed",
                evidence_type=EvidenceType.CURATED_DB,
                confidence="medium",
                confidence_score=0.6,
                context=InteractionContext(),
                snippet="seeded evidence",
            )
        )
        repos.interactions.update_composite_confidence(interaction_id)
        conn.execute(
            db.interactions.update().where(db.interactions.c.id == interaction_id).values(evidence_count=1)
        )
        conn.commit()

        planner = QueryPlanner()
        added = planner.generate_targeted_queries(repos, _config())
        conn.commit()

        rows = conn.execute(select(db.search_queries)).mappings().all()

        assert added >= 1
        assert any(row["generation_reason"] == "sparse_high_value_entity" for row in rows)
        assert any(row["generation_reason"] == "under_supported_seeded_interaction" for row in rows)
        assert all(row["planning_score"] > 0 for row in rows)
    finally:
        conn.close()
        engine.dispose()


def test_query_outcomes_are_attributed_across_linked_queries():
    engine, conn, repos = _repos()
    try:
        query_a = SearchQuery(
            query_text="tp53 a",
            source_api="pubmed",
            query_type=QueryType.GAP_FILLING,
            origin="entity_id:a",
        )
        query_b = SearchQuery(
            query_text="tp53 b",
            source_api="pubmed",
            query_type=QueryType.GAP_FILLING,
            origin="entity_id:b",
        )
        repos.queries.insert(query_a)
        repos.queries.insert(query_b)
        conn.commit()

        repos.queries.record_outcomes(
            [query_a.id, query_b.id],
            papers_processed=1,
            new_interactions=2,
            new_evidence=4,
        )
        conn.commit()

        rows = conn.execute(
            select(
                db.search_queries.c.id,
                db.search_queries.c.attributed_papers_processed,
                db.search_queries.c.attributed_new_interactions,
                db.search_queries.c.attributed_new_evidence,
            ).where(db.search_queries.c.id.in_([query_a.id, query_b.id]))
        ).mappings().all()

        assert len(rows) == 2
        assert all(row["attributed_papers_processed"] == 0.5 for row in rows)
        assert all(row["attributed_new_interactions"] == 1.0 for row in rows)
        assert all(row["attributed_new_evidence"] == 2.0 for row in rows)
    finally:
        conn.close()
        engine.dispose()


def test_targeted_slot_allocation_favors_high_yield_family():
    engine, conn, repos = _repos()
    try:
        for idx in range(3):
            q = SearchQuery(
                query_text=f"entity success {idx}",
                source_api="pubmed",
                query_type=QueryType.GAP_FILLING,
                origin=f"entity_id:{idx}",
                generation_reason="sparse_high_value_entity",
                target_kind="entity",
                status="done",
                papers_new=2,
                outcome_papers_processed=1,
                outcome_new_interactions=2,
                outcome_new_evidence=4,
                attributed_papers_processed=1.0,
                attributed_new_interactions=2.0,
                attributed_new_evidence=4.0,
                improved_graph=True,
            )
            repos.queries.insert(q)
            conn.execute(
                db.search_queries.update().where(db.search_queries.c.id == q.id).values(completed_at=f"2026-03-22T00:00:0{idx}Z")
            )
        conn.commit()

        planner = QueryPlanner()
        conflict_slots, entity_slots, interaction_slots = planner._allocate_targeted_slots(repos, 9)

        assert entity_slots >= conflict_slots
        assert entity_slots >= interaction_slots
    finally:
        conn.close()
        engine.dispose()


def test_query_outcomes_are_recorded_on_extraction_results():
    engine, conn, repos = _repos()
    try:
        query = SearchQuery(
            query_text="tp53 mdm2",
            source_api="pubmed",
            query_type=QueryType.GAP_FILLING,
            origin="entity_id:test",
        )
        repos.queries.insert(query)
        conn.commit()

        repos.queries.record_outcomes(
            [query.id],
            papers_processed=1,
            new_interactions=2,
            new_evidence=3,
        )
        conn.commit()

        row = conn.execute(
            select(
                db.search_queries.c.outcome_papers_processed,
                db.search_queries.c.outcome_new_interactions,
                db.search_queries.c.outcome_new_evidence,
                db.search_queries.c.attributed_papers_processed,
                db.search_queries.c.attributed_new_interactions,
                db.search_queries.c.attributed_new_evidence,
                db.search_queries.c.improved_graph,
            ).where(db.search_queries.c.id == query.id)
        ).mappings().one()

        assert row["outcome_papers_processed"] == 1
        assert row["outcome_new_interactions"] == 2
        assert row["outcome_new_evidence"] == 3
        assert row["attributed_papers_processed"] == 1.0
        assert row["attributed_new_interactions"] == 2.0
        assert row["attributed_new_evidence"] == 3.0
        assert row["improved_graph"] == 1
    finally:
        conn.close()
        engine.dispose()


def test_query_planner_learns_from_historical_yield():
    engine, conn, repos = _repos()
    try:
        successful = SearchQuery(
            query_text="successful gap",
            source_api="pubmed",
            query_type=QueryType.GAP_FILLING,
            origin="entity_id:done",
            generation_reason="sparse_high_value_entity",
            target_kind="entity",
            target_id="done",
            status="done",
            papers_new=3,
            outcome_papers_processed=2,
            outcome_new_interactions=2,
            outcome_new_evidence=5,
            improved_graph=True,
        )
        pending_learned = SearchQuery(
            query_text="learned gap",
            source_api="pubmed",
            query_type=QueryType.GAP_FILLING,
            origin="entity_id:pending",
            generation_reason="sparse_high_value_entity",
            target_kind="entity",
            target_id="pending",
        )
        pending_generic = SearchQuery(
            query_text="generic initial",
            source_api="pubmed",
            query_type=QueryType.INITIAL,
            origin="seed",
        )
        repos.queries.insert(successful)
        conn.execute(
            db.search_queries.update()
            .where(db.search_queries.c.id == successful.id)
            .values(completed_at="2026-03-22T00:00:00Z")
        )
        repos.queries.insert(pending_learned)
        repos.queries.insert(pending_generic)
        conn.commit()

        planner = QueryPlanner()
        planner.plan_pending_queries(repos, _config())
        conn.commit()

        pending = repos.queries.get_pending(limit=2)

        assert pending[0]["id"] == pending_learned.id
        assert pending[0]["planning_score"] > pending[1]["planning_score"]
    finally:
        conn.close()
        engine.dispose()


def test_targeted_query_generation_includes_unresolved_conflict_hubs():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="STAT3", display_name="STAT3", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="IL6", display_name="IL6", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)

        interaction_a = Interaction(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            interaction_type=InteractionType.SIGNALING,
            direction="A_to_B",
            effect="activates",
        )
        interaction_b = Interaction(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            interaction_type=InteractionType.SIGNALING,
            direction="A_to_B",
            effect="inhibits",
        )
        interaction_a_id, _ = repos.interactions.upsert(interaction_a)
        interaction_b_id, _ = repos.interactions.upsert(interaction_b)
        repos.conflicts.insert(
            Conflict(
                interaction_a_id=interaction_a_id,
                interaction_b_id=interaction_b_id,
                conflict_type=ConflictType.TRUE_CONFLICT,
                conflict_axis="effect",
                status=ConflictStatus.OPEN,
                penalty_weight=1.0,
            )
        )
        conn.commit()

        planner = QueryPlanner()
        added = planner.generate_targeted_queries(repos, _config())
        conn.commit()

        rows = conn.execute(select(db.search_queries)).mappings().all()

        assert added >= 1
        assert any(row["generation_reason"] == "unresolved_conflict_hub" for row in rows)
        assert any(row["target_kind"] == "conflict" for row in rows)
    finally:
        conn.close()
        engine.dispose()


def test_evidence_normalizer_maps_common_context_terms_to_controlled_buckets():
    normalizer = EvidenceNormalizer()
    interaction = ExtractedInteractionRaw(
        entity_a="TP53",
        entity_b="MDM2",
        interaction_type=InteractionType.SIGNALING,
        direction="A_to_B",
        effect="activates",
        evidence_type=EvidenceType.IN_VITRO,
        organism="Homo sapiens",
        tissue_cell_type="HEK293",
        condition="hypoxic stress",
        assay_type="co-immunoprecipitation",
        confidence="high",
        confidence_score=0.9,
        snippet="snippet",
    )

    normalized = normalizer.normalize(interaction)

    assert normalized.normalized_organism == "human"
    assert normalized.normalized_tissue_cell_type == "cell_line:hek293"
    assert normalized.normalized_condition == "hypoxia"
    assert normalized.normalized_assay_type == "protein_interaction:coip"


def test_evidence_insert_persists_literal_claim_and_normalized_context():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="TP53", display_name="TP53", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="MDM2", display_name="MDM2", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)
        interaction_id, _ = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="activates",
            )
        )
        claim = repos.literal_claims.insert(
            LiteralClaimRecord(
                paper_id="pmid:1",
                entity_a_text="TP53",
                entity_b_text="MDM2",
                interaction_type_text="signaling",
                direction_text="A_to_B",
                effect_text="activates",
                evidence_type_text="in_vitro",
                organism_text="Homo sapiens",
                tissue_cell_type_text="HEK293",
                condition_text="hypoxic stress",
                assay_type_text="co-immunoprecipitation",
                confidence_text="high",
                confidence_score=0.9,
                snippet="TP53 activates MDM2 in HEK293 cells during hypoxia.",
            )
        )
        repos.evidence.insert(
            EvidenceRecord(
                claim_id=claim,
                interaction_id=interaction_id,
                paper_id="pmid:1",
                evidence_type=EvidenceType.IN_VITRO,
                confidence="high",
                confidence_score=0.9,
                context=InteractionContext(
                    organism="Homo sapiens",
                    tissue_cell_type="HEK293",
                    condition="hypoxic stress",
                    assay_type="co-immunoprecipitation",
                    normalized_organism="human",
                    normalized_tissue_cell_type="cell_line:hek293",
                    normalized_condition="hypoxia",
                    normalized_assay_type="protein_interaction:coip",
                ),
                snippet="TP53 activates MDM2 in HEK293 cells during hypoxia.",
                verification_status="verified",
                verification_score=0.8,
                adjudication_score=0.85,
            )
        )
        conn.commit()

        ev_row = conn.execute(select(db.evidence)).mappings().one()
        claim_row = conn.execute(select(db.literal_claims)).mappings().one()

        assert ev_row["claim_id"] == claim
        assert ev_row["normalized_organism"] == "human"
        assert ev_row["normalized_tissue_cell_type"] == "cell_line:hek293"
        assert ev_row["normalized_condition"] == "hypoxia"
        assert ev_row["normalized_assay_type"] == "protein_interaction:coip"
        assert claim_row["entity_a_text"] == "TP53"
        assert claim_row["effect_text"] == "activates"
    finally:
        conn.close()
        engine.dispose()


def test_conflict_detection_uses_normalized_context_buckets():
    engine, conn, repos = _repos()
    try:
        entity_a = BiologicalEntity(canonical_name="TP53", display_name="TP53", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="MDM2", display_name="MDM2", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)

        int_a_id, _ = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="activates",
            )
        )
        int_b_id, _ = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="inhibits",
            )
        )
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=int_a_id,
                paper_id="pmid:1",
                evidence_type=EvidenceType.IN_VITRO,
                confidence="high",
                confidence_score=0.8,
                context=InteractionContext(
                    organism="Homo sapiens",
                    tissue_cell_type="HEK293",
                    condition="hypoxic stress",
                    normalized_organism="human",
                    normalized_tissue_cell_type="cell_line:hek293",
                    normalized_condition="hypoxia",
                ),
                snippet="snippet a",
            )
        )
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=int_b_id,
                paper_id="pmid:2",
                evidence_type=EvidenceType.IN_VITRO,
                confidence="high",
                confidence_score=0.8,
                context=InteractionContext(
                    organism="Human",
                    tissue_cell_type="293T",
                    condition="hypoxia",
                    normalized_organism="human",
                    normalized_tissue_cell_type="cell_line:hek293",
                    normalized_condition="hypoxia",
                ),
                snippet="snippet b",
            )
        )
        conn.commit()

        from autobioresearch.conflict.detector import ConflictDetector
        detector = ConflictDetector()
        conflict = detector._classify_rule_based(
            repos.interactions.get_by_id(int_a_id),
            repos.interactions.get_by_id(int_b_id),
            repos,
        )

        assert conflict is not None
        assert conflict.conflict_type == ConflictType.AMBIGUOUS
        assert conflict.context_difference == {}
    finally:
        conn.close()
        engine.dispose()


def test_conflict_resolver_refreshes_adjudication_and_uses_it_in_prompt():
    engine, conn, repos = _repos()
    try:
        conn.execute(db.papers.insert().values(
            id="pmid:1",
            source="pubmed",
            title="Paper one",
            abstract="A" * 100,
            authors="[]",
            journal="J",
            year=2023,
            doi=None,
            pmc_id=None,
            fetch_status="abstract_only",
            extraction_status="done",
            query_ids="[]",
            created_at="2026-03-22T00:00:00Z",
            updated_at="2026-03-22T00:00:00Z",
        ))
        conn.execute(db.papers.insert().values(
            id="pmid:2",
            source="pubmed",
            title="Paper two",
            abstract="B" * 100,
            authors="[]",
            journal="J",
            year=2012,
            doi=None,
            pmc_id=None,
            fetch_status="abstract_only",
            extraction_status="done",
            query_ids="[]",
            created_at="2026-03-22T00:00:00Z",
            updated_at="2026-03-22T00:00:00Z",
        ))
        entity_a = BiologicalEntity(canonical_name="STAT3", display_name="STAT3", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="IL6", display_name="IL6", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)
        int_a_id, _ = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="activates",
            )
        )
        int_b_id, _ = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="inhibits",
            )
        )
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=int_a_id,
                paper_id="pmid:1",
                evidence_type=EvidenceType.IN_VIVO,
                confidence="high",
                confidence_score=0.9,
                context=InteractionContext(
                    organism="Homo sapiens",
                    normalized_organism="human",
                    normalized_tissue_cell_type="cell_line:hek293",
                    normalized_condition="hypoxia",
                ),
                snippet="STAT3 activates IL6 under hypoxia.",
                verification_status="verified",
                verification_score=0.9,
            )
        )
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=int_b_id,
                paper_id="pmid:2",
                evidence_type=EvidenceType.COMPUTATIONAL,
                confidence="medium",
                confidence_score=0.4,
                context=InteractionContext(
                    organism="Homo sapiens",
                    normalized_organism="human",
                ),
                snippet="Model predicts IL6 inhibition.",
                verification_status="needs_review",
                verification_score=0.3,
            )
        )
        conflict = Conflict(
            interaction_a_id=int_a_id,
            interaction_b_id=int_b_id,
            conflict_type=ConflictType.AMBIGUOUS,
            conflict_axis="effect",
            status=ConflictStatus.OPEN,
            penalty_weight=0.5,
        )
        repos.conflicts.insert(conflict)
        conn.commit()

        llm = _StubLLM({
            "conflict_type": "context_dependent",
            "conflict_axis": "effect",
            "context_difference": {"condition": "hypoxia vs unspecified"},
            "is_genuine_conflict": False,
            "reasoning": "Different evidence quality and context support context dependence.",
            "suggested_queries": [],
            "penalty_weight": 0.2,
        })
        resolver = ConflictResolver(llm=llm)
        changed = resolver.analyze_and_resolve(repos, _config())
        conn.commit()

        adjudicated = conn.execute(
            select(db.evidence.c.adjudication_score, db.evidence.c.adjudication_notes)
        ).mappings().all()

        assert changed == 1
        assert all(row["adjudication_score"] is not None for row in adjudicated)
        assert "Adjudication summary" in llm.calls[0]["user"]
        assert "normalized contexts" in llm.calls[0]["user"].lower()
    finally:
        conn.close()
        engine.dispose()


def test_adjudicator_recommends_context_dependent_resolution_for_distinct_supported_contexts():
    adjudicator = EvidenceAdjudicator()

    recommendation = adjudicator.recommend_conflict_outcome(
        [{
            "paper_id": "pmid:1",
            "adjudication_score": 0.7,
            "verification_score": 0.8,
            "normalized_organism": "human",
            "normalized_tissue_cell_type": "cell_line:hek293",
            "normalized_condition": "hypoxia",
        }],
        [{
            "paper_id": "pmid:2",
            "adjudication_score": 0.68,
            "verification_score": 0.82,
            "normalized_organism": "human",
            "normalized_tissue_cell_type": "cell_line:hela",
            "normalized_condition": "lps_stimulation",
        }],
        current_type="ambiguous",
    )

    assert recommendation is not None
    assert recommendation["status"] == "resolved"
    assert recommendation["conflict_type"] == "context_dependent"


def test_conflict_resolver_auto_resolves_strong_same_context_conflict_without_llm():
    engine, conn, repos = _repos()
    try:
        conn.execute(db.papers.insert().values(
            id="pmid:1",
            source="pubmed",
            title="Strong paper",
            abstract="A" * 100,
            authors="[]",
            journal="J",
            year=2024,
            doi=None,
            pmc_id=None,
            fetch_status="abstract_only",
            extraction_status="done",
            query_ids="[]",
            created_at="2026-03-22T00:00:00Z",
            updated_at="2026-03-22T00:00:00Z",
        ))
        conn.execute(db.papers.insert().values(
            id="pmid:2",
            source="pubmed",
            title="Weak paper",
            abstract="B" * 100,
            authors="[]",
            journal="J",
            year=2008,
            doi=None,
            pmc_id=None,
            fetch_status="abstract_only",
            extraction_status="done",
            query_ids="[]",
            created_at="2026-03-22T00:00:00Z",
            updated_at="2026-03-22T00:00:00Z",
        ))
        entity_a = BiologicalEntity(canonical_name="EGFR", display_name="EGFR", entity_type="protein")
        entity_b = BiologicalEntity(canonical_name="ERK", display_name="ERK", entity_type="protein")
        repos.entities.insert(entity_a)
        repos.entities.insert(entity_b)
        int_a_id, _ = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="activates",
            )
        )
        int_b_id, _ = repos.interactions.upsert(
            Interaction(
                entity_a_id=entity_a.id,
                entity_b_id=entity_b.id,
                interaction_type=InteractionType.SIGNALING,
                direction="A_to_B",
                effect="inhibits",
            )
        )
        common_context = InteractionContext(
            organism="Homo sapiens",
            tissue_cell_type="HEK293",
            condition="hypoxia",
            normalized_organism="human",
            normalized_tissue_cell_type="cell_line:hek293",
            normalized_condition="hypoxia",
        )
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=int_a_id,
                paper_id="pmid:1",
                evidence_type=EvidenceType.IN_VIVO,
                confidence="high",
                confidence_score=0.95,
                context=common_context,
                snippet="Strong in vivo evidence.",
                verification_status="verified",
                verification_score=0.95,
            )
        )
        repos.evidence.insert(
            EvidenceRecord(
                interaction_id=int_b_id,
                paper_id="pmid:2",
                evidence_type=EvidenceType.COMPUTATIONAL,
                confidence="low",
                confidence_score=0.2,
                context=common_context,
                snippet="Weak computational evidence.",
                verification_status="needs_review",
                verification_score=0.2,
            )
        )
        conflict = Conflict(
            interaction_a_id=int_a_id,
            interaction_b_id=int_b_id,
            conflict_type=ConflictType.AMBIGUOUS,
            conflict_axis="effect",
            status=ConflictStatus.OPEN,
            penalty_weight=0.5,
        )
        repos.conflicts.insert(conflict)
        conn.commit()

        llm = _StubLLM({
            "conflict_type": "ambiguous",
            "conflict_axis": "effect",
            "context_difference": {},
            "is_genuine_conflict": True,
            "reasoning": "fallback",
            "suggested_queries": [],
            "penalty_weight": 0.5,
        })
        resolver = ConflictResolver(llm=llm)
        changed = resolver.analyze_and_resolve(repos, _config())
        conn.commit()

        row = conn.execute(select(db.conflicts)).mappings().one()

        assert changed == 1
        assert row["status"] == "resolved"
        assert row["conflict_type"] == "true_conflict"
        assert llm.calls == []
    finally:
        conn.close()
        engine.dispose()
