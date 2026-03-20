"""
Unified LLM client supporting both Anthropic API and OpenAI-compatible local LLMs.
Handles rate limiting, retries, and structured tool-use output.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from autobioresearch.config import AppConfig
from autobioresearch.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


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
            tool_choice={"type": "function", "function": {"name": tool_function["function"]["name"]}},
        )

        choice = response.choices[0] if response.choices else None
        if not choice:
            return None

        tool_calls = getattr(choice.message, "tool_calls", None)
        if tool_calls:
            raw = tool_calls[0].function.arguments
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse tool call JSON: {e}\nRaw: {raw[:500]}")
                return self._extract_json_fallback(choice.message.content or "")

        # Fallback: try to extract JSON from raw content for models without tool_use
        content = getattr(choice.message, "content", "") or ""
        return self._extract_json_fallback(content)

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
                return choice.message.content if choice else None
        except Exception as e:
            logger.error(f"LLM simple_completion failed: {e}")
            return None
