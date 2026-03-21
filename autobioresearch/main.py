"""
AutoBioResearch main orchestration loop.

Two arms run each cycle:
  Arm 1: Fetch papers → extract entities/interactions → build knowledge graph
  Arm 2: Detect conflicts → classify → generate resolution queries

The score metric (entity*interaction density vs. conflict penalty) guides the loop.
"""
from __future__ import annotations

import argparse
import logging
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
from autobioresearch.metrics import compute_score
from autobioresearch.models import QueryType, SearchQuery
from autobioresearch.storage.repositories import Repositories
from autobioresearch.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)

_shutdown = False


def _handle_sigint(sig, frame):
    # os._exit() terminates the process immediately at the OS level.
    # This is the only approach that works on Windows when the main thread is
    # blocked inside a C extension (e.g. httpx waiting for the LLM response).
    # Per-paper commits mean the DB is consistent up to the last completed paper.
    print("\nShutting down...", flush=True)
    os._exit(0)


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
    s2: SemanticScholarCrawler,
) -> list:
    """Fetch papers for a single query. Thread-safe (no DB writes)."""
    q_text = query_row["query_text"]
    api = query_row["source_api"]

    try:
        if api == "pubmed":
            return pubmed.search(q_text)
        elif api == "semantic_scholar":
            return s2.search(q_text)
        else:
            logger.warning(f"Unknown source_api: {api}, defaulting to pubmed")
            return pubmed.search(q_text)
    except Exception as e:
        logger.warning(f"Fetch failed for query '{q_text}' on {api}: {e}")
        return []


def run_fetch_phase(
    repos: Repositories,
    config: AppConfig,
    pubmed: PubMedCrawler,
    s2: SemanticScholarCrawler,
) -> int:
    """Fetch papers for pending queries. Returns count of newly inserted papers."""
    pending = repos.queries.get_pending(config.queries_per_cycle)
    if not pending:
        return 0

    logger.info(f"Fetching papers for {len(pending)} queries")

    # Mark all as running first
    for q in pending:
        repos.queries.mark_running(q["id"])

    papers_by_query: dict[str, list] = {}

    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=config.crawler_threads) as executor:
        future_to_query = {
            executor.submit(_fetch_papers_for_query, q, pubmed, s2): q
            for q in pending
        }
        for future in as_completed(future_to_query):
            q = future_to_query[future]
            try:
                papers = future.result()
                papers_by_query[q["id"]] = papers
            except Exception as e:
                logger.warning(f"Query {q['id']} fetch raised: {e}")
                papers_by_query[q["id"]] = []

    # Persist papers and update query statuses
    total_new = 0
    for q in pending:
        papers = papers_by_query.get(q["id"], [])
        for p in papers:
            p.query_ids = [q["id"]]

        new_count = repos.papers.upsert_many(papers)
        total_new += new_count
        repos.queries.mark_done(q["id"], papers_found=len(papers), papers_new=new_count)

    logger.info(f"Fetched {sum(len(v) for v in papers_by_query.values())} papers total, {total_new} new")
    return total_new


# ---------------------------------------------------------------------------
# Full text fetching (Arm 1b)
# ---------------------------------------------------------------------------

def run_full_text_phase(
    repos: Repositories,
    config: AppConfig,
    pmc_fetcher: PMCFullTextFetcher,
) -> int:
    """
    Transiently fetch full text for papers that have PMC IDs.
    Full text is NEVER written to DB — only used to upgrade extraction text in memory.
    Returns count of papers upgraded.
    """
    candidates = repos.papers.get_full_text_candidates(config.max_full_text_per_cycle)
    if not candidates:
        return 0

    logger.info(f"Checking {len(candidates)} papers for PMC full text")
    upgraded = 0
    for paper in candidates:
        pmc_id = paper.get("pmc_id")
        if not pmc_id:
            continue
        full_text = pmc_fetcher.fetch(pmc_id)
        if full_text:
            repos.papers.mark_fetch_status(paper["id"], "full_text_available")
            upgraded += 1

    if upgraded:
        logger.info(f"Upgraded {upgraded} papers to full_text_available status")
    return upgraded


# ---------------------------------------------------------------------------
# Extraction phase (Arm 1c)
# ---------------------------------------------------------------------------

def run_extraction_phase(
    repos: Repositories,
    config: AppConfig,
    extractor: PaperExtractor,
    pmc_fetcher: PMCFullTextFetcher,
    conn,
) -> tuple[int, int, int, int]:
    """
    Extract entities/interactions from pending papers.
    Returns (papers_processed, new_entities, new_interactions, new_evidence).
    Commits after each paper so data is visible in the DB progressively.
    """
    pending = repos.papers.get_pending_extraction(config.papers_per_cycle)
    if not pending:
        return 0, 0, 0, 0

    # Refresh the normalizer's DB connection — the one used at construction is closed.
    extractor._normalizer.set_repo(repos.entities)

    logger.info(f"Extracting from {len(pending)} papers")

    papers_processed = 0
    total_entities = 0
    total_interactions = 0
    total_evidence = 0

    for paper_row in pending:
        if _shutdown:
            logger.info("Shutdown requested — stopping extraction early.")
            break

        paper_id = paper_row["id"]
        title = paper_row.get("title") or ""
        abstract = paper_row.get("abstract") or ""
        pmc_id = paper_row.get("pmc_id")

        # Choose text source: prefer full text (transient), fall back to abstract
        text = abstract
        full_text_used = False

        if pmc_id and paper_row.get("fetch_status") == "full_text_available":
            full_text = pmc_fetcher.fetch(pmc_id)
            if full_text and len(full_text) > len(abstract):
                text = full_text
                full_text_used = True

        if not text or len(text.strip()) < 50:
            repos.papers.mark_extraction_failed(paper_id, "Insufficient text")
            continue

        try:
            result = extractor.extract(paper_id, title, text)
            msg = (
                f"  {paper_id}: LLM returned {len(result.entities)} entities, "
                f"{len(result.interactions)} interactions — persisting..."
            )
            logger.info(msg)
            ne, ni, nev = extractor.persist(result, repos)

            repos.papers.mark_extraction_done(paper_id, raw_llm_response=None)
            papers_processed += 1
            total_entities += ne
            total_interactions += ni
            total_evidence += nev

            logger.info(
                f"  {paper_id}: +{ne} entities, +{ni} interactions, +{nev} evidence"
                + (" [full text]" if full_text_used else " [abstract]")
            )

        except Exception as e:
            logger.error(f"Extraction failed for {paper_id}: {e}", exc_info=True)
            repos.papers.mark_extraction_failed(paper_id, str(e)[:2000])

        # Commit after each paper so data is visible immediately and a later
        # crash doesn't lose the whole batch.
        conn.commit()

    return papers_processed, total_entities, total_interactions, total_evidence


# ---------------------------------------------------------------------------
# Query generation for entity gaps
# ---------------------------------------------------------------------------

def run_gap_filling_phase(repos: Repositories, config: AppConfig) -> int:
    """
    Generate simple expansion queries for entities with few interactions.
    No LLM needed — template-based.
    Returns count of queries added.
    """
    low_coverage = repos.entities.get_low_interaction_entities(
        config.min_interactions_per_entity, limit=20
    )
    added = 0
    for entity in low_coverage:
        name = entity["canonical_name"]
        entity_type = entity["entity_type"]
        entity_id = entity["id"]

        query_text = f'"{name}"[Title/Abstract] AND ({entity_type} interactions OR binding OR signaling)'
        query = SearchQuery(
            query_text=query_text,
            source_api="pubmed",
            query_type=QueryType.ENTITY_EXPANSION,
            origin=f"entity_id:{entity_id}",
        )
        repos.queries.insert(query)
        added += 1

    if added:
        logger.info(f"Added {added} entity-expansion queries for low-coverage entities")
    return added


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle(
    cycle: int,
    config: AppConfig,
    engine,
    pubmed: PubMedCrawler,
    s2: SemanticScholarCrawler,
    pmc_fetcher: PMCFullTextFetcher,
    extractor: PaperExtractor,
    conflict_detector=None,
    conflict_resolver=None,
) -> dict:
    """Run one full autoresearch cycle. Returns cycle stats."""

    with engine.connect() as conn:
        repos = Repositories(conn)

        logger.info(f"{'='*60}")
        logger.info(f"CYCLE {cycle} START")
        logger.info(f"{'='*60}")

        # --- Phase 0: Seed if needed ---
        _seed_initial_queries(repos, config)
        conn.commit()

        # --- Phase 1: Fetch papers ---
        new_papers = run_fetch_phase(repos, config, pubmed, s2)
        conn.commit()

        # --- Phase 2: Upgrade to full text (transient) ---
        run_full_text_phase(repos, config, pmc_fetcher)
        conn.commit()

        # --- Phase 3: Extract ---
        # conn is passed so run_extraction_phase can commit after each paper.
        papers_done, new_ents, new_ints, new_ev = run_extraction_phase(
            repos, config, extractor, pmc_fetcher, conn
        )

        # --- Phase 4: Detect conflicts (Arm 2) ---
        new_conflicts = 0
        conflicts_resolved = 0
        if conflict_detector:
            new_conflicts = conflict_detector.detect(repos, config)
            conn.commit()

        # --- Phase 5: Classify + resolve conflicts ---
        if conflict_resolver:
            conflicts_resolved = conflict_resolver.analyze_and_resolve(repos, config)
            conn.commit()
            resolution_queries = conflict_resolver.generate_resolution_queries(repos, config)
            conn.commit()

        # --- Phase 6: Gap-filling queries ---
        run_gap_filling_phase(repos, config)
        conn.commit()

        # --- Phase 7: Score ---
        score, stats = compute_score(repos, config)
        repos.metrics.log(
            cycle=cycle,
            papers_processed=papers_done,
            new_entities=new_ents,
            new_interactions=new_ints,
            new_evidence=new_ev,
            new_conflicts=new_conflicts,
            conflicts_resolved=conflicts_resolved,
            **stats,
        )
        conn.commit()

    return {"cycle": cycle, "score": score, **stats}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AutoBioResearch autonomous loop")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--cycles", type=int, default=0, help="Max cycles (0=forever)")
    parser.add_argument("--no-conflicts", action="store_true", help="Skip conflict detection (Arm 2)")
    args = parser.parse_args()

    config = AppConfig.from_yaml(args.config)
    if args.cycles:
        config.max_cycles = args.cycles

    setup_logging(config.log_level, config.log_file)
    signal.signal(signal.SIGINT, _handle_sigint)

    logger.info("AutoBioResearch starting up")
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
    s2 = SemanticScholarCrawler(
        requests_per_second=config.semantic_scholar_requests_per_second,
    )
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

    # Conflict components (Arm 2)
    conflict_detector = None
    conflict_resolver = None
    if not args.no_conflicts:
        from autobioresearch.conflict.detector import ConflictDetector
        from autobioresearch.conflict.resolver import ConflictResolver
        conflict_detector = ConflictDetector()
        conflict_resolver = ConflictResolver(llm=llm)

    # Main loop
    cycle = 0
    while not _shutdown:
        cycle += 1

        try:
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
            )
            logger.info(f"Cycle {cycle} complete | Score: {stats['score']:.2f}")
        except Exception as e:
            logger.error(f"Cycle {cycle} failed: {e}", exc_info=True)

        if config.max_cycles and cycle >= config.max_cycles:
            logger.info(f"Reached max_cycles={config.max_cycles}. Stopping.")
            break

        if not _shutdown:
            logger.info(f"Sleeping {config.cycle_sleep_seconds}s until next cycle...")
            # Sleep in small increments to allow clean shutdown
            for _ in range(config.cycle_sleep_seconds):
                if _shutdown:
                    break
                time.sleep(1)

    logger.info("AutoBioResearch stopped.")


if __name__ == "__main__":
    main()
