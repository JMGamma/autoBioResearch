import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from autobioresearch.config import AppConfig
from autobioresearch.extractor.claude_client import LLMClient, LLMTruncatedError


def _client_with_response(response):
    client = object.__new__(LLMClient)
    client._config = AppConfig(
        llm_api_type="openai_compatible",
        llm_base_url="http://localhost:11434/v1",
        entity_resolution_enabled=False,
    )
    client._reasoning_logger = None
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: response
            )
        )
    )
    return client


def test_openai_compatible_uses_legacy_function_call_arguments():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="",
                    function_call=SimpleNamespace(arguments='{"entities": [], "interactions": []}'),
                ),
            )
        ]
    )
    client = _client_with_response(response)

    result = client._call_openai_compatible(
        system="system",
        user="user",
        tool_function={"function": {"name": "extract_claims"}},
    )

    assert result == {"entities": [], "interactions": []}


def test_openai_compatible_extracts_json_from_list_reasoning_content():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=[],
                    reasoning_content=[
                        {"text": '{"entities": [], "interactions": [{"entity_a": "AKT1"}]}'}
                    ],
                ),
            )
        ]
    )
    client = _client_with_response(response)

    result = client._call_openai_compatible(
        system="system",
        user="user",
        tool_function={"function": {"name": "extract_claims"}},
    )

    assert result == {"entities": [], "interactions": [{"entity_a": "AKT1"}]}


def test_openai_compatible_extracts_tool_call_wrapper_from_reasoning_content():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="",
                    reasoning_content='<tool_call>{"name":"extract_biology","arguments":{"entities":[],"interactions":[{"entity_a":"MST1R","entity_b":"prostate cancer"}]}}</tool_call>',
                ),
            )
        ]
    )
    client = _client_with_response(response)

    result = client._call_openai_compatible(
        system="system",
        user="user",
        tool_function={"function": {"name": "extract_biology"}},
    )

    assert result == {
        "entities": [],
        "interactions": [{"entity_a": "MST1R", "entity_b": "prostate cancer"}],
    }


# ---------------------------------------------------------------------------
# Anthropic truncation regression tests
# ---------------------------------------------------------------------------

def _anthropic_client_with_response(response):
    """Build an LLMClient configured for Anthropic with a mocked messages.create."""
    client = object.__new__(LLMClient)
    # Use SimpleNamespace — AppConfig validates anthropic_api_key against .env, which
    # is not present in the test environment. _call_anthropic only reads these 3 fields.
    client._config = SimpleNamespace(
        llm_model="claude-sonnet-4-6",
        llm_max_tokens=4096,
        llm_temperature=0.0,
    )
    client._reasoning_logger = None
    client._api_type = "anthropic"
    client._client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: response
        )
    )
    return client


def test_anthropic_raises_truncated_error_when_tool_use_present_and_max_tokens():
    """
    Regression for the bug introduced in 74c335a and fixed in 943c68a.

    Before the fix, when Anthropic returned stop_reason=max_tokens WITH a tool_use
    block (i.e. partial content), _call_anthropic returned block.input silently.
    The extractor's retry loop saw no exception, called break immediately, and
    processed the paper with incomplete data — no retry, no truncation_review.log.

    After the fix, _call_anthropic must raise LLMTruncatedError so the extractor
    can retry and/or log to truncation_review.log.
    """
    tool_use_block = SimpleNamespace(
        type="tool_use",
        name="extract_biology",
        input={"entities": [], "interactions": []},  # partial/empty — truncated
    )
    response = SimpleNamespace(stop_reason="max_tokens", content=[tool_use_block])
    client = _anthropic_client_with_response(response)

    with pytest.raises(LLMTruncatedError):
        client._call_anthropic(
            system="system",
            user="user",
            tool={"name": "extract_biology"},
        )


def test_anthropic_raises_truncated_error_when_no_tool_use_and_max_tokens():
    """When stop_reason=max_tokens and no tool_use block, raises LLMTruncatedError."""
    text_block = SimpleNamespace(type="text", text="partial text")
    response = SimpleNamespace(stop_reason="max_tokens", content=[text_block])
    client = _anthropic_client_with_response(response)

    with pytest.raises(LLMTruncatedError):
        client._call_anthropic(
            system="system",
            user="user",
            tool={"name": "extract_biology"},
        )


def test_anthropic_returns_input_on_end_turn():
    """Normal case: stop_reason=end_turn with tool_use block returns the dict."""
    tool_use_block = SimpleNamespace(
        type="tool_use",
        name="extract_biology",
        input={"entities": [{"name": "BRCA1"}], "interactions": []},
    )
    response = SimpleNamespace(stop_reason="end_turn", content=[tool_use_block])
    client = _anthropic_client_with_response(response)

    result = client._call_anthropic(
        system="system",
        user="user",
        tool={"name": "extract_biology"},
    )

    assert result == {"entities": [{"name": "BRCA1"}], "interactions": []}


def test_openai_compatible_raises_truncated_error_when_reasoning_has_json_but_finish_reason_length():
    """
    Regression: when finish_reason=length and the model produced no tool_calls, the code
    fell back to _extract_json_fallback on reasoning_content. If reasoning contained any
    parseable JSON (e.g. the model's thinking schema), result was non-None and
    LLMTruncatedError was NOT raised — no retry, no truncation_review.log entry.

    Fix: always raise LLMTruncatedError when finish_reason=length in fallback paths,
    regardless of whether fallback JSON was found.
    """
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(
                    content="",
                    # reasoning_content has a valid-but-empty JSON that looks like extraction output
                    reasoning_content='{"entities": [], "interactions": []}',
                    tool_calls=None,
                    function_call=None,
                ),
            )
        ]
    )
    client = _client_with_response(response)

    with pytest.raises(LLMTruncatedError):
        client._call_openai_compatible(
            system="system",
            user="user",
            tool_function={"function": {"name": "extract_biology"}},
        )


def test_extractor_retries_on_truncation_and_logs_review():
    """
    Regression: the extractor must retry once and call _log_truncation_review
    when LLMTruncatedError is raised on every attempt.
    """
    from autobioresearch.extractor.extractor import PaperExtractor, _log_truncation_review
    from autobioresearch.extractor.normalizer import EntityNormalizer
    import autobioresearch.extractor.extractor as extractor_module

    config = SimpleNamespace(
        llm_truncation_retries=1,  # 1 retry = 2 total attempts
        max_chunk_chars=100_000,   # single chunk so loop runs once
        chunk_overlap_chars=0,
        llm_max_tokens=4096,
    )

    mock_llm = MagicMock()
    mock_llm.call_with_tool.side_effect = LLMTruncatedError("truncated")

    mock_normalizer = MagicMock(spec=EntityNormalizer)

    # Patch _log_truncation_review so we can assert it was called
    log_calls = []
    original_log = extractor_module._log_truncation_review

    def fake_log(*args, **kwargs):
        log_calls.append((args, kwargs))

    extractor_module._log_truncation_review = fake_log
    try:
        extractor = PaperExtractor.__new__(PaperExtractor)
        extractor._config = config
        extractor._llm = mock_llm
        extractor._normalizer = mock_normalizer
        extractor._verifier = MagicMock()
        extractor._evidence_normalizer = MagicMock()
        extractor._adjudicator = MagicMock()

        result = extractor.extract(
            paper_id="pmid:12345",
            title="Test paper",
            text="Some abstract text.",
        )
    finally:
        extractor_module._log_truncation_review = original_log

    # The LLM should have been called twice (initial + 1 retry)
    assert mock_llm.call_with_tool.call_count == 2

    # _log_truncation_review must have been called once (on final failure)
    assert len(log_calls) == 1
    assert log_calls[0][1].get("reason") == "truncation"

    # No interactions extracted (chunk was skipped after all retries failed)
    assert result.interactions == []
