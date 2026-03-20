"""
Prompt templates and tool schemas for biological entity/interaction extraction.
"""

EXTRACTION_SYSTEM_PROMPT = """\
You are an expert biological literature analyst specializing in extracting structured \
knowledge from scientific papers. Your task is to identify biological entities and their \
interactions from the provided text.

CRITICAL RULES:
1. Only extract interactions that are EXPLICITLY stated or DIRECTLY measured in this paper's \
   text — not background knowledge the authors cite or assume.
2. For EVERY interaction, include the EXACT verbatim snippet (max 400 chars) from the text \
   that supports it. This is mandatory — interactions without a clear text basis are hallucinations.
3. Classify evidence strictly:
   - "in_vitro" = cell-free systems or cultured cell experiments
   - "in_vivo" = whole-organism experiments (animal models, patient samples)
   - "structural" = crystallography, cryo-EM, NMR structural data
   - "computational" = molecular dynamics, docking, bioinformatics predictions
   - "co_expression" = gene expression correlation studies
   - "genetic_screen" = CRISPR, RNAi, yeast two-hybrid, genetic epistasis
   - "clinical" = human patient data, clinical trials
4. Confidence levels:
   - "high" = multiple independent experiments or gold-standard assay (co-IP + functional validation)
   - "medium" = single clear direct experiment
   - "low" = indirect evidence, single data point, or inferred
5. If interaction direction is unclear, use "undirected".
6. For effect: use "activates", "inhibits", "binds", "phosphorylates", "ubiquitinates", \
   "cleaves", "recruits", "localizes", "transports", or null if unclear.
7. Report organisms precisely: "Homo sapiens", "Mus musculus", "Saccharomyces cerevisiae", etc.
8. Do NOT extract interactions just mentioned in the Introduction as prior work; only extract \
   what is demonstrated/measured in THIS paper's experiments.
"""

EXTRACTION_TOOL = {
    "name": "extract_biology",
    "description": "Extract biological entities and their interactions from paper text",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "description": "All biological entities mentioned in the text",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name as it appears in the text"
                        },
                        "entity_type": {
                            "type": "string",
                            "enum": [
                                "protein", "gene", "molecule", "metabolite",
                                "rna", "pathway", "phenotype", "disease",
                                "cell_type", "organism", "complex", "unknown"
                            ]
                        },
                        "synonyms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Other names for this entity mentioned in the text"
                        },
                        "organism": {
                            "type": "string",
                            "description": "Species (e.g. 'Homo sapiens') or null if not specified"
                        }
                    },
                    "required": ["name", "entity_type"]
                }
            },
            "interactions": {
                "type": "array",
                "description": "Biological interactions demonstrated in this paper's experiments",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity_a": {
                            "type": "string",
                            "description": "First entity in the interaction"
                        },
                        "entity_b": {
                            "type": "string",
                            "description": "Second entity in the interaction"
                        },
                        "interaction_type": {
                            "type": "string",
                            "enum": [
                                "direct_binding", "enzymatic", "signaling",
                                "genetic", "transcriptional", "translational",
                                "post_translational", "metabolic",
                                "proximal_association", "co_expression",
                                "transport", "unknown"
                            ]
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["A_to_B", "B_to_A", "bidirectional", "undirected"],
                            "description": "Direction of the interaction"
                        },
                        "effect": {
                            "type": "string",
                            "description": "Effect: activates, inhibits, binds, phosphorylates, ubiquitinates, cleaves, recruits, localizes, transports, or null"
                        },
                        "evidence_type": {
                            "type": "string",
                            "enum": [
                                "in_vitro", "in_vivo", "structural",
                                "computational", "co_expression",
                                "genetic_screen", "clinical", "unknown"
                            ]
                        },
                        "evidence_subtype": {
                            "type": "string",
                            "description": "Specific assay: western_blot, co_ip, rnaseq, cryo_em, mass_spec, chip_seq, etc."
                        },
                        "organism": {
                            "type": "string",
                            "description": "Species for this interaction (e.g. 'Homo sapiens')"
                        },
                        "tissue_cell_type": {
                            "type": "string",
                            "description": "Tissue or cell line (e.g. 'HEK293', 'liver', 'CD4+ T cells')"
                        },
                        "condition": {
                            "type": "string",
                            "description": "Experimental condition (e.g. 'hypoxia', 'serum starvation', 'LPS treatment')"
                        },
                        "assay_type": {
                            "type": "string",
                            "description": "Broader assay category if evidence_subtype doesn't cover it"
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"]
                        },
                        "confidence_score": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": "Numeric confidence 0.0-1.0"
                        },
                        "snippet": {
                            "type": "string",
                            "maxLength": 400,
                            "description": "EXACT verbatim text from the paper supporting this interaction (required)"
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Brief justification for confidence score and classification"
                        }
                    },
                    "required": [
                        "entity_a", "entity_b", "interaction_type", "direction",
                        "evidence_type", "confidence", "confidence_score",
                        "snippet", "reasoning"
                    ]
                }
            },
            "extraction_notes": {
                "type": "string",
                "description": "Any caveats, ambiguities, or notes about what was or wasn't extracted"
            }
        },
        "required": ["entities", "interactions"]
    }
}

# OpenAI-compatible function calling format (mirrors EXTRACTION_TOOL)
EXTRACTION_FUNCTION = {
    "type": "function",
    "function": {
        "name": EXTRACTION_TOOL["name"],
        "description": EXTRACTION_TOOL["description"],
        "parameters": EXTRACTION_TOOL["input_schema"],
    }
}
