"""
Prompt templates and tool schemas for conflict analysis and query generation.
"""

CONFLICT_ANALYSIS_SYSTEM_PROMPT = """\
You are an expert in biological literature and experimental methodology. Your task is to \
determine whether two scientific claims about biological interactions genuinely contradict \
each other, or whether apparent contradictions are explained by experimental context differences.

KEY DISTINCTIONS:
- CONTEXT_DEPENDENT: The divergent results are explained by biological differences in the \
  experimental context — different organisms, cell lines, disease states, concentrations, \
  assay conditions, isoforms, mutation status, feedback states, or any other factor that \
  could biologically account for the different outcomes. Both results are valid in their \
  respective contexts. This is the MOST COMMON explanation for apparent conflicts in biology.
- AMBIGUOUS: There is insufficient metadata to determine whether context explains the \
  difference. The discrepancy might be context-dependent but we cannot confirm without \
  more data.
- TRUE_CONFLICT: Identical or near-identical conditions (same organism, cell type, \
  concentration range, assay methodology) with directly opposing conclusions that cannot \
  be explained by any known biological mechanism. This should be rare — exhaust context \
  explanations before using this classification.

IMPORTANT: Biology is inherently context-dependent. Before classifying as TRUE_CONFLICT, \
actively consider:
- Could isoform, splice variant, or paralog differences explain the result?
- Could concentration/dose-response explain a sign reversal?
- Could disease state vs. normal tissue differ in relevant co-factors or feedback?
- Could one study measure a direct effect and the other a downstream consequence?
- Could assay sensitivity or temporal resolution differ in a meaningful way?
- Publication year: does one paper use higher-resolution methodology?
"""

CONFLICT_ANALYSIS_TOOL = {
    "name": "classify_conflict",
    "description": "Classify whether two biological interaction claims genuinely conflict",
    "input_schema": {
        "type": "object",
        "properties": {
            "conflict_type": {
                "type": "string",
                "enum": ["true_conflict", "context_dependent", "ambiguous"],
                "description": "Classification of the conflict"
            },
            "conflict_axis": {
                "type": "string",
                "description": "What dimension conflicts: effect, direction, evidence_interpretation, mechanism"
            },
            "context_difference": {
                "type": "object",
                "description": "Key context attributes that differ between the two claims",
                "properties": {
                    "organism": {"type": "string"},
                    "cell_type": {"type": "string"},
                    "condition": {"type": "string"},
                    "assay": {"type": "string"},
                    "other": {"type": "string"}
                }
            },
            "is_genuine_conflict": {
                "type": "boolean",
                "description": "True if this is a scientifically meaningful contradiction"
            },
            "reasoning": {
                "type": "string",
                "description": "Detailed reasoning for the classification"
            },
            "suggested_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 PubMed search queries that would help resolve this conflict"
            },
            "penalty_weight": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How much to penalize this conflict: true_conflict≈1.0, ambiguous≈0.5, context_dependent≈0.2"
            }
        },
        "required": ["conflict_type", "conflict_axis", "context_difference",
                     "is_genuine_conflict", "reasoning", "suggested_queries", "penalty_weight"]
    }
}

CONFLICT_ANALYSIS_FUNCTION = {
    "type": "function",
    "function": {
        "name": CONFLICT_ANALYSIS_TOOL["name"],
        "description": CONFLICT_ANALYSIS_TOOL["description"],
        "parameters": CONFLICT_ANALYSIS_TOOL["input_schema"],
    }
}


QUERY_GENERATION_SYSTEM_PROMPT = """\
You are an expert in biological literature search. Two papers report conflicting results \
for the same biological interaction. Your task is to generate targeted search queries \
that will find papers shedding light on this discrepancy.

A valid resolution can take two forms — keep BOTH in mind when designing queries:
1. CONTEXT EXPLANATION: A biological mechanism that explains why both results are correct \
   in different conditions — e.g. isoform-specific effects, concentration-dependent \
   biphasic responses, feedback loops that differ by cell state, splice variants, \
   post-translational modification status, co-factor availability, or disease-stage effects. \
   Finding this is often more valuable than invalidating one paper.
2. ADJUDICATION: Papers with higher-resolution methods (structural, genetic, in vivo) that \
   directly test which claim is correct under comparable conditions.

Design a mix of queries covering both possibilities. Use specific protein/gene/molecule \
names, MeSH terms in brackets for PubMed, Boolean operators, and natural language for \
Semantic Scholar. Prefer mechanistic and review papers over descriptive studies.
"""

CONTEXT_ENRICHMENT_SYSTEM_PROMPT = """\
You are an expert in biological literature search. Two papers report the same biological \
interaction producing different outcomes depending on experimental context (cell type, \
tissue, organism, condition, etc.). This is expected in biology — the same pathway can \
behave very differently in different contexts.

Your task is to generate search queries that will find papers explaining WHY this \
context-dependence exists. The goal is NOT to invalidate either claim, but to find \
mechanistic explanations for the context-sensitivity: isoform differences, co-factor \
availability, chromatin state, feedback loops, alternative splicing, post-translational \
modifications, or other biological reasons the interaction behaves differently across contexts.

Guidelines:
- Focus on mechanistic "why" questions, not "which result is correct"
- Search for the specific contextual factors that differ between the two claims
- Look for review articles on context-dependent regulation of these entities
- Use specific protein/gene/molecule names
- Include MeSH terms for PubMed queries
"""

QUERY_GENERATION_TOOL = {
    "name": "generate_queries",
    "description": "Generate search queries to find papers resolving a biological conflict",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Brief summary of the conflict being investigated"
            },
            "queries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "query_text": {
                            "type": "string",
                            "description": "The search query string"
                        },
                        "source_api": {
                            "type": "string",
                            "enum": ["pubmed", "semantic_scholar"],
                            "description": "Which API to use for this query"
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Why this query should help resolve the conflict"
                        }
                    },
                    "required": ["query_text", "source_api", "reasoning"]
                },
                "minItems": 2,
                "maxItems": 5
            }
        },
        "required": ["summary", "queries"]
    }
}

QUERY_GENERATION_FUNCTION = {
    "type": "function",
    "function": {
        "name": QUERY_GENERATION_TOOL["name"],
        "description": QUERY_GENERATION_TOOL["description"],
        "parameters": QUERY_GENERATION_TOOL["input_schema"],
    }
}

# Context-enrichment tool shares the same schema as query generation —
# only the system prompt differs (mechanistic "why" framing vs. resolution framing).
CONTEXT_ENRICHMENT_TOOL = {
    "name": "generate_queries",
    "description": (
        "Generate search queries to find papers explaining WHY this biological interaction "
        "is context-dependent — the mechanistic basis for the context-sensitivity"
    ),
    "input_schema": QUERY_GENERATION_TOOL["input_schema"],
}

CONTEXT_ENRICHMENT_FUNCTION = {
    "type": "function",
    "function": {
        "name": CONTEXT_ENRICHMENT_TOOL["name"],
        "description": CONTEXT_ENRICHMENT_TOOL["description"],
        "parameters": CONTEXT_ENRICHMENT_TOOL["input_schema"],
    }
}
