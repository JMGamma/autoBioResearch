"""
AutoBioResearch main orchestration loop.

Two arms run each cycle:
  Arm 1: Fetch papers → extract entities/interactions → build knowledge graph
  Arm 2: Detect conflicts → classify → generate resolution queries

The score metric (entity*interaction density vs. conflict penalty) guides the loop.
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import math
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from autobioresearch.config import AppConfig
from autobioresearch.crawlers.pmc_fulltext import PMCFullTextFetcher
from autobioresearch.crawlers.pubmed import PubMedCrawler
from autobioresearch.crawlers.semantic_scholar import SemanticScholarCrawler
from autobioresearch.database import create_all, init_engine
from autobioresearch.extractor.claude_client import LLMClient
from autobioresearch.extractor.extractor import PaperExtractor
from autobioresearch.extractor.normalizer import EntityNormalizer
from autobioresearch.extractor.verifier import InteractionVerifier
from autobioresearch.metrics import compute_score
from autobioresearch.perturbation.sign_map import unmapped_effect_summary
from autobioresearch.models import ExtractedInteractionRaw, QueryType, SearchQuery
from autobioresearch.planner import QueryPlanner
from autobioresearch.storage.repositories import Repositories
from autobioresearch.utils.logging_config import setup_logging, setup_truncation_review_logging

logger = logging.getLogger(__name__)

_shutdown = False
_shutdown_reason = "normal_exit"
_shutdown_signal_count = 0
_current_cycle = 0
_current_phase = "startup"


def _signal_name(sig: int) -> str:
    try:
        return signal.Signals(sig).name
    except ValueError:
        return str(sig)


def _set_runtime_context(*, cycle: Optional[int] = None, phase: Optional[str] = None) -> None:
    global _current_cycle, _current_phase
    if cycle is not None:
        _current_cycle = cycle
    if phase is not None:
        _current_phase = phase


def _flush_log_handlers() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass


def _log_shutdown_event(message: str, *, level: int = logging.WARNING) -> None:
    logger.log(level, message)
    _flush_log_handlers()


def _handle_sigint(sig, frame):
    global _shutdown, _shutdown_reason, _shutdown_signal_count
    _shutdown_signal_count += 1
    sig_name = _signal_name(sig)

    if _shutdown_signal_count == 1:
        _shutdown = True
        _shutdown_reason = f"signal:{sig_name}"
        _log_shutdown_event(
            "Shutdown requested via "
            f"{sig_name} during cycle={_current_cycle or 'n/a'} phase={_current_phase}"
        )
        print("\nShutdown requested. Finishing current work where possible...", flush=True)
        return

    # A second signal is treated as a force-exit request.
    _shutdown_reason = f"forced_signal:{sig_name}"
    _log_shutdown_event(
        "Second shutdown signal received; forcing immediate exit "
        f"via {sig_name} during cycle={_current_cycle or 'n/a'} phase={_current_phase}",
        level=logging.ERROR,
    )
    print("\nForcing immediate shutdown...", flush=True)
    os._exit(1)


def _log_process_exit() -> None:
    _log_shutdown_event(
        "Process exiting with reason="
        f"{_shutdown_reason} cycle={_current_cycle or 'n/a'} phase={_current_phase}",
        level=logging.INFO,
    )


# ---------------------------------------------------------------------------
# Seed queries
# ---------------------------------------------------------------------------

def _seed_initial_queries(repos: Repositories, config: AppConfig) -> int:
    """Insert seed queries from config if no queries exist yet."""
    if repos.queries.count_pending() > 0:
        return 0
    inserted = 0
    for q in config.seed_queries:
        query = SearchQuery(
            query_text=q["query"],
            source_api=q.get("source_api", "pubmed"),
            query_type=QueryType.INITIAL,
            origin="seed",
        )
        repos.queries.insert(query)
        inserted += 1
    if inserted:
        logger.info(f"Seeded {inserted} initial search queries")
    return inserted


# ---------------------------------------------------------------------------
# Paper fetching (Arm 1a)
# ---------------------------------------------------------------------------

def _fetch_papers_for_query(
    query_row: dict,
    pubmed: PubMedCrawler,
    s2: Optional[SemanticScholarCrawler],
) -> tuple[bool, list]:
    """Fetch papers for a single query. Thread-safe (no DB writes)."""
    q_text = query_row["query_text"]
    api = query_row["source_api"]
    max_results = int(query_row.get("papers_per_query", 0) or 0)

    try:
        if api == "pubmed":
            return True, pubmed.search(q_text, max_results=max_results)
        elif api == "semantic_scholar":
            if s2 is None:
                logger.debug(f"Semantic Scholar disabled; routing query to PubMed: {q_text!r}")
                return True, pubmed.search(q_text, max_results=max_results)
            return True, s2.search(q_text, max_results=max_results)
        else:
            logger.warning(f"Unknown source_api: {api}, defaulting to pubmed")
            return True, pubmed.search(q_text, max_results=max_results)
    except Exception as e:
        logger.warning(f"Fetch failed for query '{q_text}' on {api}: {e}")
        return False, []


def _empty_fetch_stats() -> dict:
    return {
        "queries_run": 0,
        "queries_failed": 0,
        "queries_with_new_papers": 0,
        "papers_fetched_total": 0,
        "papers_fetched_new": 0,
    }


def _empty_extraction_stats() -> dict:
    return {
        "papers_processed": 0,
        "papers_failed": 0,
        "new_entities": 0,
        "new_interactions": 0,
        "new_evidence": 0,
        "claims_verified": 0,
        "claims_needing_review": 0,
        "query_linked_evidence": 0,
        "conflicts_reopened": 0,
    }


def _merge_stats(into: dict, delta: dict) -> dict:
    for key, value in delta.items():
        into[key] = into.get(key, 0) + value
    return into


def _fetch_single_query(
    repos: Repositories,
    config: AppConfig,
    query_row: dict,
    pubmed: PubMedCrawler,
    s2: Optional[SemanticScholarCrawler],
) -> dict:
    """Fetch papers for one query, persist them, and update query status."""
    q = dict(query_row)
    q["papers_per_query"] = config.papers_per_query
    repos.queries.mark_running(q["id"])

    success, papers = _fetch_papers_for_query(q, pubmed, s2)
    if not success:
        repos.queries.mark_failed(q["id"], error="fetch_failed")
        return {
            "queries_run": 1,
            "queries_failed": 1,
            "queries_with_new_papers": 0,
            "papers_fetched_total": 0,
            "papers_fetched_new": 0,
        }

    is_conflict_query = q.get("query_type") == QueryType.CONFLICT_RESOLUTION.value
    for p in papers:
        p.query_ids = [q["id"]]
        p.priority = 1 if is_conflict_query else 0

    new_count = repos.papers.upsert_many(papers)
    repos.queries.mark_done(q["id"], papers_found=len(papers), papers_new=new_count)
    return {
        "queries_run": 1,
        "queries_failed": 0,
        "queries_with_new_papers": 1 if papers else 0,
        "papers_fetched_total": len(papers),
        "papers_fetched_new": new_count,
    }


def run_fetch_phase(
    repos: Repositories,
    config: AppConfig,
    pubmed: PubMedCrawler,
    s2: Optional[SemanticScholarCrawler],
) -> dict:
    """Fetch papers for pending queries. Returns fetch telemetry for the cycle."""
    pending = repos.queries.get_pending(config.queries_per_cycle)
    if not pending:
        return {
            "queries_run": 0,
            "queries_failed": 0,
            "queries_with_new_papers": 0,
            "papers_fetched_total": 0,
            "papers_fetched_new": 0,
        }

    for q in pending:
        q["papers_per_query"] = config.papers_per_query

    logger.info(f"Fetching papers for {len(pending)} queries")

    # Mark all as running first
    for q in pending:
        repos.queries.mark_running(q["id"])

    papers_by_query: dict[str, list] = {}
    fetch_success_by_query: dict[str, bool] = {}

    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=config.crawler_threads) as executor:
        future_to_query = {
            executor.submit(_fetch_papers_for_query, q, pubmed, s2): q
            for q in pending
        }
        for future in as_completed(future_to_query):
            q = future_to_query[future]
            try:
                success, papers = future.result()
                fetch_success_by_query[q["id"]] = success
                papers_by_query[q["id"]] = papers
            except Exception as e:
                logger.warning(f"Query {q['id']} fetch raised: {e}")
                fetch_success_by_query[q["id"]] = False
                papers_by_query[q["id"]] = []

    # Persist papers and update query statuses
    total_new = 0
    for q in pending:
        papers = papers_by_query.get(q["id"], [])
        if not fetch_success_by_query.get(q["id"], False):
            repos.queries.mark_failed(q["id"], error="fetch_failed")
            continue
        is_conflict_query = q.get("query_type") == QueryType.CONFLICT_RESOLUTION.value
        for p in papers:
            p.query_ids = [q["id"]]
            p.priority = 1 if is_conflict_query else 0

        new_count = repos.papers.upsert_many(papers)
        total_new += new_count
        repos.queries.mark_done(q["id"], papers_found=len(papers), papers_new=new_count)

    papers_total = sum(len(v) for v in papers_by_query.values())
    queries_failed = sum(1 for ok in fetch_success_by_query.values() if not ok)
    queries_with_new_papers = sum(
        1
        for q in pending
        if fetch_success_by_query.get(q["id"], False) and len(papers_by_query.get(q["id"], [])) > 0
    )
    logger.info(f"Fetched {papers_total} papers total, {total_new} new")
    return {
        "queries_run": len(pending),
        "queries_failed": queries_failed,
        "queries_with_new_papers": queries_with_new_papers,
        "papers_fetched_total": papers_total,
        "papers_fetched_new": total_new,
    }


# ---------------------------------------------------------------------------
# Extraction phase (Arm 1b)
# ---------------------------------------------------------------------------

def run_extraction_phase(
    repos: Repositories,
    config: AppConfig,
    extractor: PaperExtractor,
    pmc_fetcher: PMCFullTextFetcher,
    conn,
    limit: int | None = None,
) -> dict:
    """
    Extract entities/interactions from pending papers.
    Returns extraction telemetry for the cycle.
    Commits after each paper so data is visible in the DB progressively.
    """
    pending = repos.papers.get_pending_extraction(limit or config.papers_per_cycle)
    if not pending:
        return _empty_extraction_stats()

    # Refresh the normalizer's DB connection — the one used at construction is closed.
    extractor._normalizer.set_repo(repos.entities)

    logger.info(f"Extracting from {len(pending)} papers")

    papers_processed = 0
    papers_failed = 0
    total_entities = 0
    total_interactions = 0
    total_evidence = 0
    claims_verified = 0
    claims_needing_review = 0
    query_linked_evidence = 0
    conflicts_reopened = 0

    for i, paper_row in enumerate(pending):
        if _shutdown:
            remaining = len(pending) - i
            logger.info(
                f"Shutdown requested — stopping extraction early. "
                f"{remaining} paper{'s' if remaining != 1 else ''} left to extract."
            )
            break

        paper_id = paper_row["id"]
        title = paper_row.get("title") or ""
        abstract = paper_row.get("abstract") or ""
        pmc_id = paper_row.get("pmc_id")

        # Choose text source: prefer full text (transient), fall back to abstract
        text = abstract
        full_text_used = False

        if pmc_id:
            full_text = pmc_fetcher.fetch(pmc_id)
            if full_text and len(full_text) > len(abstract):
                text = full_text
                full_text_used = True

        if not text or len(text.strip()) < 50:
            repos.papers.mark_extraction_failed(paper_id, "Insufficient text")
            papers_failed += 1
            continue

        try:
            result = extractor.extract(paper_id, title, text)
            msg = (
                f"  {paper_id}: LLM returned {len(result.entities)} entities, "
                f"{len(result.interactions)} interactions — persisting..."
            )
            logger.info(msg)
            ne, ni, nev = extractor.persist(result, repos)
            interaction_ids = list({
                ev["interaction_id"]
                for ev in repos.evidence.get_for_paper(result.paper_id)
            })
            conflicts_reopened += repos.conflicts.reopen_for_interactions(interaction_ids)

            repos.papers.mark_extraction_done(paper_id)
            papers_processed += 1
            total_entities += ne
            total_interactions += ni
            total_evidence += nev
            claims_verified += sum(
                1 for ev in repos.evidence.get_for_paper(result.paper_id)
                if ev.get("verification_status") == "verified"
            )
            claims_needing_review += sum(
                1 for ev in repos.evidence.get_for_paper(result.paper_id)
                if ev.get("verification_status") == "needs_review"
            )
            query_ids = json.loads(paper_row.get("query_ids") or "[]")
            if query_ids:
                query_linked_evidence += nev
                repos.queries.record_outcomes(
                    query_ids,
                    papers_processed=1,
                    new_interactions=ni,
                    new_evidence=nev,
                )

            logger.info(
                f"  {paper_id}: +{ne} entities, +{ni} interactions, +{nev} evidence"
                + (" [full text]" if full_text_used else " [abstract]")
            )

        except Exception as e:
            logger.error(f"Extraction failed for {paper_id}: {e}", exc_info=True)
            repos.papers.mark_extraction_failed(paper_id, str(e)[:2000])
            papers_failed += 1

        # Commit after each paper so data is visible immediately and a later
        # crash doesn't lose the whole batch.
        conn.commit()

    return {
        "papers_processed": papers_processed,
        "papers_failed": papers_failed,
        "new_entities": total_entities,
        "new_interactions": total_interactions,
        "new_evidence": total_evidence,
        "claims_verified": claims_verified,
        "claims_needing_review": claims_needing_review,
        "query_linked_evidence": query_linked_evidence,
        "conflicts_reopened": conflicts_reopened,
    }


def run_interleaved_fetch_extraction_phase(
    repos: Repositories,
    config: AppConfig,
    pubmed: PubMedCrawler,
    s2: Optional[SemanticScholarCrawler],
    extractor: PaperExtractor,
    pmc_fetcher: PMCFullTextFetcher,
    conn,
) -> tuple[dict, dict]:
    """
    Interleave retrieval with extraction so API calls are naturally spaced out by
    slower per-paper processing work instead of bursting at cycle start.
    """
    pending = repos.queries.get_pending(config.queries_per_cycle)
    if not pending:
        return _empty_fetch_stats(), run_extraction_phase(
            repos, config, extractor, pmc_fetcher, conn, limit=config.papers_per_cycle
        )

    logger.info(
        "Interleaving fetch and extraction for %d queries with up to %d papers this cycle",
        len(pending),
        config.papers_per_cycle,
    )

    fetch_stats = _empty_fetch_stats()
    extraction_stats = _empty_extraction_stats()

    for idx, query_row in enumerate(pending):
        if _shutdown:
            logger.info("Shutdown requested — stopping fetch/extract interleave early.")
            break

        _merge_stats(fetch_stats, _fetch_single_query(repos, config, query_row, pubmed, s2))
        conn.commit()

        consumed = extraction_stats["papers_processed"] + extraction_stats["papers_failed"]
        remaining_paper_budget = max(0, config.papers_per_cycle - consumed)
        remaining_queries = len(pending) - idx
        if remaining_paper_budget <= 0:
            continue

        slice_size = max(1, math.ceil(remaining_paper_budget / remaining_queries))
        _merge_stats(
            extraction_stats,
            run_extraction_phase(
                repos,
                config,
                extractor,
                pmc_fetcher,
                conn,
                limit=min(slice_size, remaining_paper_budget),
            ),
        )

    consumed = extraction_stats["papers_processed"] + extraction_stats["papers_failed"]
    remaining_paper_budget = max(0, config.papers_per_cycle - consumed)
    if remaining_paper_budget > 0 and not _shutdown:
        _merge_stats(
            extraction_stats,
            run_extraction_phase(
                repos,
                config,
                extractor,
                pmc_fetcher,
                conn,
                limit=remaining_paper_budget,
            ),
        )

    return fetch_stats, extraction_stats


# ---------------------------------------------------------------------------
# Query generation for score-driven targeted search
# ---------------------------------------------------------------------------

def run_query_generation_phase(repos: Repositories, config: AppConfig, planner: QueryPlanner) -> int:
    """Generate targeted score-aware queries for the next cycle."""
    added = planner.generate_targeted_queries(repos, config)
    if added:
        logger.info(f"Added {added} targeted search queries")
    return added


def run_verification_phase(
    repos: Repositories,
    config: AppConfig,
    verifier: InteractionVerifier | None,
) -> dict:
    """
    Re-verify unresolved conflict-linked evidence using stored paper text/snippets.
    Returns verifier telemetry for this phase.
    """
    if verifier is None or not config.verification_enabled:
        return {"claims_verified": 0, "claims_needing_review": 0}

    candidates = repos.evidence.get_conflict_verification_candidates(
        limit=config.claims_to_verify_per_cycle
    )
    if not candidates:
        return {"claims_verified": 0, "claims_needing_review": 0}

    verified = 0
    needs_review = 0
    for row in candidates:
        interaction = ExtractedInteractionRaw(
            entity_a=row["entity_a_name"],
            entity_b=row["entity_b_name"],
            interaction_type=row["interaction_type"],
            direction=row.get("direction") or "undirected",
            effect=row.get("effect"),
            evidence_type="unknown",
            organism=row.get("organism"),
            tissue_cell_type=row.get("tissue_cell_type"),
            condition=row.get("condition"),
            confidence=row.get("confidence") or "low",
            confidence_score=float(row.get("confidence_score") or 0.0),
            snippet=row.get("snippet") or "",
        )
        verified_interaction = verifier.verify_interaction(
            row["paper_id"],
            row.get("paper_title") or "",
            row.get("paper_abstract") or "",
            interaction,
            conflict_linked=True,
        )
        repos.evidence.update_verification(
            row["evidence_id"],
            status=verified_interaction.verification_status or "needs_review",
            score=verified_interaction.verification_score,
            notes=verified_interaction.verification_notes,
        )
        if verified_interaction.verification_status == "verified":
            verified += 1
        else:
            needs_review += 1

    return {"claims_verified": verified, "claims_needing_review": needs_review}


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle(
    cycle: int,
    config: AppConfig,
    engine,
    pubmed: PubMedCrawler,
    s2: Optional[SemanticScholarCrawler],
    pmc_fetcher: PMCFullTextFetcher,
    extractor: PaperExtractor,
    conflict_detector=None,
    conflict_resolver=None,
    verifier: InteractionVerifier | None = None,
    planner: QueryPlanner | None = None,
    conflicts_only: bool = False,
) -> dict:
    """Run one full autoresearch cycle. Returns cycle stats."""

    with engine.connect() as conn:
        repos = Repositories(conn)
        _set_runtime_context(cycle=cycle, phase="cycle_start")

        logger.info(f"{'='*60}")
        logger.info(f"CYCLE {cycle} START" + (" [conflicts-only]" if conflicts_only else ""))
        logger.info(f"{'='*60}")

        # --- Phase 0: Seed if needed ---
        if not conflicts_only:
            _set_runtime_context(phase="seed_queries")
            _seed_initial_queries(repos, config)
            conn.commit()

        # --- Phase 0.5: plan pending work ---
        if planner and not conflicts_only:
            _set_runtime_context(phase="plan_queries")
            planner.plan_pending_queries(repos, config)
            conn.commit()

        # --- Phase 1-2: Interleave fetch + extract ---
        if conflicts_only:
            fetch_stats = _empty_fetch_stats()
            extraction_stats = _empty_extraction_stats()
        else:
            _set_runtime_context(phase="fetch_extract")
            fetch_stats, extraction_stats = run_interleaved_fetch_extraction_phase(
                repos, config, pubmed, s2, extractor, pmc_fetcher, conn
            )

        # --- Phase 3: Detect conflicts (Arm 2) ---
        new_conflicts = 0
        conflicts_resolved = 0
        if _shutdown:
            logger.info("Shutdown in progress — skipping conflict/verification phases.")
        if conflict_detector and not _shutdown:
            _set_runtime_context(phase="detect_conflicts")
            new_conflicts = conflict_detector.detect(repos, config)
            conn.commit()

        # --- Phase 4: Classify + resolve conflicts ---
        if conflict_resolver and not _shutdown:
            _set_runtime_context(phase="resolve_conflicts")
            conflicts_resolved = conflict_resolver.analyze_and_resolve(repos, config)
            conn.commit()
            _set_runtime_context(phase="generate_resolution_queries")
            resolution_queries = conflict_resolver.generate_follow_up_queries(repos, config)
            conn.commit()
        else:
            resolution_queries = 0

        _set_runtime_context(phase="verify_claims")
        if not _shutdown:
            verification_stats = run_verification_phase(repos, config, verifier)
            conn.commit()
        else:
            verification_stats = {"claims_verified": 0, "claims_needing_review": 0}

        # --- Phase 5: Generate targeted next-step queries ---
        _set_runtime_context(phase="generate_targeted_queries")
        gap_queries = run_query_generation_phase(repos, config, planner) if planner and not conflicts_only else 0
        conn.commit()

        # --- Phase 6: Score ---
        _set_runtime_context(phase="score")
        score, stats = compute_score(repos, config)
        repos.metrics.log(
            cycle=cycle,
            papers_processed=extraction_stats["papers_processed"],
            papers_fetched_total=fetch_stats["papers_fetched_total"],
            papers_fetched_new=fetch_stats["papers_fetched_new"],
            papers_extraction_failed=extraction_stats["papers_failed"],
            new_entities=extraction_stats["new_entities"],
            new_interactions=extraction_stats["new_interactions"],
            new_evidence=extraction_stats["new_evidence"],
            evidence_accepted=extraction_stats["new_evidence"],
            claims_verified=extraction_stats["claims_verified"] + verification_stats["claims_verified"],
            claims_needing_review=extraction_stats["claims_needing_review"] + verification_stats["claims_needing_review"],
            new_conflicts=new_conflicts,
            conflicts_reopened=extraction_stats["conflicts_reopened"],
            conflicts_resolved=conflicts_resolved,
            queries_generated=resolution_queries + gap_queries,
            queries_with_new_papers=fetch_stats["queries_with_new_papers"],
            query_linked_evidence=extraction_stats["query_linked_evidence"],
            **stats,
        )
        conn.commit()

        # --- Effect sign-map coverage check ---
        # Logs unmapped effect strings so we can spot when a new batch warrants a pass.
        _set_runtime_context(phase="signmap_check")
        sm = unmapped_effect_summary(conn)
        if sm["total_unmapped_interactions"] > 0:
            top_str = ", ".join(
                f"'{x['effect']}'×{x['count']}" for x in sm["top_unmapped"]
            )
            logger.info(
                "Sign-map coverage: %d interactions / %d distinct effects unmapped. Top: %s",
                sm["total_unmapped_interactions"],
                sm["distinct_unmapped_effects"],
                top_str,
            )
        else:
            logger.debug("Sign-map coverage: all effect strings mapped.")

    return {"cycle": cycle, "score": score, **stats}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AutoBioResearch autonomous loop")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--cycles", type=int, default=0, help="Max cycles (0=forever)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-conflicts", action="store_true", help="Skip conflict detection/resolution (Arm 2)")
    mode.add_argument("--conflicts-only", action="store_true", help="Run conflict resolution arm only (skip fetch/extract)")
    args = parser.parse_args()

    config = AppConfig.from_yaml(args.config)
    if args.cycles:
        config.max_cycles = args.cycles

    setup_logging(config.log_level, config.log_file)
    setup_truncation_review_logging(config.truncation_review_log_file)
    atexit.register(_log_process_exit)
    signal.signal(signal.SIGINT, _handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigint)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_sigint)

    logger.info("AutoBioResearch starting up")
    logger.info(
        "Process info | pid=%s ppid=%s python=%s cwd=%s argv=%s",
        os.getpid(),
        os.getppid(),
        sys.executable,
        os.getcwd(),
        json.dumps(sys.argv),
    )
    logger.info(f"LLM: {config.llm_api_type} / {config.llm_model}")
    logger.info(f"Database: {config.db_path}")

    # Initialize DB
    engine = init_engine(config.db_path)
    create_all(engine)

    # Build components
    pubmed = PubMedCrawler(
        requests_per_second=config.pubmed_requests_per_second,
        ncbi_api_key=config.ncbi_api_key,
    )
    s2: Optional[SemanticScholarCrawler] = None
    if config.semantic_scholar_enabled:
        s2 = SemanticScholarCrawler(
            requests_per_second=config.semantic_scholar_requests_per_second,
            semantic_scholar_api_key=config.semantic_scholar_api_key,
            contact_email=config.contact_email,
        )
    else:
        logger.info("Semantic Scholar disabled (semantic_scholar_enabled=false); S2 queries will route to PubMed")
    pmc_fetcher = PMCFullTextFetcher(ncbi_api_key=config.ncbi_api_key)
    llm = LLMClient(config)

    # External entity resolver (UniProt + ChEBI) — shared across all cycles,
    # results cached in memory so repeated names are free after first lookup.
    entity_resolver = None
    if config.entity_resolution_enabled:
        from autobioresearch.crawlers.entity_resolvers import EntityResolver
        entity_resolver = EntityResolver(
            requests_per_second=config.entity_resolution_requests_per_second,
            timeout=config.entity_resolution_timeout,
        )
        logger.info("Entity resolution enabled (UniProt + ChEBI)")

    # Normalizer needs a DB connection — we'll pass a fresh one per cycle.
    # Seed aliases only need a one-time connection at startup.
    with engine.connect() as conn:
        from autobioresearch.storage.repositories import EntityRepo
        entity_repo = EntityRepo(conn)
        normalizer = EntityNormalizer(
            entity_repo=entity_repo,
            fuzzy_threshold=config.fuzzy_match_threshold,
            entity_resolver=entity_resolver,
        )
        conn.commit()

    extractor = PaperExtractor(config=config, llm=llm, normalizer=normalizer)
    verifier = InteractionVerifier(config=config, llm=llm)
    planner = QueryPlanner()

    # Conflict components (Arm 2)
    conflict_detector = None
    conflict_resolver = None
    if not args.no_conflicts:
        from autobioresearch.conflict.detector import ConflictDetector
        from autobioresearch.conflict.resolver import ConflictResolver
        conflict_detector = ConflictDetector()
        conflict_resolver = ConflictResolver(llm=llm)

    conflicts_only = args.conflicts_only
    if conflicts_only:
        logger.info("Running in conflicts-only mode — fetch/extract arm disabled")

    # Main loop
    cycle = 0
    while not _shutdown:
        cycle += 1

        try:
            _set_runtime_context(cycle=cycle, phase="run_cycle")
            stats = run_cycle(
                cycle=cycle,
                config=config,
                engine=engine,
                pubmed=pubmed,
                s2=s2,
                pmc_fetcher=pmc_fetcher,
                extractor=extractor,
                conflict_detector=conflict_detector,
                conflict_resolver=conflict_resolver,
                verifier=verifier,
                planner=planner,
                conflicts_only=conflicts_only,
            )
            logger.info(f"Cycle {cycle} complete | Score: {stats['score']:.2f}")
        except Exception as e:
            logger.error(f"Cycle {cycle} failed: {e}", exc_info=True)

        if config.max_cycles and cycle >= config.max_cycles:
            logger.info(f"Reached max_cycles={config.max_cycles}. Stopping.")
            break

        if not _shutdown:
            _set_runtime_context(cycle=cycle, phase="sleep")
            logger.info(f"Sleeping {config.cycle_sleep_seconds}s until next cycle...")
            # Sleep in small increments to allow clean shutdown
            for _ in range(config.cycle_sleep_seconds):
                if _shutdown:
                    break
                time.sleep(1)

    _set_runtime_context(phase="stopped")
    logger.info(f"AutoBioResearch stopped. reason={_shutdown_reason}")


if __name__ == "__main__":
    main()
