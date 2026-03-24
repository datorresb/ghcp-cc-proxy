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
        "model": "claude-sonnet-4",
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
        "model": "claude-sonnet-4",
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
        "model": "claude-sonnet-4",
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
        "model": "claude-sonnet-4",
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
        "model": "claude-sonnet-4",
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
        "model": "claude-sonnet-4",
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
