"""
Configuration loader.
Reads config.yaml for defaults; environment variables (and .env file) override.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM backend ---
    llm_api_type: str = "anthropic"          # anthropic | openai_compatible
    llm_model: str = "claude-sonnet-4-6"
    llm_base_url: Optional[str] = None       # for openai_compatible local LLMs
    llm_api_key: Optional[str] = None        # for openai_compatible; many servers accept "none"
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")

    # --- LLM tuning ---
    llm_temperature: float = 0.1
    llm_max_tokens: int = 8192   # output cap; extraction JSON is ~3-6k tokens even for large papers
    llm_requests_per_minute: int = 40
    llm_max_retries: int = 3
    llm_retry_backoff_seconds: float = 5.0
    llm_timeout_seconds: Optional[int] = 600  # 10 min hard cap; kills runaway generation loops

    # --- Database ---
    db_path: str = "./autobioresearch.db"

    # --- Paper fetching ---
    crawler_threads: int = 4
    papers_per_query: int = 50
    max_full_text_per_cycle: int = 20
    pubmed_requests_per_second: float = 3.0
    semantic_scholar_requests_per_second: float = 1.0
    ncbi_api_key: Optional[str] = Field(default=None, alias="NCBI_API_KEY")

    # --- Extraction ---
    max_chunk_chars: int = 6000
    chunk_overlap_chars: int = 200
    min_snippet_length: int = 20
    max_snippet_length: int = 400
    snippet_fuzzy_threshold: float = 0.75
    verification_enabled: bool = True
    claims_to_verify_per_cycle: int = 25
    verification_min_confidence_score: float = 0.55

    # --- Conflict detection ---
    max_candidate_pairs_per_cycle: int = 500
    conflicts_to_analyze_per_cycle: int = 20

    # --- Entity normalization ---
    fuzzy_match_threshold: float = 0.92
    min_interactions_per_entity: int = 3
    entity_resolution_enabled: bool = True   # UniProt + ChEBI external lookups
    entity_resolution_requests_per_second: float = 3.0
    entity_resolution_timeout: int = 10

    # --- Perturbation ---
    perturbation_combination_exponent: float = 0.55  # α in: 1 - ∏(1 - |xᵢ|^α) multi-path score combination

    # --- Scoring ---
    penalty: float = 2.0

    # --- Loop control ---
    cycle_sleep_seconds: int = 300
    max_cycles: int = 0                      # 0 = run forever
    queries_per_cycle: int = 10
    papers_per_cycle: int = 50
    targeted_queries_per_cycle: int = 10
    under_supported_seed_evidence_max: int = 1

    # --- Logging ---
    log_level: str = "INFO"
    log_file: str = "./logs/autobioresearch.log"

    # --- Reasoning / thinking capture (openai_compatible only) ---
    # Captures <think>…</think> blocks (Qwen3, DeepSeek-R1, QwQ, etc.) and/or
    # the reasoning_content field exposed by some server APIs (DeepSeek-style).
    log_reasoning: bool = False
    reasoning_log_file: str = "./logs/reasoning.log"
    reasoning_log_max_bytes: int = 10_485_760   # 10 MB per file
    reasoning_log_backup_count: int = 3

    # --- Seed queries (loaded from YAML, not env) ---
    seed_queries: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_llm_config(self) -> "AppConfig":
        if self.llm_api_type == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY must be set when llm_api_type is 'anthropic'. "
                "Add it to your .env file."
            )
        if self.llm_api_type == "openai_compatible" and not self.llm_base_url:
            raise ValueError(
                "llm_base_url must be set in config.yaml when llm_api_type is 'openai_compatible'."
            )
        return self

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "AppConfig":
        yaml_path = Path(path)
        data: dict = {}
        if yaml_path.exists():
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
        # env vars and .env take precedence over yaml (handled by pydantic-settings)
        return cls(**data)
