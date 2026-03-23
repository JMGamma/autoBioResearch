"""
Controlled-vocabulary normalization for evidence context.
"""
from __future__ import annotations

import re

from autobioresearch.models import ExtractedInteractionRaw


class EvidenceNormalizer:
    _ORGANISM_RULES: list[tuple[tuple[str, ...], str]] = [
        (("human", "homo sapiens", "patient"), "human"),
        (("mouse", "murine", "mus musculus"), "mouse"),
        (("rat", "rattus norvegicus"), "rat"),
        (("mammal", "mammalian"), "mammal_unspecified"),
    ]
    _TISSUE_RULES: list[tuple[tuple[str, ...], str]] = [
        (("hek293", "293t"), "cell_line:hek293"),
        (("hela",), "cell_line:hela"),
        (("mcf7", "mcf-7"), "cell_line:mcf7"),
        (("jurkat",), "cell_line:jurkat"),
        (("liver", "hepatic"), "tissue:liver"),
        (("lung", "pulmonary"), "tissue:lung"),
        (("t cell", "t-cell", "cd4+", "cd8+"), "cell_type:t_cell"),
        (("macrophage",), "cell_type:macrophage"),
    ]
    _CONDITION_RULES: list[tuple[tuple[str, ...], str]] = [
        (("hypoxia", "hypoxic"), "hypoxia"),
        (("lps", "lipopolysaccharide"), "lps_stimulation"),
        (("serum starvation", "starved"), "serum_starvation"),
        (("tnf", "tnf-alpha", "tnfa"), "tnf_stimulation"),
        (("ifn", "interferon"), "interferon_stimulation"),
    ]
    _ASSAY_RULES: list[tuple[tuple[str, ...], str]] = [
        (("co-ip", "coip", "co immunoprecipitation", "co-immunoprecipitation"), "protein_interaction:coip"),
        (("western blot", "immunoblot"), "protein_detection:western_blot"),
        (("rna-seq", "rnaseq"), "expression:rnaseq"),
        (("chip-seq", "chip seq"), "binding:chip_seq"),
        (("cryo-em", "cryo em"), "structure:cryo_em"),
        (("mass spec", "mass spectrometry"), "proteomics:mass_spectrometry"),
    ]

    def normalize(self, interaction: ExtractedInteractionRaw) -> ExtractedInteractionRaw:
        return interaction.model_copy(update={
            "normalized_organism": self._normalize_value(interaction.organism, self._ORGANISM_RULES, prefix="organism"),
            "normalized_tissue_cell_type": self._normalize_value(interaction.tissue_cell_type, self._TISSUE_RULES, prefix="context"),
            "normalized_condition": self._normalize_value(interaction.condition, self._CONDITION_RULES, prefix="condition"),
            "normalized_assay_type": self._normalize_value(
                interaction.assay_type or interaction.evidence_subtype,
                self._ASSAY_RULES,
                prefix="assay",
            ),
        })

    def _normalize_value(
        self,
        value: str | None,
        rules: list[tuple[tuple[str, ...], str]],
        *,
        prefix: str,
    ) -> str | None:
        if not value:
            return None
        lowered = value.lower().strip()
        for aliases, canonical in rules:
            if any(alias in lowered for alias in aliases):
                return canonical
        cleaned = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
        return f"{prefix}:{cleaned}" if cleaned else None
