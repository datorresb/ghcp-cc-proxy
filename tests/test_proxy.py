"""Tests for copilot_proxy.py — unit tests for conversion logic + integration tests against live proxy."""

import json
import subprocess
import time
import os
import signal
import pytest
import urllib.request
import urllib.error

# ── Import conversion functions directly ─────────────────────────────

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from copilot_proxy import _anthropic_to_openai, _openai_to_anthropic, _map_model, _sse_line


# ═══════════════════════════════════════════════════════════════════════
# Unit Tests — no network, no proxy needed
# ═══════════════════════════════════════════════════════════════════════


class TestModelMapping:
    def test_direct_match(self):
        assert _map_model("claude-sonnet-4") == "claude-sonnet-4"
        assert _map_model("claude-opus-4-5") == "claude-opus-4.5"
        assert _map_model("claude-haiku-4-5") == "claude-haiku-4.5"

    def test_prefix_match(self):
        assert _map_model("claude-opus-4-5-20250514") == "claude-opus-4.5"
        assert _map_model("claude-sonnet-4-5-20250514") == "claude-sonnet-4.5"

    def test_alias(self):
        assert _map_model("opus") == "claude-opus-4.5"
        assert _map_model("sonnet") == "claude-sonnet-4"
        assert _map_model("haiku") == "claude-haiku-4.5"

    def test_passthrough_unknown(self):
        assert _map_model("gpt-4o") == "gpt-4o"
        assert _map_model("gemini-pro") == "gemini-pro"


class TestAnthropicToOpenAI:
    def test_simple_message(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 100,
        })
        assert result["model"] == "claude-sonnet-4"
        assert result["messages"] == [{"role": "user", "content": "hello"}]
        assert result["max_tokens"] == 100

    def test_system_prompt_string(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "system": "You are helpful",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50,
        })
        assert result["messages"][0] == {"role": "system", "content": "You are helpful"}
        assert result["messages"][1] == {"role": "user", "content": "hi"}

    def test_system_prompt_list(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "system": [{"type": "text", "text": "Be concise"}],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50,
        })
        assert result["messages"][0] == {"role": "system", "content": "Be concise"}

    def test_system_prompt_multi_block(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "system": [
                {"type": "text", "text": "Line one"},
                {"type": "text", "text": "Line two"},
            ],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50,
        })
        assert result["messages"][0]["content"] == "Line one\nLine two"

    def test_content_blocks(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this"},
                    {"type": "text", "text": "and this"},
                ],
            }],
            "max_tokens": 50,
        })
        assert result["messages"][0]["content"] == "Look at this\nand this"

    def test_tool_result_flattened(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": "result text"},
                ],
            }],
            "max_tokens": 50,
        })
        assert "result text" in result["messages"][0]["content"]

    def test_tool_result_nested_blocks(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "x",
                    "content": [{"type": "text", "text": "nested result"}],
                }],
            }],
            "max_tokens": 50,
        })
        assert "nested result" in result["messages"][0]["content"]

    def test_tool_use_serialized(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "messages": [{
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "t1",
                    "name": "get_weather",
                    "input": {"city": "NYC"},
                }],
            }],
            "max_tokens": 50,
        })
        parsed = json.loads(result["messages"][0]["content"])
        assert parsed["tool"] == "get_weather"
        assert parsed["input"] == {"city": "NYC"}

    def test_model_mapping_applied(self):
        result = _anthropic_to_openai({
            "model": "claude-opus-4-5-20250514",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50,
        })
        assert result["model"] == "claude-opus-4.5"

    def test_defaults(self):
        result = _anthropic_to_openai({
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert result["max_tokens"] == 4096
        assert result["temperature"] == 1
        assert result["stream"] is False

    def test_empty_system_ignored(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "system": "",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50,
        })
        assert result["messages"][0]["role"] == "user"

    def test_multi_turn(self):
        result = _anthropic_to_openai({
            "model": "claude-sonnet-4",
            "messages": [
                {"role": "user", "content": "I'm Dave"},
                {"role": "assistant", "content": "Hi Dave!"},
                {"role": "user", "content": "What's my name?"},
            ],
            "max_tokens": 50,
        })
        assert len(result["messages"]) == 3
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"
        assert result["messages"][2]["role"] == "user"


class TestOpenAIToAnthropic:
    def test_basic_response(self):
        result = _openai_to_anthropic({
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }, "claude-sonnet-4")

        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["content"] == [{"type": "text", "text": "Hello!"}]
        assert result["model"] == "claude-sonnet-4"
        assert result["stop_reason"] == "end_turn"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 5

    def test_length_stop_reason(self):
        result = _openai_to_anthropic({
            "choices": [{"message": {"content": "cut"}, "finish_reason": "length"}],
            "usage": {},
        }, "claude-sonnet-4")
        assert result["stop_reason"] == "max_tokens"

    def test_empty_response(self):
        result = _openai_to_anthropic({
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {},
        }, "claude-sonnet-4")
        assert result["content"] == []

    def test_missing_usage(self):
        result = _openai_to_anthropic({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        }, "claude-sonnet-4")
        assert result["usage"]["input_tokens"] == 0
        assert result["usage"]["output_tokens"] == 0

    def test_unknown_finish_reason_defaults(self):
        result = _openai_to_anthropic({
            "choices": [{"message": {"content": "x"}, "finish_reason": "content_filter"}],
            "usage": {},
        }, "claude-sonnet-4")
        assert result["stop_reason"] == "end_turn"

    def test_id_format(self):
        result = _openai_to_anthropic({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }, "claude-sonnet-4")
        assert result["id"].startswith("msg_")
        assert len(result["id"]) == 28  # msg_ + 24 hex


class TestSSELine:
    def test_format(self):
        line = _sse_line("content_block_delta", {"type": "content_block_delta", "index": 0})
        decoded = line.decode()
        assert decoded.startswith("event: content_block_delta\n")
        assert "data: " in decoded
        assert decoded.endswith("\n\n")

    def test_json_valid(self):
        line = _sse_line("ping", {"type": "ping"})
        data_part = line.decode().split("data: ")[1].strip()
        parsed = json.loads(data_part)
        assert parsed["type"] == "ping"


# ═══════════════════════════════════════════════════════════════════════
# Integration Tests — require proxy running on localhost:8080
# ═══════════════════════════════════════════════════════════════════════

PROXY_URL = os.environ.get("PROXY_URL", "http://localhost:8080")


def _proxy_available():
    try:
        req = urllib.request.Request(f"{PROXY_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _post_messages(body: dict, timeout: int = 30) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{PROXY_URL}/v1/messages",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


@pytest.mark.skipif(not _proxy_available(), reason="Proxy not running on localhost:8080")
class TestIntegrationMessages:
    """These tests hit the live proxy and make real Copilot API calls."""

    def test_health(self):
        req = urllib.request.Request(f"{PROXY_URL}/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_models(self):
        req = urllib.request.Request(f"{PROXY_URL}/v1/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        assert data["object"] == "list"
        assert len(data["data"]) > 0
        ids = [m["id"] for m in data["data"]]
        assert "claude-sonnet-4" in ids

    def test_simple_message(self):
        resp = _post_messages({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "Reply with exactly one word: hello"}],
            "max_tokens": 10,
        })
        assert resp["type"] == "message"
        assert resp["role"] == "assistant"
        assert len(resp["content"]) > 0
        assert resp["content"][0]["type"] == "text"
        assert resp["stop_reason"] in ("end_turn", "max_tokens")

    def test_system_prompt(self):
        resp = _post_messages({
            "model": "claude-sonnet-4",
            "system": "You must respond with exactly the word 'pong' and nothing else",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 10,
        })
        assert "pong" in resp["content"][0]["text"].lower()

    def test_streaming(self):
        data = json.dumps({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "Say: ok"}],
            "max_tokens": 5,
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            f"{PROXY_URL}/v1/messages",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()

        events = [l for l in body.split("\n") if l.startswith("event: ")]
        event_types = [e.split("event: ")[1] for e in events]

        assert "message_start" in event_types
        assert "content_block_start" in event_types
        assert "content_block_delta" in event_types
        assert "message_stop" in event_types

    def test_404(self):
        req = urllib.request.Request(
            f"{PROXY_URL}/v1/nonexistent",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 404

    def test_post_streaming_not_hung(self):
        """After a streaming request, the server should still respond."""
        # First: streaming request
        data = json.dumps({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "Say: test"}],
            "max_tokens": 5,
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            f"{PROXY_URL}/v1/messages",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()

        # Then: health check must respond fast
        health_req = urllib.request.Request(f"{PROXY_URL}/health")
        with urllib.request.urlopen(health_req, timeout=3) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
