"""
Prompt templates and tool schemas for conflict analysis and query generation.
"""

CONFLICT_ANALYSIS_SYSTEM_PROMPT = """\
You are an expert in biological literature and experimental methodology. Your task is to \
determine whether two scientific claims about biological interactions genuinely contradict \
each other, or whether apparent contradictions are explained by experimental context differences.

KEY DISTINCTIONS:
- TRUE_CONFLICT: Same organism, same cell type, same conditions, opposing conclusions that \
  cannot be explained by methodological differences. This is a real scientific controversy.
- CONTEXT_DEPENDENT: Different organisms, cell lines, disease states, concentrations, or \
  assay conditions that could biologically explain divergent results. NOT a true conflict — \
  biology is context-dependent by nature.
- AMBIGUOUS: Insufficient context to classify. Needs more experimental data to resolve.

IMPORTANT CONSIDERATIONS:
- Species differences (mouse vs. human) are expected and not true conflicts.
- Concentration-dependent effects (activation at low dose, inhibition at high dose) are normal.
- Cell-type-specific effects are common and not inherently contradictory.
- Assay artifacts can cause false results — consider whether the methods are comparable.
- Publication year matters: newer structural data may supersede older biochemical claims.
- Consider whether one claim might be an indirect effect and the other a direct one.
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
You are an expert in biological literature search. Your task is to generate specific, \
targeted search queries that will find papers resolving a scientific conflict between \
two biological interaction claims.

Guidelines for good queries:
- Use specific protein/gene/molecule names rather than general terms
- Include MeSH terms in brackets for PubMed: [Title/Abstract], [MeSH Terms]
- Use Boolean operators: AND, OR, NOT
- For Semantic Scholar queries, use natural language descriptions
- Queries should be narrow enough to be relevant but broad enough to find papers
- Consider both the mechanism of the interaction and the broader biological context
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
