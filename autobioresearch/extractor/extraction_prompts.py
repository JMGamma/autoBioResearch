"""
Prompt templates and tool schemas for biological entity/interaction extraction.
"""

EXTRACTION_SYSTEM_PROMPT = """\
You are an expert biological literature analyst specializing in extracting structured \
knowledge from scientific papers. Your task is to identify biological entities and their \
interactions from the provided text.

SCOPE — MAMMALS ONLY:
This database covers mammalian biology exclusively. Only extract entities and interactions \
studied in mammalian species (humans, mice, rats, primates, pigs, cows, dogs, rabbits, etc.). \
SKIP and DO NOT extract anything studied solely in: plants (Arabidopsis, rice, maize, etc.), \
yeast (Saccharomyces, Schizosaccharomyces, etc.), insects (Drosophila, mosquito, etc.), \
nematodes (C. elegans), fish (zebrafish, Danio rerio, etc.), frogs (Xenopus, etc.), \
bacteria, archaea, or viruses studied only in non-mammalian hosts. \
If a paper studies both yeast and human cells, extract only the human/mammalian findings.

EXCEPTION — EXTERNAL ORGANISMS INTERACTING WITH MAMMALIAN BIOLOGY:
Pathogens and symbionts are valid entities when they natively interact with mammalian \
biology (e.g. SARS-CoV-2 spike protein binding human ACE2, H. pylori activating NF-κB \
in gastric epithelium, Lactobacillus reuteri modulating gut immune cells). Extract these \
with entity_type "pathogen" or "symbiont" as appropriate.

CRITICAL EXCLUSION — HETEROLOGOUS EXPRESSION SYSTEMS:
DO NOT extract interactions where a non-mammalian organism is used purely as a \
production or assay host for mammalian proteins. Skip interactions when: \
E. coli or yeast is used solely to express/purify a recombinant mammalian protein; \
baculovirus/insect-cell systems are used to produce a mammalian kinase or receptor; \
yeast two-hybrid is used as a detection platform for mammalian protein interactions; \
Xenopus oocytes are injected with mammalian mRNA only for electrophysiology readouts. \
The test: is the non-mammalian organism acting as an endogenous biological agent in the \
interaction, or merely a heterologous production/assay host? Only the former is in scope.

ENTITY TYPE GUIDANCE:
Use the most specific type available. Key distinctions:
- "modification": Use when a post-translational or chemical modification IS the entity \
  being discussed (e.g. "H3K27 acetylation", "AKT phosphorylation at Ser473", \
  "K48-linked ubiquitination"). This is distinct from using "phosphorylates" as an \
  interaction effect — use this type when the modification itself is the subject.
- "structure": Use for subcellular organelles (mitochondria, nucleus, Golgi apparatus, \
  endoplasmic reticulum, lysosome, centrosome) AND anatomical structures/tissues \
  (retina, choroid, fascia, cortex, liver, hippocampus, stroma). Do NOT classify \
  these as "cell_type" or "organ" — use "structure".
- "pathogen": Use for disease-causing external organisms interacting with mammalian \
  biology (e.g. SARS-CoV-2, Mycobacterium tuberculosis, Candida albicans, \
  Plasmodium falciparum, influenza virus). Use when the organism is associated with \
  infection or disease.
- "symbiont": Use for non-pathogenic external organisms — commensal or mutualistic \
  microorganisms (e.g. Lactobacillus reuteri, Bacteroides thetaiotaomicron, gut \
  microbiome members described in a beneficial or neutral context). When ambiguous \
  (e.g. E. coli as a model/lab strain), default to "pathogen".

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
   - "cited_claim" = interaction cited from a prior publication or stated as established \
     background — the original evidence type is unknown from this paper alone
4. Confidence levels:
   - "high" = multiple independent experiments or gold-standard assay (co-IP + functional validation)
   - "medium" = single clear direct experiment
   - "low" = indirect evidence, single data point, or inferred
5. If interaction direction is unclear, use "undirected".
6. For effect: use "activates", "inhibits", "binds", "phosphorylates", "ubiquitinates", \
   "cleaves", "recruits", "localizes", "transports", or null if unclear.
7. Report organisms precisely: "Homo sapiens", "Mus musculus", "Rattus norvegicus", etc.
8. Evidence source determines evidence_type and confidence — apply these rules strictly:
   - Interactions DIRECTLY MEASURED in THIS paper's experiments → evidence_type from rule 3, \
     confidence based on rules 3–4 above.
   - Interactions cited from prior work or mentioned as established background (e.g. "It is known \
     that X activates Y", "Previous studies showed...", "as reported by Smith et al.") → \
     evidence_type MUST be "cited_claim", confidence "low", reasoning must note it is cited \
     rather than directly demonstrated here.
   - Review papers that summarise a field → ALL interactions must be evidence_type "cited_claim" \
     and confidence "low" unless the review itself presents new meta-analysis or pooled data.
   - Do NOT fabricate interactions not mentioned anywhere in the text.
"""

EXTRACTION_TOOL = {
    "name": "extract_biology",
    "description": "Extract biological entities and their interactions from paper text",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "description": "All biological entities mentioned in the text. MUST be an array of objects — use [] if none found. Never use a string.",
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
                                "cell_type", "cell_line", "organism", "complex",
                                "modification", "structure", "pathogen", "symbiont",
                                "unknown"
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
                "description": "Biological interactions demonstrated in this paper's experiments. MUST be an array of objects — use [] if none found. Never use a string.",
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
                                "genetic_screen", "clinical",
                                "cited_claim", "unknown"
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


VERIFICATION_SYSTEM_PROMPT = """\
You are validating a previously extracted biological interaction claim against the paper text.

Check only whether the claimed fields are supported by the supplied evidence snippet and paper text.
Be conservative. If support is unclear, mark the field unsupported and set overall_status to "needs_review".

Focus on these fields:
- effect
- direction
- organism
- tissue_cell_type
- condition

Rules:
1. Treat the snippet as the strongest evidence.
2. Use the paper title/abstract only for context, not to hallucinate support.
3. If a field is absent from the claim, mark it supported.
4. If support is indirect or ambiguous, mark unsupported.
5. Return concise notes.
"""

VERIFICATION_TOOL = {
    "name": "verify_interaction_claim",
    "description": "Validate whether an extracted interaction claim is supported by the paper text",
    "input_schema": {
        "type": "object",
        "properties": {
            "overall_status": {
                "type": "string",
                "enum": ["verified", "needs_review"],
            },
            "verification_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "supported_fields": {
                "type": "object",
                "properties": {
                    "effect": {"type": "boolean"},
                    "direction": {"type": "boolean"},
                    "organism": {"type": "boolean"},
                    "tissue_cell_type": {"type": "boolean"},
                    "condition": {"type": "boolean"},
                },
                "required": ["effect", "direction", "organism", "tissue_cell_type", "condition"],
            },
            "notes": {
                "type": "string",
                "description": "Short explanation of unsupported or uncertain fields",
            },
        },
        "required": ["overall_status", "verification_score", "supported_fields", "notes"],
    },
}

VERIFICATION_FUNCTION = {
    "type": "function",
    "function": {
        "name": VERIFICATION_TOOL["name"],
        "description": VERIFICATION_TOOL["description"],
        "parameters": VERIFICATION_TOOL["input_schema"],
    }
}
