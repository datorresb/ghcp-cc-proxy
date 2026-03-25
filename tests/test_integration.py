"""
Integration tests for the GitHub Copilot → Anthropic proxy.

These tests start the actual proxy server and make real API calls through it.
Requires `gh auth token` to succeed (GitHub Copilot access).

Run with:
    python3 -m pytest tests/test_integration.py -v
    python3 -m pytest tests/test_integration.py -v -m integration  # only real API tests
"""

import base64
import json
import os
import signal
import socket
import subprocess
import sys
import time

import pytest

# ── Skip all tests if gh auth is unavailable ─────────────────────────

_gh_check = subprocess.run(
    ["gh", "auth", "token"], capture_output=True, text=True
)
if _gh_check.returncode != 0:
    pytestmark = pytest.mark.skip(reason="gh auth token failed — no Copilot access")
else:
    pytestmark = []


def _find_free_port() -> int:
    """Find and return a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_request(url: str, method: str = "GET", data: bytes | None = None,
                  headers: dict | None = None, timeout: int = 30) -> tuple[int, dict | bytes]:
    """Make an HTTP request using only stdlib. Returns (status_code, parsed_json_or_bytes)."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    req = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            if "json" in content_type:
                return resp.status, json.loads(body)
            return resp.status, body
    except HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return exc.code, body


def _read_sse_events(url: str, data: bytes, headers: dict,
                     timeout: int = 60) -> list[tuple[str, dict]]:
    """Read SSE events from a streaming response. Returns list of (event_type, data_dict)."""
    from urllib.request import Request, urlopen

    req = Request(url, data=data, headers=headers, method="POST")
    events = []
    with urlopen(req, timeout=timeout) as resp:
        current_event = None
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    data_obj = json.loads(payload)
                    events.append((current_event or "message", data_obj))
                except json.JSONDecodeError:
                    pass
            elif line == "":
                current_event = None
    return events


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def proxy_url():
    """Start the proxy server on a random port and return its base URL.

    Waits up to 10 seconds for the health endpoint to respond.
    Kills the proxy process on teardown.
    """
    if _gh_check.returncode != 0:
        pytest.skip("gh auth token failed — no Copilot access")

    port = _find_free_port()
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["HOST"] = "127.0.0.1"
    env["LOG_LEVEL"] = "DEBUG"

    proxy_script = os.path.join(os.path.dirname(__file__), "..", "copilot_proxy.py")
    proc = subprocess.Popen(
        [sys.executable, proxy_script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    base_url = f"http://127.0.0.1:{port}"

    # Wait for the proxy to become ready
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            status, _ = _http_request(f"{base_url}/health", timeout=2)
            if status == 200:
                break
        except Exception:
            pass
        # Check if process died
        if proc.poll() is not None:
            stdout = proc.stdout.read().decode() if proc.stdout else ""
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            pytest.fail(f"Proxy exited early (code {proc.returncode}).\n"
                        f"stdout: {stdout}\nstderr: {stderr}")
        time.sleep(0.3)
    else:
        proc.kill()
        proc.wait()
        pytest.fail("Proxy did not become ready within 10 seconds")

    yield base_url

    # Teardown
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ── Tests ────────────────────────────────────────────────────────────

def test_health_endpoint(proxy_url):
    """GET /health returns 200 with status ok."""
    status, body = _http_request(f"{proxy_url}/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_models_endpoint(proxy_url):
    """GET /v1/models returns 200 with a list of models."""
    status, body = _http_request(f"{proxy_url}/v1/models", timeout=30)
    assert status == 200
    assert "data" in body
    assert isinstance(body["data"], list)
    assert len(body["data"]) > 0


@pytest.mark.integration
def test_simple_message(proxy_url):
    """POST /v1/messages with a simple prompt returns a valid Anthropic response."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Say hello"}],
    }).encode()
    headers = {"Content-Type": "application/json"}

    status, body = _http_request(
        f"{proxy_url}/v1/messages", method="POST",
        data=payload, headers=headers, timeout=30,
    )

    assert status == 200
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert isinstance(body["content"], list)
    assert len(body["content"]) > 0
    assert body["content"][-1]["type"] == "text"
    assert len(body["content"][-1]["text"]) > 0
    assert body["stop_reason"] in ("end_turn", "max_tokens")
    assert "usage" in body
    assert body["usage"]["output_tokens"] > 0


@pytest.mark.integration
def test_streaming_message(proxy_url):
    """POST /v1/messages with stream=true returns correct SSE event sequence."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "stream": True,
        "messages": [{"role": "user", "content": "Say hello in one word"}],
    }).encode()
    headers = {"Content-Type": "application/json"}

    events = _read_sse_events(
        f"{proxy_url}/v1/messages",
        data=payload, headers=headers, timeout=60,
    )

    event_types = [e[0] for e in events]
    data_types = [e[1].get("type") for e in events]

    # Verify required event types appear in order
    assert "message_start" in event_types
    assert "content_block_start" in event_types
    assert "content_block_delta" in event_types
    assert "content_block_stop" in event_types
    assert "message_delta" in event_types
    assert "message_stop" in event_types

    # Verify message_start is first real event (ping may interleave)
    non_ping = [(et, dt) for et, dt in zip(event_types, data_types) if dt != "ping"]
    assert non_ping[0] == ("message_start", "message_start")

    # Verify we got at least one text_delta
    deltas = [e[1] for e in events if e[1].get("type") == "content_block_delta"]
    text_deltas = [d for d in deltas if d.get("delta", {}).get("type") == "text_delta"]
    assert len(text_deltas) > 0
    # Verify text content is non-empty
    full_text = "".join(d["delta"]["text"] for d in text_deltas)
    assert len(full_text) > 0


@pytest.mark.integration
def test_thinking(proxy_url):
    """POST /v1/messages with thinking enabled returns a valid response.

    The response may or may not include thinking content depending on model
    support, but the request should succeed and contain text output.
    """
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "thinking": {"type": "enabled", "budget_tokens": 5000},
        "messages": [{"role": "user", "content": "What is 17 * 23?"}],
    }).encode()
    headers = {"Content-Type": "application/json"}

    status, body = _http_request(
        f"{proxy_url}/v1/messages", method="POST",
        data=payload, headers=headers, timeout=60,
    )

    assert status == 200
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert isinstance(body["content"], list)
    assert len(body["content"]) > 0

    # At least one text block should exist
    text_blocks = [b for b in body["content"] if b["type"] == "text"]
    assert len(text_blocks) > 0
    assert len(text_blocks[0]["text"]) > 0

    # If thinking is present, verify its structure
    thinking_blocks = [b for b in body["content"] if b["type"] == "thinking"]
    if thinking_blocks:
        assert "thinking" in thinking_blocks[0]


@pytest.mark.integration
def test_tool_use(proxy_url):
    """POST /v1/messages asking to use a tool returns a valid response format."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": "What is 2+2? Use the calculator tool."}],
    }).encode()
    headers = {"Content-Type": "application/json"}

    status, body = _http_request(
        f"{proxy_url}/v1/messages", method="POST",
        data=payload, headers=headers, timeout=30,
    )

    assert status == 200
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert isinstance(body["content"], list)
    assert len(body["content"]) > 0
    # The response should have at least a text block (tool use is best-effort)
    assert any(b["type"] in ("text", "tool_use") for b in body["content"])


@pytest.mark.integration
def test_image_message(proxy_url):
    """POST /v1/messages with a base64-encoded 1x1 PNG image succeeds.

    The proxy should correctly convert the image to OpenAI format.
    The model may not parse the image meaningfully but should not error.
    """
    # Minimal 1x1 red PNG (67 bytes)
    png_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    b64_image = base64.b64encode(png_1x1).decode()

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64_image,
                    },
                },
                {
                    "type": "text",
                    "text": "What do you see in this image?",
                },
            ],
        }],
    }).encode()
    headers = {"Content-Type": "application/json"}

    status, body = _http_request(
        f"{proxy_url}/v1/messages", method="POST",
        data=payload, headers=headers, timeout=30,
    )

    # Should succeed (200) or possibly get an upstream error for invalid image,
    # but the proxy itself should not crash
    assert status in (200, 400, 422)
    if status == 200:
        assert body["type"] == "message"
        assert isinstance(body["content"], list)


def test_count_tokens(proxy_url):
    """POST /v1/messages/count_tokens returns input_tokens > 0."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "Hello, world!"}],
    }).encode()
    headers = {"Content-Type": "application/json"}

    status, body = _http_request(
        f"{proxy_url}/v1/messages/count_tokens", method="POST",
        data=payload, headers=headers, timeout=10,
    )

    assert status == 200
    assert "input_tokens" in body
    assert isinstance(body["input_tokens"], int)
    assert body["input_tokens"] > 0


# ── Dashboard / API Tests ────────────────────────────────────────────

def test_dashboard_page(proxy_url):
    """GET / returns 200 with HTML dashboard."""
    status, body = _http_request(f"{proxy_url}/")
    assert status == 200
    assert b"Copilot Proxy" in body


def test_api_auth_status(proxy_url):
    """GET /api/auth-status returns authenticated status."""
    status, body = _http_request(f"{proxy_url}/api/auth-status")
    assert status == 200
    assert "authenticated" in body
    assert isinstance(body["authenticated"], bool)


def test_api_config_get(proxy_url):
    """GET /api/config returns model config with available_targets."""
    status, body = _http_request(f"{proxy_url}/api/config")
    assert status == 200
    assert "model_map" in body
    assert "available_targets" in body
    assert "overrides" in body
    assert isinstance(body["available_targets"], list)


def test_api_config_post_valid(proxy_url):
    """POST /api/config with valid override succeeds."""
    payload = json.dumps({"overrides": {"main": "gpt-5.4"}}).encode()
    headers = {"Content-Type": "application/json"}
    status, body = _http_request(
        f"{proxy_url}/api/config", method="POST",
        data=payload, headers=headers,
    )
    assert status == 200
    assert body.get("status") == "ok"

    # Verify the override is reflected in config
    status, cfg = _http_request(f"{proxy_url}/api/config")
    assert cfg["overrides"].get("main") == "gpt-5.4"

    # Reset override
    reset = json.dumps({"overrides": {}}).encode()
    _http_request(f"{proxy_url}/api/config", method="POST",
                  data=reset, headers=headers)


def test_api_config_post_invalid_target(proxy_url):
    """POST /api/config with invalid target returns 400."""
    payload = json.dumps({"overrides": {"main": "nonexistent-model"}}).encode()
    headers = {"Content-Type": "application/json"}
    status, body = _http_request(
        f"{proxy_url}/api/config", method="POST",
        data=payload, headers=headers,
    )
    assert status == 400
    assert "error" in body


def test_api_config_post_invalid_slot(proxy_url):
    """POST /api/config with invalid slot returns 400."""
    payload = json.dumps({"overrides": {"invalid": "gpt-5.4"}}).encode()
    headers = {"Content-Type": "application/json"}
    status, body = _http_request(
        f"{proxy_url}/api/config", method="POST",
        data=payload, headers=headers,
    )
    assert status == 400


def test_api_stats(proxy_url):
    """GET /api/stats returns usage stats."""
    status, body = _http_request(f"{proxy_url}/api/stats")
    assert status == 200
    assert "total_requests" in body
    assert "total_tokens" in body
    assert "by_model" in body
    assert "uptime_seconds" in body
    assert "request_log" in body
    assert isinstance(body["request_log"], list)


@pytest.mark.integration
def test_stats_increment_after_request(proxy_url):
    """Stats increment after sending a message."""
    # Get baseline
    _, before = _http_request(f"{proxy_url}/api/stats")
    baseline_reqs = before["total_requests"]

    # Send a message
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "Hi"}],
    }).encode()
    headers = {"Content-Type": "application/json"}
    _http_request(f"{proxy_url}/v1/messages", method="POST",
                  data=payload, headers=headers, timeout=30)

    # Check stats incremented
    _, after = _http_request(f"{proxy_url}/api/stats")
    assert after["total_requests"] > baseline_reqs
    assert len(after["request_log"]) > 0
    last = after["request_log"][-1]
    assert "requested_model" in last
    assert "sent_model" in last


# ── Tool Support Tests ───────────────────────────────────────────────

@pytest.mark.integration
def test_tool_use_request_with_tools(proxy_url):
    """POST /v1/messages with tools definitions returns a valid response."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather for a city",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"}
                    },
                    "required": ["city"],
                },
            }
        ],
        "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
    }).encode()
    headers = {"Content-Type": "application/json"}

    status, body = _http_request(
        f"{proxy_url}/v1/messages", method="POST",
        data=payload, headers=headers, timeout=60,
    )

    assert status == 200
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert isinstance(body["content"], list)
    assert len(body["content"]) > 0
    # Response should have either text or tool_use (model may or may not call the tool)
    types = {b["type"] for b in body["content"]}
    assert types & {"text", "tool_use"}
    # If tool_use, verify structure
    tool_uses = [b for b in body["content"] if b["type"] == "tool_use"]
    for tu in tool_uses:
        assert "id" in tu
        assert "name" in tu
        assert "input" in tu
        assert isinstance(tu["input"], dict)
    if tool_uses:
        assert body["stop_reason"] == "tool_use"


@pytest.mark.integration
def test_tool_result_in_history(proxy_url):
    """POST /v1/messages with tool_result in history succeeds."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 200,
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
        "messages": [
            {"role": "user", "content": "What is the weather in Paris?"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_test123", "name": "get_weather", "input": {"city": "Paris"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_test123", "content": "Sunny, 22°C"},
            ]},
        ],
    }).encode()
    headers = {"Content-Type": "application/json"}

    status, body = _http_request(
        f"{proxy_url}/v1/messages", method="POST",
        data=payload, headers=headers, timeout=30,
    )

    assert status == 200
    assert body["type"] == "message"
    # Model should respond with text summarizing the weather
    text_blocks = [b for b in body["content"] if b["type"] == "text"]
    assert len(text_blocks) > 0


@pytest.mark.integration
@pytest.mark.slow
def test_e2e_claude_cli_tool_execution(proxy_url):
    """E2E smoke test: Claude Code CLI writes a file via proxy tools."""
    import subprocess as sp
    import os

    test_file = "/workspaces/ghcp-cc-proxy/test-e2e-smoke.txt"
    # Clean up if exists
    if os.path.exists(test_file):
        os.remove(test_file)

    result = sp.run(
        ["claude", "-p", "--dangerously-skip-permissions",
         f"create a file at {test_file} containing exactly: e2e-smoke-test-ok"],
        env={**os.environ, "ANTHROPIC_BASE_URL": proxy_url, "ANTHROPIC_AUTH_TOKEN": "sk-test"},
        capture_output=True, text=True, timeout=180,
    )

    assert os.path.exists(test_file), f"File not created. stdout: {result.stdout}, stderr: {result.stderr}"
    content = open(test_file).read().strip()
    assert "e2e-smoke-test-ok" in content

    # Clean up
    os.remove(test_file)
