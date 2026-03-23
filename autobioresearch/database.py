"""
SQLAlchemy Core table definitions and engine initialization.
All tables use TEXT primary keys (UUIDs or prefixed IDs) for portability.
WAL mode is enabled for concurrent read/write access.
"""
from sqlalchemy import (
    Column, Float, ForeignKey, Integer, MetaData, Table, Text, create_engine, event, inspect, text
)

metadata = MetaData()

papers = Table(
    "papers",
    metadata,
    Column("id", Text, primary_key=True),           # e.g. "pmid:12345678"
    Column("source", Text, nullable=False),          # pubmed | semantic_scholar | arxiv
    Column("title", Text),
    Column("abstract", Text),                        # retained permanently
    # full_text is NEVER stored — fetched transiently during extraction only
    Column("authors", Text),                         # JSON array of strings
    Column("journal", Text),
    Column("year", Integer),
    Column("doi", Text),
    Column("pmc_id", Text),                          # for transient full-text fetching
    Column("fetch_status", Text, nullable=False, default="pending"),
    # pending | abstract_only | full_text_available | failed
    Column("extraction_status", Text, nullable=False, default="pending"),
    # pending | processing | done | failed
    Column("extraction_error", Text),
    Column("raw_llm_response", Text),                # stored for debugging/reprocessing
    Column("query_ids", Text),                       # JSON array of query UUIDs
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

entities = Table(
    "entities",
    metadata,
    Column("id", Text, primary_key=True),            # UUID4
    Column("canonical_name", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    # protein|gene|molecule|metabolite|rna|pathway|phenotype|disease|cell_type|organism|complex|unknown
    Column("synonyms", Text),                        # JSON array
    Column("external_ids", Text),                    # JSON: {"uniprot":"P01375","ncbi_gene":"7124"}
    Column("organism", Text),
    Column("description", Text),
    Column("paper_count", Integer, default=0),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

entity_synonyms = Table(
    "entity_synonyms",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("synonym", Text, nullable=False),         # lowercased for matching
    Column("source", Text),                          # llm_extracted | manual | seeded
)

interactions = Table(
    "interactions",
    metadata,
    # One row = one unique interaction claim (entity pair + type + effect)
    # Multiple papers evidencing the same interaction go into the `evidence` table
    Column("id", Text, primary_key=True),            # UUID4
    Column("entity_a_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("entity_b_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("interaction_type", Text, nullable=False),
    # direct_binding|enzymatic|signaling|genetic|transcriptional|translational|
    # post_translational|metabolic|proximal_association|co_expression|transport|unknown
    Column("direction", Text),                       # A_to_B|B_to_A|bidirectional|undirected
    Column("effect", Text),                          # activates|inhibits|binds|phosphorylates|etc.
    Column("evidence_count", Integer, default=0),    # auto-maintained
    Column("composite_confidence", Text),            # high|medium|low — derived from evidence
    Column("composite_confidence_score", Float),      # 0.0-1.0 weighted average
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

evidence = Table(
    "evidence",
    metadata,
    # One row = one paper's evidence for one interaction
    Column("id", Text, primary_key=True),            # UUID4
    Column("claim_id", Text, nullable=True),
    Column("interaction_id", Text, ForeignKey("interactions.id"), nullable=False),
    Column("paper_id", Text, ForeignKey("papers.id"), nullable=False),
    Column("evidence_type", Text, nullable=False),
    # in_vitro|in_vivo|structural|computational|co_expression|genetic_screen|clinical|unknown
    Column("evidence_subtype", Text),                # western_blot|co_ip|rnaseq|cryo_em|etc.
    Column("confidence", Text, nullable=False),      # high|medium|low
    Column("confidence_score", Float, nullable=False),# 0.0-1.0
    Column("organism", Text),
    Column("tissue_cell_type", Text),
    Column("condition", Text),                       # disease state, drug treatment, etc.
    Column("temperature", Text),
    Column("concentration", Text),
    Column("assay_type", Text),
    Column("normalized_organism", Text),
    Column("normalized_tissue_cell_type", Text),
    Column("normalized_condition", Text),
    Column("normalized_assay_type", Text),
    Column("snippet", Text, nullable=False),         # verbatim text from paper, max 400 chars
    Column("snippet_start", Integer),
    Column("snippet_end", Integer),
    Column("verification_status", Text),
    Column("verification_score", Float),
    Column("verification_notes", Text),
    Column("adjudication_score", Float),
    Column("adjudication_notes", Text),
    Column("created_at", Text, nullable=False),
)

literal_claims = Table(
    "literal_claims",
    metadata,
    Column("id", Text, primary_key=True),
    Column("paper_id", Text, ForeignKey("papers.id"), nullable=False),
    Column("entity_a_text", Text, nullable=False),
    Column("entity_b_text", Text, nullable=False),
    Column("interaction_type_text", Text, nullable=False),
    Column("direction_text", Text),
    Column("effect_text", Text),
    Column("evidence_type_text", Text),
    Column("organism_text", Text),
    Column("tissue_cell_type_text", Text),
    Column("condition_text", Text),
    Column("assay_type_text", Text),
    Column("evidence_subtype_text", Text),
    Column("confidence_text", Text),
    Column("confidence_score", Float, nullable=False),
    Column("snippet", Text, nullable=False),
    Column("reasoning", Text),
    Column("verification_status", Text),
    Column("verification_score", Float),
    Column("verification_notes", Text),
    Column("created_at", Text, nullable=False),
)

conflicts = Table(
    "conflicts",
    metadata,
    Column("id", Text, primary_key=True),            # UUID4
    Column("interaction_a_id", Text, ForeignKey("interactions.id"), nullable=False),
    Column("interaction_b_id", Text, ForeignKey("interactions.id"), nullable=False),
    Column("conflict_type", Text, nullable=False),   # true_conflict|context_dependent|ambiguous
    Column("conflict_axis", Text),                   # what dimension conflicts
    Column("context_difference", Text),              # JSON
    Column("status", Text, nullable=False, default="open"),
    # open|investigating|resolved|dismissed|reopened
    Column("resolution_paper_id", Text),
    Column("resolution_note", Text),
    Column("llm_analysis", Text),
    Column("generated_query_ids", Text),             # JSON array
    Column("penalty_weight", Float, default=1.0),
    # true_conflict=1.0, ambiguous=0.5, context_dependent=0.2
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

search_queries = Table(
    "search_queries",
    metadata,
    Column("id", Text, primary_key=True),            # UUID4
    Column("query_text", Text, nullable=False),
    Column("source_api", Text, nullable=False),      # pubmed|semantic_scholar|arxiv
    Column("query_type", Text, nullable=False),
    # initial|conflict_resolution|entity_expansion|gap_filling
    Column("origin", Text, nullable=False),
    # "seed" | "conflict_id:<uuid>" | "entity_id:<uuid>"
    Column("generation_reason", Text),
    Column("target_kind", Text),
    Column("target_id", Text),
    Column("planning_score", Float, nullable=False, default=0.0),
    Column("planning_factors", Text),
    Column("status", Text, nullable=False, default="pending"),
    # pending|running|done|failed
    Column("papers_found", Integer, default=0),
    Column("papers_new", Integer, default=0),
    Column("outcome_papers_processed", Integer, default=0),
    Column("outcome_new_interactions", Integer, default=0),
    Column("outcome_new_evidence", Integer, default=0),
    Column("attributed_papers_processed", Float, nullable=False, default=0.0),
    Column("attributed_new_interactions", Float, nullable=False, default=0.0),
    Column("attributed_new_evidence", Float, nullable=False, default=0.0),
    Column("improved_graph", Integer),
    Column("last_error", Text),
    Column("created_at", Text, nullable=False),
    Column("executed_at", Text),
    Column("completed_at", Text),
)

metrics_log = Table(
    "metrics_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("cycle", Integer, nullable=False),
    Column("n_entities", Integer, nullable=False),
    Column("n_interactions", Integer, nullable=False),
    Column("n_evidence", Integer, nullable=False),
    Column("n_unresolved_conflicts", Integer, nullable=False),
    Column("weighted_conflict_sum", Float, nullable=False),
    Column("score", Float, nullable=False),
    Column("penalty", Float, nullable=False),
    Column("papers_processed", Integer, nullable=False),
    Column("papers_fetched_total", Integer, nullable=False, default=0),
    Column("papers_fetched_new", Integer, nullable=False, default=0),
    Column("papers_extraction_failed", Integer, nullable=False, default=0),
    Column("new_entities", Integer, nullable=False),
    Column("new_interactions", Integer, nullable=False),
    Column("new_evidence", Integer, nullable=False),
    Column("evidence_accepted", Integer, nullable=False, default=0),
    Column("claims_verified", Integer, nullable=False, default=0),
    Column("claims_needing_review", Integer, nullable=False, default=0),
    Column("new_conflicts", Integer, nullable=False),
    Column("conflicts_reopened", Integer, nullable=False, default=0),
    Column("conflicts_resolved", Integer, nullable=False),
    Column("queries_generated", Integer, nullable=False, default=0),
    Column("queries_with_new_papers", Integer, nullable=False, default=0),
    Column("query_linked_evidence", Integer, nullable=False, default=0),
    Column("timestamp", Text, nullable=False),
)


def create_indexes(engine) -> None:
    """Create non-unique indexes after table creation."""
    index_stmts = [
        # Refresh indexes whose definitions may have changed between versions
        "DROP INDEX IF EXISTS idx_interactions_unique_claim",
        # papers
        "CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source)",
        "CREATE INDEX IF NOT EXISTS idx_papers_extraction_status ON papers(extraction_status)",
        "CREATE INDEX IF NOT EXISTS idx_papers_fetch_status ON papers(fetch_status)",
        "CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year)",
        # entities
        "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_canonical_organism ON entities(canonical_name, organism)",
        # entity_synonyms
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_synonyms_synonym ON entity_synonyms(synonym)",
        # interactions
        "CREATE INDEX IF NOT EXISTS idx_interactions_ab ON interactions(entity_a_id, entity_b_id)",
        "CREATE INDEX IF NOT EXISTS idx_interactions_ba ON interactions(entity_b_id, entity_a_id)",
        "CREATE INDEX IF NOT EXISTS idx_interactions_type ON interactions(interaction_type)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_interactions_unique_claim ON interactions(entity_a_id, entity_b_id, interaction_type, effect, direction)",
        # evidence
        "CREATE INDEX IF NOT EXISTS idx_evidence_interaction ON evidence(interaction_id)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_paper ON evidence(paper_id)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence(evidence_type)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_organism ON evidence(organism)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_norm_organism ON evidence(normalized_organism)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_norm_tissue ON evidence(normalized_tissue_cell_type)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_norm_condition ON evidence(normalized_condition)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_unique ON evidence(interaction_id, paper_id)",
        # literal_claims
        "CREATE INDEX IF NOT EXISTS idx_literal_claims_paper ON literal_claims(paper_id)",
        # conflicts
        "CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)",
        "CREATE INDEX IF NOT EXISTS idx_conflicts_type ON conflicts(conflict_type)",
        "CREATE INDEX IF NOT EXISTS idx_conflicts_pair ON conflicts(interaction_a_id, interaction_b_id)",
        # search_queries
        "CREATE INDEX IF NOT EXISTS idx_queries_status ON search_queries(status)",
        "CREATE INDEX IF NOT EXISTS idx_queries_type ON search_queries(query_type)",
        "CREATE INDEX IF NOT EXISTS idx_queries_origin ON search_queries(origin)",
    ]
    with engine.connect() as conn:
        for stmt in index_stmts:
            conn.execute(text(stmt))
        conn.commit()


def _ensure_column(engine, table_name: str, column_name: str, ddl: str) -> None:
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with engine.connect() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))
        conn.commit()


def apply_migrations(engine) -> None:
    """Best-effort additive migrations for existing SQLite databases."""
    _ensure_column(engine, "search_queries", "last_error", "last_error TEXT")
    _ensure_column(engine, "search_queries", "completed_at", "completed_at TEXT")
    _ensure_column(engine, "search_queries", "generation_reason", "generation_reason TEXT")
    _ensure_column(engine, "search_queries", "target_kind", "target_kind TEXT")
    _ensure_column(engine, "search_queries", "target_id", "target_id TEXT")
    _ensure_column(engine, "search_queries", "planning_score", "planning_score FLOAT NOT NULL DEFAULT 0.0")
    _ensure_column(engine, "search_queries", "planning_factors", "planning_factors TEXT")
    _ensure_column(engine, "search_queries", "outcome_papers_processed", "outcome_papers_processed INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "search_queries", "outcome_new_interactions", "outcome_new_interactions INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "search_queries", "outcome_new_evidence", "outcome_new_evidence INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "search_queries", "attributed_papers_processed", "attributed_papers_processed FLOAT NOT NULL DEFAULT 0.0")
    _ensure_column(engine, "search_queries", "attributed_new_interactions", "attributed_new_interactions FLOAT NOT NULL DEFAULT 0.0")
    _ensure_column(engine, "search_queries", "attributed_new_evidence", "attributed_new_evidence FLOAT NOT NULL DEFAULT 0.0")
    _ensure_column(engine, "search_queries", "improved_graph", "improved_graph INTEGER")

    _ensure_column(engine, "metrics_log", "papers_fetched_total", "papers_fetched_total INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "papers_fetched_new", "papers_fetched_new INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "papers_extraction_failed", "papers_extraction_failed INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "evidence_accepted", "evidence_accepted INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "claims_verified", "claims_verified INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "claims_needing_review", "claims_needing_review INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "conflicts_reopened", "conflicts_reopened INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "queries_generated", "queries_generated INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "queries_with_new_papers", "queries_with_new_papers INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "metrics_log", "query_linked_evidence", "query_linked_evidence INTEGER NOT NULL DEFAULT 0")
    _ensure_column(engine, "evidence", "verification_status", "verification_status TEXT")
    _ensure_column(engine, "evidence", "verification_score", "verification_score FLOAT")
    _ensure_column(engine, "evidence", "verification_notes", "verification_notes TEXT")
    _ensure_column(engine, "evidence", "claim_id", "claim_id TEXT")
    _ensure_column(engine, "evidence", "normalized_organism", "normalized_organism TEXT")
    _ensure_column(engine, "evidence", "normalized_tissue_cell_type", "normalized_tissue_cell_type TEXT")
    _ensure_column(engine, "evidence", "normalized_condition", "normalized_condition TEXT")
    _ensure_column(engine, "evidence", "normalized_assay_type", "normalized_assay_type TEXT")
    _ensure_column(engine, "evidence", "adjudication_score", "adjudication_score FLOAT")
    _ensure_column(engine, "evidence", "adjudication_notes", "adjudication_notes TEXT")


def init_engine(db_path: str):
    """Create and configure SQLite engine with WAL mode."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def create_all(engine) -> None:
    """Create all tables and indexes."""
    metadata.create_all(engine)
    apply_migrations(engine)
    create_indexes(engine)
