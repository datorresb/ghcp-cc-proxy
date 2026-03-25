"""Unit tests for request/response translation functions.

These tests verify the Anthropic ↔ OpenAI translation logic without
making any real API calls.
"""

import json
import sys
import os

# Add parent directory to path so we can import copilot_proxy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import copilot_proxy as proxy


# ── Tool translation: Request ────────────────────────────────────────

def test_tools_translated_to_openai_functions():
    """Anthropic tools[] should become OpenAI tools[] with function format."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather for a city",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ],
    }
    result = proxy._anthropic_to_openai(body)

    assert "tools" in result
    assert len(result["tools"]) == 1
    tool = result["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "get_weather"
    assert tool["function"]["description"] == "Get weather for a city"
    assert tool["function"]["parameters"]["type"] == "object"
    assert "city" in tool["function"]["parameters"]["properties"]


def test_tool_choice_auto():
    """tool_choice 'auto' maps to OpenAI 'auto'."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [{"name": "t", "input_schema": {"type": "object", "properties": {}}}],
        "tool_choice": "auto",
    }
    result = proxy._anthropic_to_openai(body)
    assert result["tool_choice"] == "auto"


def test_tool_choice_any():
    """tool_choice 'any' maps to OpenAI 'required'."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [{"name": "t", "input_schema": {"type": "object", "properties": {}}}],
        "tool_choice": "any",
    }
    result = proxy._anthropic_to_openai(body)
    assert result["tool_choice"] == "required"


def test_tool_choice_none():
    """tool_choice 'none' maps to OpenAI 'none'."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [{"name": "t", "input_schema": {"type": "object", "properties": {}}}],
        "tool_choice": "none",
    }
    result = proxy._anthropic_to_openai(body)
    assert result["tool_choice"] == "none"


def test_tool_choice_specific():
    """tool_choice with specific tool maps to OpenAI function choice."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [{"name": "get_weather", "input_schema": {"type": "object", "properties": {}}}],
        "tool_choice": {"type": "tool", "name": "get_weather"},
    }
    result = proxy._anthropic_to_openai(body)
    assert result["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}


def test_tool_use_in_assistant_history():
    """tool_use blocks in assistant messages become tool_calls."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "Weather?"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_123", "name": "get_weather", "input": {"city": "Paris"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_123", "content": "Sunny"},
            ]},
        ],
    }
    result = proxy._anthropic_to_openai(body)
    msgs = result["messages"]

    # First: user message
    assert msgs[0]["role"] == "user"
    # Second: assistant with tool_calls
    assert msgs[1]["role"] == "assistant"
    assert "tool_calls" in msgs[1]
    assert msgs[1]["tool_calls"][0]["id"] == "toolu_123"
    assert msgs[1]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert json.loads(msgs[1]["tool_calls"][0]["function"]["arguments"]) == {"city": "Paris"}
    # Third: tool result
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "toolu_123"
    assert msgs[2]["content"] == "Sunny"


def test_no_tools_no_tools_field():
    """Without tools in request, result should not have tools field."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    result = proxy._anthropic_to_openai(body)
    assert "tools" not in result
    assert "tool_choice" not in result


# ── Tool translation: Response ───────────────────────────────────────

def test_tool_calls_in_response():
    """OpenAI tool_calls in response become Anthropic tool_use blocks."""
    oai_response = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Paris"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    result = proxy._openai_to_anthropic(oai_response, "claude-sonnet-4.6")

    assert result["stop_reason"] == "tool_use"
    tool_uses = [b for b in result["content"] if b["type"] == "tool_use"]
    assert len(tool_uses) == 1
    assert tool_uses[0]["name"] == "get_weather"
    assert tool_uses[0]["input"] == {"city": "Paris"}
    assert "id" in tool_uses[0]


def test_multiple_tool_calls_in_response():
    """Multiple tool_calls become multiple tool_use blocks."""
    oai_response = {
        "choices": [{
            "message": {
                "content": "Let me check both.",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
                    {"id": "call_2", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "London"}'}},
                ],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    result = proxy._openai_to_anthropic(oai_response, "claude-sonnet-4.6")

    assert result["stop_reason"] == "tool_use"
    text_blocks = [b for b in result["content"] if b["type"] == "text"]
    tool_blocks = [b for b in result["content"] if b["type"] == "tool_use"]
    assert len(text_blocks) == 1
    assert text_blocks[0]["text"] == "Let me check both."
    assert len(tool_blocks) == 2


def test_no_tool_calls_normal_response():
    """Normal response without tool_calls works as before."""
    oai_response = {
        "choices": [{
            "message": {"content": "Hello!", "reasoning_content": ""},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    result = proxy._openai_to_anthropic(oai_response, "claude-sonnet-4.6")

    assert result["stop_reason"] == "end_turn"
    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"] == "Hello!"


def test_malformed_tool_arguments():
    """Malformed JSON in tool arguments doesn't crash, returns empty dict."""
    oai_response = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "test", "arguments": "not json"}},
                ],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    result = proxy._openai_to_anthropic(oai_response, "claude-sonnet-4.6")
    tool_uses = [b for b in result["content"] if b["type"] == "tool_use"]
    assert len(tool_uses) == 1
    assert tool_uses[0]["input"] == {}


# ── Model mapping ────────────────────────────────────────────────────

def test_model_mapping_sonnet():
    """claude-sonnet-4-6 maps to claude-sonnet-4.6."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    result = proxy._anthropic_to_openai(body)
    assert result["model"] == "claude-sonnet-4.6"


def test_model_mapping_prefix():
    """claude-haiku-4-5-20251001 prefix-matches to claude-haiku-4.5."""
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    result = proxy._anthropic_to_openai(body)
    assert result["model"] == "claude-haiku-4.5"


def test_unknown_model_passthrough():
    """Unknown models pass through unchanged."""
    body = {
        "model": "some-unknown-model",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    result = proxy._anthropic_to_openai(body)
    assert result["model"] == "some-unknown-model"
