from types import SimpleNamespace

from autobioresearch.config import AppConfig
from autobioresearch.extractor.claude_client import LLMClient


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
