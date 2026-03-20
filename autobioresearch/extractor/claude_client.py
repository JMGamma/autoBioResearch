"""
Unified LLM client supporting both Anthropic API and OpenAI-compatible local LLMs.
Handles rate limiting, retries, and structured tool-use output.

Reasoning capture (openai_compatible only):
  When config.log_reasoning is True, the client captures chain-of-thought from:
    1. <think>…</think> tags in the response content  (Qwen3, DeepSeek-R1, QwQ, …)
    2. message.reasoning_content field                (DeepSeek-style server APIs)
  Captured reasoning is written to a dedicated rotating log file defined by
  config.reasoning_log_file and never mixed into the main application log.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
import time
from pathlib import Path
from typing import Any, Optional

from autobioresearch.config import AppConfig
from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Module-level reasoning logger — configured lazily on first LLMClient instantiation.
_reasoning_logger: Optional[logging.Logger] = None


def _get_reasoning_logger(config: AppConfig) -> Optional[logging.Logger]:
    """Return a dedicated rotating-file logger for LLM reasoning traces, or None."""
    global _reasoning_logger
    if not config.log_reasoning:
        return None
    if _reasoning_logger is not None:
        return _reasoning_logger

    log_path = Path(config.reasoning_log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    rl = logging.getLogger("autobioresearch.reasoning")
    rl.setLevel(logging.DEBUG)
    rl.propagate = False  # keep reasoning out of the main log

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=config.reasoning_log_max_bytes,
        backupCount=config.reasoning_log_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s\n%(message)s\n" + "-" * 80))
    rl.addHandler(handler)

    _reasoning_logger = rl
    return _reasoning_logger


# Regex for <think>…</think> blocks (Qwen3, QwQ, etc.)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


class LLMClient:
    """
    Unified LLM client.
    - llm_api_type="anthropic": uses anthropic SDK (tool_use pattern)
    - llm_api_type="openai_compatible": uses openai SDK pointed at llm_base_url
      (function_calling pattern — works with Ollama, LM Studio, vLLM, etc.)
    """

    def __init__(self, config: AppConfig):
        self._config = config
        self._limiter = RateLimiter(config.llm_requests_per_minute / 60.0)
        self._api_type = config.llm_api_type
        self._reasoning_logger = _get_reasoning_logger(config)

        if self._api_type == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        elif self._api_type == "openai_compatible":
            import openai
            self._client = openai.OpenAI(
                base_url=config.llm_base_url,
                api_key=config.llm_api_key or "none",
            )
        else:
            raise ValueError(f"Unknown llm_api_type: {self._api_type!r}. Use 'anthropic' or 'openai_compatible'.")

    def call_with_tool(
        self,
        system: str,
        user: str,
        tool: dict,
        tool_function: dict,
    ) -> Optional[dict]:
        """
        Call the LLM and extract the tool call result.

        Args:
            system: System prompt text
            user: User message text
            tool: Anthropic tool definition dict
            tool_function: OpenAI-compatible function definition dict

        Returns:
            Parsed dict from tool call arguments, or None on failure.
        """
        for attempt in range(self._config.llm_max_retries + 1):
            try:
                self._limiter.acquire()
                if self._api_type == "anthropic":
                    return self._call_anthropic(system, user, tool)
                else:
                    return self._call_openai_compatible(system, user, tool_function)
            except Exception as e:
                if attempt < self._config.llm_max_retries:
                    wait = self._config.llm_retry_backoff_seconds * (2 ** attempt)
                    logger.warning(f"LLM call failed (attempt {attempt+1}): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    logger.error(f"LLM call failed after {self._config.llm_max_retries+1} attempts: {e}")
                    return None

    def _call_anthropic(self, system: str, user: str, tool: dict) -> Optional[dict]:
        response = self._client.messages.create(
            model=self._config.llm_model,
            max_tokens=self._config.llm_max_tokens,
            temperature=self._config.llm_temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return block.input

        logger.warning("Anthropic response did not contain expected tool_use block")
        return None

    def _capture_reasoning(self, message, context: str = "") -> str:
        """
        Extract and log reasoning/thinking from an OpenAI-compatible chat message.

        Looks in two places:
          1. message.reasoning_content  — dedicated field (DeepSeek-style APIs)
          2. <think>…</think> tags      — inline in message.content (Qwen3, QwQ, etc.)

        Returns the raw content with any <think> blocks stripped out (so callers
        never accidentally try to parse reasoning text as JSON).
        """
        if not self._reasoning_logger:
            # Reasoning logging disabled — still strip <think> tags so they don't
            # corrupt JSON parsing downstream.
            content = getattr(message, "content", "") or ""
            return _THINK_RE.sub("", content).strip()

        reasoning_parts: list[str] = []

        # Source 1: explicit reasoning_content field
        rc = getattr(message, "reasoning_content", None)
        if rc:
            reasoning_parts.append(rc.strip())

        # Source 2: <think>…</think> blocks inside content
        content = getattr(message, "content", "") or ""
        for match in _THINK_RE.finditer(content):
            block = match.group(1).strip()
            if block:
                reasoning_parts.append(block)

        if reasoning_parts:
            header = f"[{context}]" if context else "[reasoning]"
            self._reasoning_logger.debug(
                f"{header}\n" + "\n---\n".join(reasoning_parts)
            )
        else:
            if self._reasoning_logger:
                self._reasoning_logger.debug(
                    f"[{context or 'reasoning'}] (no reasoning content found in response)"
                )

        # Return content with <think> blocks removed
        return _THINK_RE.sub("", content).strip()

    def _call_openai_compatible(self, system: str, user: str, tool_function: dict) -> Optional[dict]:
        response = self._client.chat.completions.create(
            model=self._config.llm_model,
            max_tokens=self._config.llm_max_tokens,
            temperature=self._config.llm_temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[tool_function],
            # Local inference servers (Ollama, LM Studio, vLLM, etc.) only accept
            # the string forms "none" | "auto" | "required" — not the object form
            # {"type": "function", "function": {...}} that the real OpenAI API accepts.
            # "required" forces the model to call one of the supplied tools, which is
            # exactly what we want since we always pass exactly one tool here.
            tool_choice="required",
        )

        choice = response.choices[0] if response.choices else None
        if not choice:
            return None

        # Capture reasoning before touching content — also strips <think> tags.
        tool_name = tool_function.get("function", {}).get("name", "tool_call")
        clean_content = self._capture_reasoning(choice.message, context=tool_name)

        tool_calls = getattr(choice.message, "tool_calls", None)
        if tool_calls:
            raw = tool_calls[0].function.arguments
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse tool call JSON: {e}\nRaw: {raw[:500]}")
                return self._extract_json_fallback(clean_content)

        # Fallback: try to extract JSON from cleaned content
        return self._extract_json_fallback(clean_content)

    def _extract_json_fallback(self, content: str) -> Optional[dict]:
        """Try to extract a JSON object from raw LLM content as a last resort."""
        import re
        matches = re.findall(r"\{[\s\S]+\}", content)
        for m in reversed(matches):  # try largest block first
            try:
                return json.loads(m)
            except json.JSONDecodeError:
                continue
        logger.warning("Could not extract JSON from LLM response content")
        return None

    def simple_completion(self, system: str, user: str) -> Optional[str]:
        """Plain text completion without tool calling."""
        try:
            self._limiter.acquire()
            if self._api_type == "anthropic":
                response = self._client.messages.create(
                    model=self._config.llm_model,
                    max_tokens=self._config.llm_max_tokens,
                    temperature=self._config.llm_temperature,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return response.content[0].text if response.content else None
            else:
                response = self._client.chat.completions.create(
                    model=self._config.llm_model,
                    max_tokens=self._config.llm_max_tokens,
                    temperature=self._config.llm_temperature,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                choice = response.choices[0] if response.choices else None
                if not choice:
                    return None
                # Strip <think> blocks (and log them if enabled)
                return self._capture_reasoning(choice.message, context="simple_completion") or None
        except Exception as e:
            logger.error(f"LLM simple_completion failed: {e}")
            return None
