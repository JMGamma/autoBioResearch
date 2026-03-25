# AutoBioResearch

AutoBioResearch is an autonomous biological knowledge graph builder. It searches scientific literature, extracts entities and interactions with an LLM, stores evidence in SQLite, detects contradictory claims, and generates follow-up queries to keep the loop moving.

The current project is centered on a working local pipeline:

- literature retrieval from PubMed and Semantic Scholar
- transient PMC full-text fetching when available
- LLM-based extraction of entities, interactions, and evidence snippets
- entity normalization with seeded synonyms plus optional UniProt/ChEBI lookups
- conflict detection, LLM-assisted conflict analysis, and follow-up query generation
- query planning based on prior query yield and graph impact
- graph export and database maintenance scripts
- a perturbation propagation prototype over the built graph

## What The Project Does Today

### Retrieval and extraction

- pulls papers from PubMed and Semantic Scholar
- prefers PMC full text when available, but does not store full text in the database
- extracts entities, interactions, evidence type, direction, effect, and biological context
- stores literal claim records and normalized evidence records separately so older data can be reprocessed

### Normalization and verification

- deduplicates entities with exact synonym lookup, seeded aliases, fuzzy matching, and optional external resolution
- normalizes evidence context fields such as organism, tissue or cell type, condition, and assay
- verifies selected claims and marks them as `verified` or `needs_review`

### Conflict handling and search planning

- detects conflicting interaction claims already present in the graph
- classifies conflicts as `true_conflict`, `context_dependent`, or `ambiguous`
- generates follow-up search queries for unresolved conflicts
- scores and prioritizes pending queries using observed yield and graph gaps
- generates targeted gap-filling queries for sparse entities and under-supported seeded interactions

### Graph analysis and utilities

- logs per-cycle graph metrics and score history
- exports the graph to JSON or GraphML
- includes a perturbation propagation prototype for downstream effect analysis

## Repository Layout

```text
autobioresearch/
  main.py                    Main orchestration loop
  config.py                  Config loading from YAML and environment
  database.py                SQLite schema, indexes, and additive migrations
  metrics.py                 Score computation
  planner.py                 Query planning and targeted query generation
  crawlers/                  PubMed, Semantic Scholar, PMC, external entity resolvers
  extractor/                 LLM client, extraction, normalization, verification
  conflict/                  Conflict detection, adjudication, and resolution
  perturbation/              Sign mapping and propagation logic
  seeders/                   UniProt/IntAct, Reactome, and SIGNOR importers
  storage/                   Repository layer for DB access
  models/                    Pydantic models

scripts/
  init_db.py                 Create schema and indexes
  seed_interactions.py       Import curated interactions
  reset_db.py                Soft or hard reset for SQLite DBs
  inspect_conflicts.py       Review conflict queue from the CLI
  export_graph.py            Export graph to JSON or GraphML
  reprocess_existing_data.py Backfill newer reliability fields on older DBs
  validate_synonym_overlaps.py Audit duplicate synonym mappings
  perturbation_prototype.py  Run propagation from a seed entity
```

## Database Model

The SQLite database currently includes these core tables:

- `papers`: paper metadata and abstracts
- `entities`: canonical entities
- `entity_synonyms`: lookup table for aliases
- `interactions`: unique normalized interaction claims
- `literal_claims`: raw snippet-grounded claims captured before normalization
- `evidence`: normalized evidence rows linked to interactions
- `conflicts`: detected contradictions between interaction claims
- `search_queries`: pending, running, completed, and failed search work
- `metrics_log`: per-cycle telemetry and score history

Full text from PMC is fetched transiently during extraction and is not stored.

## Requirements

- Python 3.10+
- `uv` recommended, or `pip`
- one of:
  - `ANTHROPIC_API_KEY` for Anthropic mode
  - an OpenAI-compatible local or remote endpoint for `openai_compatible` mode

## Installation

```bash
git clone https://github.com/JMGamma/autobioresearch
cd autobioresearch
uv sync
```

If you want the test dependencies as well:

```bash
uv sync --group dev
```

## Configuration

Create a local environment file:

```bash
cp .env.example .env
```

Configuration is split across:

- `.env` for secrets such as `ANTHROPIC_API_KEY`, `NCBI_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`, and `LLM_API_KEY`
- `config.yaml` for runtime behavior

Important `config.yaml` settings in the current codebase include:

```yaml
llm_api_type: "anthropic"          # or "openai_compatible"
llm_model: "claude-sonnet-4-6"
llm_base_url: null                 # required for openai_compatible mode

db_path: "./autobioresearch.db"

queries_per_cycle: 10
papers_per_cycle: 50
targeted_queries_per_cycle: 10

verification_enabled: true
claims_to_verify_per_cycle: 25

entity_resolution_enabled: true
entity_resolution_requests_per_second: 3.0
```

For local models, `openai_compatible` mode expects a server that supports tool or function calling.

Reasoning capture for compatible local models can be enabled in `config.yaml`:

```yaml
log_reasoning: true
reasoning_log_file: "./logs/reasoning.log"
reasoning_log_max_bytes: 10485760
reasoning_log_backup_count: 3
```

## Getting Started

Initialize the database:

```bash
uv run scripts/init_db.py
```

Optionally seed curated interactions before running the loop:

```bash
uv run scripts/seed_interactions.py
uv run scripts/seed_interactions.py --source uniprot
uv run scripts/seed_interactions.py --source reactome
uv run scripts/seed_interactions.py --source signor
```

Run the main loop:

```bash
uv run -m autobioresearch.main
```

Useful options:

```text
--config PATH
--cycles N
--no-conflicts
```

## Common Scripts

Reset a database for testing:

```bash
uv run scripts/reset_db.py
uv run scripts/reset_db.py --hard --yes
uv run scripts/reset_db.py --db-path ./test.db --yes
```

Inspect conflicts:

```bash
uv run scripts/inspect_conflicts.py
uv run scripts/inspect_conflicts.py --status open --type true_conflict --show-evidence
```

Audit synonym overlaps:

```bash
uv run scripts/validate_synonym_overlaps.py
uv run scripts/validate_synonym_overlaps.py --show-rows --limit 20
```

Export the graph:

```bash
uv run scripts/export_graph.py --format json --out graph.json
uv run scripts/export_graph.py --format graphml --out graph.graphml
uv run scripts/export_graph.py --format json --min-confidence high --min-evidence 2
```

Backfill newer processing fields onto an existing database:

```bash
uv run scripts/reprocess_existing_data.py
uv run scripts/reprocess_existing_data.py --refresh-conflicts
```

Run the perturbation prototype:

```bash
uv run scripts/perturbation_prototype.py --entity TP53 --mode suppress --depth 3
uv run scripts/perturbation_prototype.py --entity BRCA2 --mode promote --depth 2 --out results.json
```

## Testing

The repository includes `pytest` coverage for extraction workflows, normalization, crawlers, entity resolution, seeders, and perturbation propagation.

Run the test suite with:

```bash
uv run pytest
```

## Notes

- SQLite WAL mode is enabled by default.
- Existing databases are updated with additive migrations when the schema is created.
- Conflict resolution and claim verification both rely on the configured LLM backend.

Project provided under MIT license terms
