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


class LLMTruncatedError(Exception):
    """Raised when an LLM response is cut off due to max_tokens (finish_reason=length)
    and all JSON recovery strategies have failed. Callers may retry with the same input."""


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

# Hermes-style XML tool call format emitted by many local models:
#   <tool_call><function=name><parameter=p>value</parameter></function></tool_call>
_XML_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<function=([^>]+)>(.*?)</function>\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_XML_PARAM_RE = re.compile(
    r"<parameter=([^>]+)>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)


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

        # Build httpx timeout: None means no limit (safe for slow local LLMs).
        # Both SDKs accept an httpx.Timeout or a plain number of seconds.
        _timeout = config.llm_timeout_seconds  # None | int

        if self._api_type == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=config.anthropic_api_key,
                timeout=_timeout,
            )
        elif self._api_type == "openai_compatible":
            import openai
            self._client = openai.OpenAI(
                base_url=config.llm_base_url,
                api_key=config.llm_api_key or "none",
                timeout=_timeout,
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
            logger.warning("LLM returned no choices")
            return None

        tool_name = tool_function.get("function", {}).get("name", "tool_call")
        finish_reason = getattr(choice, "finish_reason", "unknown")

        if finish_reason == "length":
            logger.warning(
                f"LLM response truncated (finish_reason=length) for tool '{tool_name}'. "
                f"Response hit max_tokens={self._config.llm_max_tokens}. "
                f"Consider raising llm_max_tokens in config.yaml."
            )

        # Grab raw content BEFORE think-stripping — the model may embed the tool call
        # inside <think> tags (observed with Qwen3), which would otherwise be stripped.
        # Also check reasoning_content: some servers (Qwen/DeepSeek) move the entire
        # chain-of-thought (including the tool call) into a dedicated field, leaving
        # content empty.
        raw_content = getattr(choice.message, "content", "") or ""
        raw_reasoning = getattr(choice.message, "reasoning_content", "") or ""
        xml_result_pre = (
            self._parse_xml_tool_call(raw_content)
            or self._parse_xml_tool_call(raw_reasoning)
        )

        # Capture/log reasoning and strip <think> tags from content.
        clean_content = self._capture_reasoning(choice.message, context=tool_name)

        tool_calls = getattr(choice.message, "tool_calls", None)
        if tool_calls:
            raw = tool_calls[0].function.arguments
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Tool call JSON parse failed for '{tool_name}' "
                    f"(finish_reason={finish_reason}): {e}\n"
                    f"Raw arguments (first 300 chars): {raw[:300]}"
                )
                repaired = self._repair_truncated_json(raw)
                if repaired is not None:
                    logger.info(f"Recovered partial JSON from truncated tool call for '{tool_name}'")
                    return repaired
                result = self._extract_json_fallback(clean_content)
                if result is None and finish_reason == "length":
                    raise LLMTruncatedError(
                        f"LLM response truncated for tool '{tool_name}' "
                        f"(max_tokens={self._config.llm_max_tokens})"
                    )
                return result

        # No tool_calls — model used content field instead of the tool_calls mechanism.

        # 1. XML found in raw content (e.g. tool call was inside <think> block)
        if xml_result_pre is not None:
            logger.debug(f"Recovered tool call for '{tool_name}' via XML parser (raw content)")
            return xml_result_pre

        # 2. XML found in clean content (tool call outside <think>)
        xml_result_clean = self._parse_xml_tool_call(clean_content)
        if xml_result_clean is not None:
            logger.debug(f"Recovered tool call for '{tool_name}' via XML parser (clean content)")
            return xml_result_clean

        logger.warning(
            f"No tool_calls in LLM response for '{tool_name}' "
            f"(finish_reason={finish_reason}). "
            f"Content after <think> strip (first 300 chars): "
            f"{clean_content[:300] if clean_content else '(empty)'}"
        )
        result = self._extract_json_fallback(clean_content)
        if result is None and finish_reason == "length":
            raise LLMTruncatedError(
                f"LLM response truncated for tool '{tool_name}' "
                f"(max_tokens={self._config.llm_max_tokens})"
            )
        return result

    def _repair_truncated_json(self, raw: str) -> Optional[dict]:
        """
        Attempt to recover a dict from JSON that was cut off mid-stream (e.g. due to
        max_tokens). Strategy: walk the string character-by-character tracking open
        braces/brackets, then append the required closing characters.
        If that still fails, walk backwards to the last complete element and close there.
        """
        s = raw.strip()
        if not s:
            return None

        def _close(s: str) -> Optional[dict]:
            stack: list[str] = []
            in_string = False
            escape = False
            for ch in s:
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_string:
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch in "{[":
                    stack.append(ch)
                elif ch in "}]":
                    if stack:
                        stack.pop()
            if not stack:
                return None  # no repair needed / nothing to close
            closing = "".join("}" if c == "{" else "]" for c in reversed(stack))
            try:
                return json.loads(s + closing)
            except json.JSONDecodeError:
                return None

        # Attempt 1: close the open structure as-is
        result = _close(s)
        if result is not None:
            return result

        # Attempt 2: strip back to the last complete top-level comma-separated element
        # (handles a truncated last array entry by removing it then closing)
        for cut in (s.rfind(","), s.rfind("}")):
            if cut > 0:
                result = _close(s[: cut + 1])
                if result is not None:
                    return result

        return None

    def _parse_xml_tool_call(self, content: str) -> Optional[dict]:
        """
        Parse the Hermes-style XML tool call format that many local models emit
        instead of using the proper OpenAI tool_calls mechanism:

            <tool_call>
            <function=tool_name>
            <parameter=param_name>value</parameter>
            ...
            </function>
            </tool_call>

        Each parameter value is JSON-decoded if possible, otherwise kept as a string.
        Returns a dict of {param_name: value} or None if the format is not detected.
        """
        match = _XML_TOOL_CALL_RE.search(content)
        if not match:
            return None

        body = match.group(2)
        result: dict = {}
        for pm in _XML_PARAM_RE.finditer(body):
            name = pm.group(1).strip()
            value = pm.group(2).strip()
            try:
                result[name] = json.loads(value)
            except json.JSONDecodeError:
                result[name] = value

        if not result:
            return None

        logger.debug(f"Parsed XML-style tool call with params: {list(result.keys())}")
        return result

    def _extract_json_fallback(self, content: str) -> Optional[dict]:
        """Try to extract a JSON object from raw LLM content as a last resort."""
        if not content:
            logger.warning("JSON fallback: content is empty — model returned no usable output")
            return None
        matches = re.findall(r"\{[\s\S]+\}", content)
        for m in reversed(matches):  # try largest block first
            try:
                return json.loads(m)
            except json.JSONDecodeError:
                repaired = self._repair_truncated_json(m)
                if repaired is not None:
                    return repaired
        logger.warning(
            f"Could not extract JSON from LLM content. "
            f"Content (first 300 chars): {content[:300]}"
        )
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
