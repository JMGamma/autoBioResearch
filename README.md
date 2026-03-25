# AutoBioResearch

An autonomous biological knowledge graph builder. AutoBioResearch crawls scientific literature, extracts biological entities and their interactions using an LLM, detects conflicting claims between papers, and generates targeted search queries to resolve those conflicts — all in a self-driving loop guided by a single score metric.

Inspired by [karpathy/autoresearch](https://github.com/karpathy/autoresearch)'s tight feedback-loop philosophy, adapted for biological knowledge.

---

## What it does

**Arm 1 — Crawler & Extractor**

- Searches PubMed and Semantic Scholar for biology papers
- Fetches full text transiently from PMC Open Access when available (never stored)
- Uses an LLM to extract biological entities (proteins, genes, small molecules, metabolites, pathways, etc.) and their interactions from paper text
- Records rich metadata per interaction: type, direction, effect, evidence quality, biological context (organism, tissue/cell type, condition, assay)
- Multiple papers supporting the same interaction accumulate as separate evidence records, boosting composite confidence

**Arm 2 — Conflict Detector & Resolver**

- Scans the database for interaction pairs making opposite claims about the same entity pair
- Classifies each conflict: `true_conflict` (same context, genuinely contradictory), `context_dependent` (different organisms/conditions — expected), or `ambiguous`
- Uses an LLM to analyze ambiguous cases with full evidence context
- Generates targeted PubMed queries to find papers that resolve open conflicts
- Feeds resolution queries back into Arm 1 to close the loop

**Score metric**

```
score = (N_entities × N_interactions) / (1 + penalty × weighted_conflict_sum)
```

The loop maximizes this: more entities and interactions is better; unresolved conflicts are penalized (true conflicts more heavily than context-dependent ones). This drives autonomous prioritization of what to search next.

---

## Architecture

```
AutoBioResearch/
├── config.yaml                    # Tuning parameters
├── config/
│   └── synonyms.yaml              # Seeded biological synonym aliases
│
├── autobioresearch/
│   ├── main.py                    # Orchestration loop (both arms)
│   ├── config.py                  # AppConfig (pydantic-settings)
│   ├── database.py                # SQLAlchemy Core schema
│   ├── metrics.py                 # Score computation
│   │
│   ├── models/                    # Pydantic data models
│   │   ├── entity.py              # BiologicalEntity, EntityType
│   │   ├── interaction.py         # Interaction, EvidenceRecord, InteractionType, EvidenceType
│   │   ├── paper.py               # Paper, ExtractionResult
│   │   ├── conflict.py            # Conflict, ConflictType, ConflictStatus
│   │   └── query.py               # SearchQuery, QueryType
│   │
│   ├── crawlers/
│   │   ├── pubmed.py              # NCBI Entrez E-utilities
│   │   ├── semantic_scholar.py    # Semantic Scholar Graph API
│   │   └── pmc_fulltext.py        # PMC JATS XML (transient — never stored)
│   │
│   ├── extractor/
│   │   ├── claude_client.py       # Unified LLM client (Anthropic + OpenAI-compat)
│   │   ├── extraction_prompts.py  # System prompts + tool schemas
│   │   ├── extractor.py           # PaperExtractor: chunk → LLM → verify → persist
│   │   └── normalizer.py          # EntityNormalizer: synonym deduplication
│   │
│   ├── conflict/
│   │   ├── detector.py            # Rule-based conflict detection
│   │   ├── resolver.py            # LLM conflict classification + query generation
│   │   └── conflict_prompts.py    # System prompts + tool schemas
│   │
│   ├── storage/
│   │   └── repositories.py        # Typed DB read/write for all tables
│   │
│   └── seeders/
│       ├── entity_seeder.py       # Resolve/create entities from UniProt accessions
│       ├── uniprot_fetcher.py     # UniProt/IntAct binary interaction fetcher
│       ├── reactome_fetcher.py    # Reactome PSIMITAB TSV streamer
│       └── signor_fetcher.py      # SIGNOR causal signaling network fetcher
│
└── scripts/
    ├── init_db.py                 # One-time DB initialization
    ├── seed_interactions.py       # Bootstrap from UniProt/IntAct + Reactome
    ├── reset_db.py                # Wipe data for testing (soft or hard)
    ├── inspect_conflicts.py       # CLI conflict queue viewer
    └── export_graph.py            # Export to JSON or GraphML
```

### Database tables

| Table | Purpose |
|---|---|
| `papers` | Paper metadata + abstract (full text never stored) |
| `entities` | Canonical biological entities with synonym tracking |
| `entity_synonyms` | Flat synonym index for fast lookup and deduplication |
| `interactions` | Unique interaction claims (entity pair + type + effect) |
| `evidence` | One row per paper per interaction — context, assay, snippet |
| `conflicts` | Detected conflicts between interaction claims |
| `search_queries` | Query queue (seed, conflict-resolution, gap-filling) |
| `metrics_log` | Per-cycle score history |

---

## Setup

### Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An Anthropic API key **or** OpenAI-compatible LLM (Ollama, LM Studio, vLLM, etc.)

### Install

```bash
git clone https://github.com/JMGamma/autobioresearch
cd autobioresearch
uv sync
```

### Configure

```bash
cp .env.example .env
# API keys and other secrets stored in .env
```

All other settings live in `config.yaml`. Key options:

```yaml
# Use Anthropic Claude (default)
llm_api_type: "anthropic"
llm_model: "claude-sonnet-4-6"

# OR: use a local LLM via OpenAI-compatible REST API
llm_api_type: "openai_compatible"
llm_model: "llama3.1:8b"
llm_base_url: "http://localhost:11434/v1"   # Ollama example
llm_api_key: "none"
```

Larger papers can be chunked to enable use of smaller LLMs. Chunking settings are configured in config.yaml. Default settings were used with Qwen3.5-9B. Models with smaller context windows will require more aggressive chunking.  Use caution when chunking papers, as critical biological context may be lost if content is split into different chunks.

```yaml
# --- Extraction ---
max_chunk_chars: 100000
chunk_overlap_chars: 2000
min_snippet_length: 20
max_snippet_length: 400
snippet_fuzzy_threshold: 0.75
```

Local LLMs need to support function calling / tool use (Llama 3.1+, Mistral, Qwen 2.5+, etc.).

### Capturing model reasoning (local LLMs only)

Capture of model reasoning outputs is possible for model evaluation & rapid troubleshooting. 
Enable it in `config.yaml`:

```yaml
log_reasoning: true
reasoning_log_file: "./logs/reasoning.log"
reasoning_log_max_bytes: 10485760   # rotate at 10 MB
reasoning_log_backup_count: 3
```

### Initialize the database

```bash
uv run scripts/init_db.py
```

### Bootstrap from curated databases (optional)

Pre-populate the knowledge graph with high-quality, manually curated interactions from **UniProt/IntAct** and **Reactome** before (or alongside) the LLM research loop. This seeds the graph with a reliable foundation and gives the loop prior context to build on.

```bash
# Seed from all sources (recommended before first run)
uv run scripts/seed_interactions.py

# Individual sources
uv run scripts/seed_interactions.py --source uniprot
uv run scripts/seed_interactions.py --source reactome
uv run scripts/seed_interactions.py --source signor

# Test with a small sample before committing (dry-run skips all DB writes)
uv run scripts/seed_interactions.py --source uniprot --uniprot-limit 500 --dry-run --verbose
```

**What gets imported:**

| Source | Data | Interaction type | Coverage |
|---|---|---|---|
| UniProt / IntAct | Experimentally determined binary protein-protein interactions, Swiss-Prot reviewed, human | `direct_binding` | ~100K pairs |
| Reactome | Manually curated pathway reactions (binding, phosphorylation, ubiquitination, etc.), human | `direct_binding`, `post_translational`, `enzymatic`, `metabolic`, `transcriptional`, `proximal_association` | ~500K records |
| SIGNOR | Causal signaling interactions with explicit direction and mechanism, human | `post_translational`, `transcriptional`, `direct_binding`, `signaling`, `transport`, `enzymatic` | ~25K records |

**How it integrates with the loop:**

- Seeded interactions use the `curated_db` evidence type (weighted at 0.9, between `genetic_screen` and `in_vitro`)
- Seeded entities are registered with all known synonyms, so the LLM extractor can match and merge against them as it processes papers
- The script is fully idempotent — safe to re-run, will not create duplicate records

**Options:**

```
--db-path PATH          SQLite file to seed (default: ./autobioresearch.db)
--source {all,uniprot,reactome}
--uniprot-limit N       Max UniProt interactions (0 = unlimited)
--reactome-limit N      Max Reactome interactions (0 = unlimited)
--signor-limit N        Max SIGNOR interactions (0 = unlimited)
--rate-limit FLOAT      UniProt API req/s for entity resolution (default: 2.0)
--dry-run               Resolve entities but skip DB writes
--verbose               DEBUG-level logging
```

---

## Usage

### Start the loop

```bash
uv run -m autobioresearch.main
```

Options:

```
--config PATH       Path to config.yaml (default: ./config.yaml)
--cycles N          Stop after N cycles (default: 0 = run forever)
--no-conflicts      Skip conflict detection (Arm 2) — useful for initial data collection
```

### Reset the database (testing)

```bash
# Soft reset — truncates all data rows, keeps schema (ready to run again immediately)
uv run scripts/reset_db.py

# Hard reset — deletes the .db file and recreates it from scratch
uv run scripts/reset_db.py --hard

# Skip the confirmation prompt (useful in test scripts)
uv run scripts/reset_db.py --yes
uv run scripts/reset_db.py --hard --yes

# Custom DB path
uv run scripts/reset_db.py --db-path ./test_run.db --hard --yes
```

### Inspect conflicts

```bash
uv run scripts/inspect_conflicts.py
uv run scripts/inspect_conflicts.py --status open --type true_conflict --show-evidence
```

### Audit synonym overlaps

```bash
# Summary of synonyms that currently map to multiple entities
uv run scripts/validate_synonym_overlaps.py

# Show the specific entity rows behind each overlap
uv run scripts/validate_synonym_overlaps.py --show-rows --limit 20
```

### Export the knowledge graph

```bash
# JSON (nodes + edges + open conflicts)
uv run scripts/export_graph.py --format json --out graph.json

# GraphML (compatible with Gephi, Cytoscape, NetworkX)
uv run scripts/export_graph.py --format graphml --out graph.graphml

# Filter by confidence and evidence count
uv run scripts/export_graph.py --format json --min-confidence high --min-evidence 2
```

---

## Interaction schema

Each interaction record captures:

| Field | Description |
|---|---|
| `interaction_type` | `direct_binding`, `enzymatic`, `signaling`, `genetic`, `transcriptional`, `post_translational`, `metabolic`, `proximal_association`, `co_expression`, `transport` |
| `direction` | `A_to_B`, `B_to_A`, `bidirectional`, `undirected` |
| `effect` | `activates`, `inhibits`, `binds`, `phosphorylates`, `ubiquitinates`, `cleaves`, `recruits`, `localizes`, `transports` |
| `composite_confidence` | `high` / `medium` / `low` — aggregated from all evidence |
| `evidence_count` | Number of papers supporting this interaction |

Each **evidence** record captures:

| Field | Description |
|---|---|
| `evidence_type` | `in_vitro`, `in_vivo`, `structural`, `computational`, `co_expression`, `genetic_screen`, `clinical`, `curated_db` |
| `evidence_subtype` | `western_blot`, `co_ip`, `cryo_em`, `rnaseq`, `chip_seq`, `mass_spec`, etc. |
| `organism` | e.g. `Homo sapiens`, `Mus musculus` |
| `tissue_cell_type` | e.g. `HEK293`, `liver`, `CD4+ T cells` |
| `condition` | e.g. `hypoxia`, `LPS treatment`, `serum starvation` |
| `snippet` | Verbatim text from the paper supporting the interaction |

---

## Conflict classification

| Type | Penalty weight | Meaning |
|---|---|---|
| `true_conflict` | 1.0 | Same organism, same conditions, opposite claims — a real scientific controversy |
| `ambiguous` | 0.5 | Insufficient context to classify; more data needed |
| `context_dependent` | 0.0 | Different organisms/conditions explain the divergence — expected in biology |

Open true conflicts generate targeted resolution queries, which feed back into Arm 1's search queue.

---

## Entity normalization

Entities are deduplicated using a layered strategy:

1. **Exact synonym match** — against a fast in-memory cache + DB lookup
2. **Seeded alias table** — `config/synonyms.yaml` covers ~35 common aliases (TNF-alpha = TNFA = TNF, p53 = TP53, AKT = PKB, etc.)
3. **Fuzzy match** — `difflib.SequenceMatcher` at 0.92 threshold, scoped to the same entity type
4. **Create new** — if no match, a new canonical entity is created and all synonyms indexed

---

## Rate limits

| API | Default | With API key |
|---|---|---|
| PubMed (NCBI) | 3 req/s | 10 req/s (set `NCBI_API_KEY` in .env) |
| Semantic Scholar | 1 req/s | — |
| Anthropic / local LLM | 40 req/min (configurable) | — |

---

## License

MIT
