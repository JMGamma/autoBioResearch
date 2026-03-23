"""
Seed known biological interactions from curated databases (UniProt/IntAct, Reactome, SIGNOR).

Run this before or alongside the autonomous research loop to pre-populate the
knowledge graph with high-quality, manually curated interactions.

Usage:
    uv run scripts/seed_interactions.py
    uv run scripts/seed_interactions.py --source uniprot --uniprot-limit 500
    uv run scripts/seed_interactions.py --source reactome --dry-run --verbose
    uv run scripts/seed_interactions.py --source signor
    uv run scripts/seed_interactions.py --source all
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from autobioresearch.database import create_all, init_engine
from autobioresearch.models import (
    EvidenceType,
    ExtractionStatus,
    FetchStatus,
    Interaction,
    InteractionType,
    InteractionContext,
    EvidenceRecord,
    Paper,
)
from autobioresearch.seeders.entity_seeder import EntitySeeder
from autobioresearch.seeders.uniprot_fetcher import (
    UniProtInteractionFetcher,
    _experiments_to_confidence,
)
from autobioresearch.seeders.reactome_fetcher import ReactomeInteractionFetcher
from autobioresearch.seeders.signor_fetcher import SignorInteractionFetcher
from autobioresearch.storage.repositories import Repositories
from autobioresearch.utils.logging_config import setup_logging


def _make_virtual_paper(source: str, paper_id: str, title: str) -> Paper:
    return Paper(
        id=paper_id,
        source=source,
        title=title,
        abstract=None,
        authors=[],
        year=None,
        fetch_status=FetchStatus.ABSTRACT_ONLY,
        extraction_status=ExtractionStatus.DONE,
    )


def seed_uniprot(conn, repos: Repositories, seeder: EntitySeeder, args, stats: dict):
    fetcher = UniProtInteractionFetcher(
        requests_per_second=args.rate_limit,
        page_size=500,
    )

    processed = 0
    for raw in fetcher.iter_interactions():
        if args.uniprot_limit and processed >= args.uniprot_limit:
            break

        entity_a_id = seeder.get_or_create(raw.acc_a)
        entity_b_id = seeder.get_or_create(raw.acc_b)
        if not entity_a_id or not entity_b_id:
            stats["skipped"] += 1
            continue

        paper_id = f"uniprot:{raw.intact_id_a}"
        score, conf = _experiments_to_confidence(raw.n_experiments)

        if not args.dry_run:
            paper = _make_virtual_paper(
                source="uniprot",
                paper_id=paper_id,
                title=(
                    f"IntAct curated interaction: {raw.acc_a} / {raw.acc_b} "
                    f"({raw.intact_id_a} / {raw.intact_id_b})"
                ),
            )
            if repos.papers.upsert(paper):
                stats["papers_new"] += 1

            interaction = Interaction(
                entity_a_id=entity_a_id,
                entity_b_id=entity_b_id,
                interaction_type=InteractionType.DIRECT_BINDING,
                direction="undirected",
                effect="binds",
            )
            interaction_id, is_new = repos.interactions.upsert(interaction)
            if is_new:
                stats["interactions_new"] += 1

            evidence = EvidenceRecord(
                interaction_id=interaction_id,
                paper_id=paper_id,
                evidence_type=EvidenceType.CURATED_DB,
                confidence=conf,
                confidence_score=score,
                context=InteractionContext(
                    organism="Homo sapiens",
                    assay_type="IntAct",
                ),
                snippet=(
                    f"IntAct binary interaction: {raw.acc_a} / {raw.acc_b} "
                    f"({raw.n_experiments} experiment(s))"
                )[:400],
            )
            if repos.evidence.insert(evidence):
                stats["evidence_new"] += 1
                repos.interactions.update_composite_confidence(interaction_id)

        processed += 1
        if processed % 500 == 0:
            if not args.dry_run:
                conn.commit()
            logging.info(f"[UniProt] Processed {processed} interactions...")

    if not args.dry_run:
        conn.commit()

    stats["uniprot_processed"] = processed
    logging.info(
        f"[UniProt] Complete: {processed} interactions, "
        f"{stats['interactions_new']} new, {stats['evidence_new']} evidence records."
    )


def seed_reactome(conn, repos: Repositories, seeder: EntitySeeder, args, stats: dict):
    fetcher = ReactomeInteractionFetcher()

    # Track virtual papers we've already upserted this run to avoid redundant calls
    seen_papers: set[str] = set()
    processed = 0
    interactions_before = stats["interactions_new"]
    evidence_before = stats["evidence_new"]

    for raw in fetcher.iter_interactions():
        if args.reactome_limit and processed >= args.reactome_limit:
            break

        entity_a_id = seeder.get_or_create(raw.acc_a)
        entity_b_id = seeder.get_or_create(raw.acc_b)
        if not entity_a_id or not entity_b_id:
            stats["skipped"] += 1
            continue

        paper_id = f"reactome:{raw.pathway_id}"

        if not args.dry_run:
            if paper_id not in seen_papers:
                paper = _make_virtual_paper(
                    source="reactome",
                    paper_id=paper_id,
                    title=f"Reactome pathway {raw.pathway_id}",
                )
                if repos.papers.upsert(paper):
                    stats["papers_new"] += 1
                seen_papers.add(paper_id)

            interaction = Interaction(
                entity_a_id=entity_a_id,
                entity_b_id=entity_b_id,
                interaction_type=raw.interaction_type,  # type: ignore[arg-type]
                direction=raw.direction,
                effect=raw.effect,
            )
            interaction_id, is_new = repos.interactions.upsert(interaction)
            if is_new:
                stats["interactions_new"] += 1

            evidence = EvidenceRecord(
                interaction_id=interaction_id,
                paper_id=paper_id,
                evidence_type=EvidenceType.CURATED_DB,
                confidence="high",
                confidence_score=0.75,
                context=InteractionContext(
                    organism="Homo sapiens",
                    assay_type="Reactome",
                ),
                snippet=(
                    f"Reactome pathway {raw.pathway_id}: "
                    f"{raw.acc_a} {raw.mi_label} {raw.acc_b}"
                )[:400],
            )
            if repos.evidence.insert(evidence):
                stats["evidence_new"] += 1
                repos.interactions.update_composite_confidence(interaction_id)

        processed += 1
        if processed % 1000 == 0:
            if not args.dry_run:
                conn.commit()
            logging.info(f"[Reactome] Processed {processed} interactions...")

    if not args.dry_run:
        conn.commit()

    stats["reactome_processed"] = processed
    new_ix = stats["interactions_new"] - interactions_before
    new_ev = stats["evidence_new"] - evidence_before
    logging.info(
        f"[Reactome] Complete: {processed} interactions, "
        f"{new_ix} new, {new_ev} evidence records."
    )


def seed_signor(conn, repos: Repositories, seeder: EntitySeeder, args, stats: dict):
    fetcher = SignorInteractionFetcher()

    processed = 0
    interactions_before = stats["interactions_new"]
    evidence_before = stats["evidence_new"]

    for raw in fetcher.iter_interactions():
        if args.signor_limit and processed >= args.signor_limit:
            break

        entity_a_id = seeder.get_or_create(raw.acc_a)
        entity_b_id = seeder.get_or_create(raw.acc_b)
        if not entity_a_id or not entity_b_id:
            stats["skipped"] += 1
            continue

        paper_id = f"signor:{raw.signor_id}"

        # SIGNOR scores are per-record confidence values (0–1)
        conf = "high" if raw.score >= 0.7 else ("medium" if raw.score >= 0.4 else "low")

        if not args.dry_run:
            paper = _make_virtual_paper(
                source="signor",
                paper_id=paper_id,
                title=f"SIGNOR curated interaction {raw.signor_id}: {raw.acc_a} {raw.mechanism} {raw.acc_b}",
            )
            if repos.papers.upsert(paper):
                stats["papers_new"] += 1

            interaction = Interaction(
                entity_a_id=entity_a_id,
                entity_b_id=entity_b_id,
                interaction_type=raw.interaction_type,  # type: ignore[arg-type]
                direction=raw.direction,
                effect=raw.effect,
            )
            interaction_id, is_new = repos.interactions.upsert(interaction)
            if is_new:
                stats["interactions_new"] += 1

            context = InteractionContext(
                organism="Homo sapiens",
                assay_type="SIGNOR",
                tissue_cell_type=raw.cell_data or raw.tissue_data or None,
            )
            evidence = EvidenceRecord(
                interaction_id=interaction_id,
                paper_id=paper_id,
                evidence_type=EvidenceType.CURATED_DB,
                confidence=conf,
                confidence_score=raw.score,
                context=context,
                snippet=(
                    f"SIGNOR {raw.signor_id}: {raw.acc_a} {raw.raw_effect} {raw.acc_b} "
                    f"via {raw.mechanism}"
                )[:400],
            )
            if repos.evidence.insert(evidence):
                stats["evidence_new"] += 1
                repos.interactions.update_composite_confidence(interaction_id)

        processed += 1
        if processed % 1000 == 0:
            if not args.dry_run:
                conn.commit()
            logging.info(f"[SIGNOR] Processed {processed} interactions...")

    if not args.dry_run:
        conn.commit()

    stats["signor_processed"] = processed
    new_ix = stats["interactions_new"] - interactions_before
    new_ev = stats["evidence_new"] - evidence_before
    logging.info(
        f"[SIGNOR] Complete: {processed} interactions, "
        f"{new_ix} new, {new_ev} evidence records."
    )


def print_summary(stats: dict):
    print("\n" + "=" * 50)
    print("Seeding complete.")
    print(f"  UniProt interactions processed:  {stats.get('uniprot_processed', 0)}")
    print(f"  Reactome interactions processed: {stats.get('reactome_processed', 0)}")
    print(f"  SIGNOR interactions processed:   {stats.get('signor_processed', 0)}")
    print(f"  Papers inserted:                 {stats['papers_new']}")
    print(f"  Interactions new:                {stats['interactions_new']}")
    print(f"  Evidence records new:            {stats['evidence_new']}")
    print(f"  Skipped (unresolvable):          {stats['skipped']}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="Seed known interactions from UniProt/IntAct and Reactome."
    )
    parser.add_argument(
        "--db-path", default="./autobioresearch.db",
        help="Path to SQLite database file (default: ./autobioresearch.db)"
    )
    parser.add_argument(
        "--source", choices=["all", "uniprot", "reactome", "signor"], default="all",
        help="Which database(s) to seed (default: all)"
    )
    parser.add_argument(
        "--uniprot-limit", type=int, default=0,
        help="Max UniProt interactions to process (0 = unlimited)"
    )
    parser.add_argument(
        "--reactome-limit", type=int, default=0,
        help="Max Reactome interactions to process (0 = unlimited)"
    )
    parser.add_argument(
        "--signor-limit", type=int, default=0,
        help="Max SIGNOR interactions to process (0 = unlimited)"
    )
    parser.add_argument(
        "--rate-limit", type=float, default=2.0,
        help="UniProt API requests/second for entity resolution (default: 2.0)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve entities but skip all DB writes"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG-level logging"
    )
    args = parser.parse_args()

    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging(log_level=log_level)

    if args.dry_run:
        logging.info("DRY RUN — no data will be written to the database.")

    db_path = Path(args.db_path)
    engine = init_engine(str(db_path))
    create_all(engine)

    stats = {
        "papers_new": 0,
        "interactions_new": 0,
        "evidence_new": 0,
        "skipped": 0,
        "uniprot_processed": 0,
        "reactome_processed": 0,
        "signor_processed": 0,
    }

    with engine.connect() as conn:
        repos = Repositories(conn)
        seeder = EntitySeeder(repos.entities, requests_per_second=args.rate_limit)

        if args.source in ("all", "uniprot"):
            seed_uniprot(conn, repos, seeder, args, stats)

        if args.source in ("all", "reactome"):
            seed_reactome(conn, repos, seeder, args, stats)

        if args.source in ("all", "signor"):
            seed_signor(conn, repos, seeder, args, stats)

    print_summary(stats)


if __name__ == "__main__":
    main()
