#!/usr/bin/env python3
"""
GitHub Copilot → Anthropic API Proxy for Claude Code.

Translates Anthropic Messages API requests into OpenAI chat/completions
requests that GitHub Copilot understands, then translates responses back.

Usage:
    python copilot_proxy.py              # port 8080 (default)
    PORT=3000 python copilot_proxy.py

Requires:
    - gh CLI authenticated: `gh auth login`
    - GitHub Copilot subscription
"""

import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("copilot-proxy")

PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "127.0.0.1")
MAX_BODY = 10 * 1024 * 1024  # 10 MB
TOKEN_TTL = 600  # refresh every 10 min

_lock = threading.Lock()
_cache: dict = {"token": None, "endpoint": None, "expires": 0}

# ── Model mapping ────────────────────────────────────────────────────

_HARDCODED_MODEL_MAP = {
    "claude-opus-4-6":          "claude-opus-4.6",
    "claude-opus-4-6-1m":       "claude-opus-4.6-1m",
    "claude-opus-4-6[1m]":      "claude-opus-4.6-1m",
    "claude-sonnet-4-6":        "claude-sonnet-4.6",
    "claude-haiku-4-5":         "claude-haiku-4.5",
    "opus":                     "claude-opus-4.6",
    "opus[1m]":                 "claude-opus-4.6-1m",
    "sonnet":                   "claude-sonnet-4.6",
    "haiku":                    "claude-haiku-4.5",
}

_models_cache = {"data": None, "expires": 0}
MODELS_CACHE_TTL = int(os.environ.get("MODELS_CACHE_TTL", "300"))


def _load_models():
    """Load model mappings from models.json, falling back to hardcoded map."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")
    try:
        with open(path) as f:
            data = json.load(f)
        model_map = data.get("model_map", _HARDCODED_MODEL_MAP)
        logger.info("Loaded %d model mappings from models.json", len(model_map))
        return model_map
    except FileNotFoundError:
        logger.warning("models.json not found, using hardcoded model map")
        return dict(_HARDCODED_MODEL_MAP)
    except json.JSONDecodeError:
        logger.warning("models.json is invalid JSON, using hardcoded model map")
        return dict(_HARDCODED_MODEL_MAP)


MODEL_MAP = _load_models()


def _map_model(model: str) -> str:
    if model in MODEL_MAP:
        return MODEL_MAP[model]
    for prefix, mapped in MODEL_MAP.items():
        if model.startswith(prefix):
            return mapped
    return model  # pass-through for unknown models


# ── Token management ────────────────────────────────────────────────

def _fetch_token() -> dict:
    gh_token = subprocess.check_output(
        ["gh", "auth", "token", "-h", "github.com"], text=True
    ).strip()
    req = Request(
        "https://api.github.com/copilot_internal/v2/token",
        headers={
            "Authorization": f"token {gh_token}",
            "Editor-Version": "vscode/1.96.0",
            "Editor-Plugin-Version": "copilot-chat/0.40.0",
        },
    )
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return {
        "token": data["token"],
        "endpoint": data.get("endpoints", {}).get(
            "api", "https://api.githubcopilot.com"
        ),
        "expires": time.time() + TOKEN_TTL,
    }


def _get_token():
    with _lock:
        if _cache["token"] is None or time.time() >= _cache["expires"]:
            _cache.update(_fetch_token())
        return _cache["token"], _cache["endpoint"]


def _machine_id() -> str:
    try:
        mac = hex(uuid.getnode())[2:]
        return hashlib.sha256(mac.encode()).hexdigest()[:32]
    except Exception:
        return uuid.uuid4().hex[:32]


MACHINE_ID = _machine_id()


# ── Anthropic ↔ OpenAI conversion ───────────────────────────────────

def _anthropic_to_openai(body: dict) -> dict:
    """Convert Anthropic Messages API request → OpenAI chat/completions."""
    messages = []

    # System prompt — Anthropic allows string or list of content blocks
    system = body.get("system")
    if system:
        if isinstance(system, list):
            text = "\n".join(
                b.get("text", "") for b in system
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            text = str(system)
        if text:
            messages.append({"role": "system", "content": text})

    # Messages
    for msg in body.get("messages", []):
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            text_parts = []
            image_parts = []
            has_images = False
            for block in content:
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_result":
                    # Flatten tool results to text
                    tc = block.get("content", "")
                    if isinstance(tc, list):
                        tc = "\n".join(
                            b.get("text", "") for b in tc if b.get("type") == "text"
                        )
                    text_parts.append(tc)
                elif btype == "tool_use":
                    text_parts.append(json.dumps({"tool": block.get("name"), "input": block.get("input")}))
                elif btype == "image":
                    has_images = True
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        data = source.get("data", "")
                        image_parts.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}})
                    elif source.get("type") == "url":
                        image_parts.append({"type": "image_url", "image_url": {"url": source.get("url", "")}})
            if has_images:
                # Use multimodal array format
                multimodal = []
                combined_text = "\n".join(p for p in text_parts if p)
                if combined_text:
                    multimodal.append({"type": "text", "text": combined_text})
                multimodal.extend(image_parts)
                if multimodal:
                    messages.append({"role": role, "content": multimodal})
            else:
                text = "\n".join(p for p in text_parts if p)
                if text:
                    messages.append({"role": role, "content": text})
        else:
            messages.append({"role": role, "content": str(content)})

    result = {
        "model": _map_model(body.get("model", "claude-sonnet-4")),
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 1),
        "stream": body.get("stream", False),
    }

    # Map Anthropic thinking parameter to OpenAI reasoning_effort
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = thinking.get("budget_tokens", 0)
        if budget <= 2048:
            result["reasoning_effort"] = "low"
        elif budget <= 16384:
            result["reasoning_effort"] = "medium"
        else:
            result["reasoning_effort"] = "high"

    return result


def _openai_to_anthropic(oai: dict, model: str) -> dict:
    """Convert OpenAI chat/completions response → Anthropic Messages API."""
    choice = (oai.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content", "")
    reasoning_text = message.get("reasoning_content", "")
    finish = choice.get("finish_reason", "stop")

    stop_map = {"stop": "end_turn", "length": "max_tokens"}
    usage = oai.get("usage", {})

    content = []
    if reasoning_text:
        content.append({"type": "thinking", "thinking": reasoning_text})
    if text:
        content.append({"type": "text", "text": text})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": stop_map.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── Streaming helpers ────────────────────────────────────────────────

def _sse_line(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _stream_anthropic(wfile, model: str, text: str, input_tokens: int, output_tokens: int):
    """Write Anthropic SSE stream for a completed response."""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    # message_start
    wfile.write(_sse_line("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "content": [], "model": model,
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    }))

    # content_block_start
    wfile.write(_sse_line("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    }))

    # ping
    wfile.write(_sse_line("ping", {"type": "ping"}))

    # content_block_delta — stream in chunks
    chunk_size = 64
    for i in range(0, max(len(text), 1), chunk_size):
        chunk = text[i:i + chunk_size]
        wfile.write(_sse_line("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": chunk},
        }))
        wfile.flush()

    # content_block_stop
    wfile.write(_sse_line("content_block_stop", {
        "type": "content_block_stop", "index": 0,
    }))

    # message_delta
    wfile.write(_sse_line("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }))

    # message_stop
    wfile.write(_sse_line("message_stop", {"type": "message_stop"}))


def _stream_from_copilot_sse(upstream_resp, wfile, model: str):
    """Read OpenAI SSE stream from Copilot and re-emit as Anthropic SSE."""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    # message_start
    wfile.write(_sse_line("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "content": [], "model": model,
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }))
    wfile.write(_sse_line("ping", {"type": "ping"}))
    wfile.flush()

    output_tokens = 0
    thinking_started = False
    thinking_ended = False
    text_started = False
    content_index = 0

    for raw_line in upstream_resp:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        delta = (chunk.get("choices") or [{}])[0].get("delta", {})

        reasoning_text = delta.get("reasoning_content", "")
        if reasoning_text:
            if not thinking_started:
                wfile.write(_sse_line("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                }))
                thinking_started = True
            output_tokens += max(1, len(reasoning_text) // 4)
            wfile.write(_sse_line("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "thinking_delta", "thinking": reasoning_text},
            }))
            wfile.flush()

        text = delta.get("content", "")
        if text:
            if thinking_started and not thinking_ended:
                wfile.write(_sse_line("content_block_stop", {
                    "type": "content_block_stop", "index": 0,
                }))
                thinking_ended = True
                content_index = 1
            if not text_started:
                wfile.write(_sse_line("content_block_start", {
                    "type": "content_block_start", "index": content_index,
                    "content_block": {"type": "text", "text": ""},
                }))
                text_started = True
            output_tokens += max(1, len(text) // 4)
            wfile.write(_sse_line("content_block_delta", {
                "type": "content_block_delta", "index": content_index,
                "delta": {"type": "text_delta", "text": text},
            }))
            wfile.flush()

    # Finalize
    if thinking_started and not thinking_ended:
        wfile.write(_sse_line("content_block_stop", {
            "type": "content_block_stop", "index": 0,
        }))
        content_index = 1
    if not text_started:
        wfile.write(_sse_line("content_block_start", {
            "type": "content_block_start", "index": content_index,
            "content_block": {"type": "text", "text": ""},
        }))
    wfile.write(_sse_line("content_block_stop", {
        "type": "content_block_stop", "index": content_index,
    }))
    wfile.write(_sse_line("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }))
    wfile.write(_sse_line("message_stop", {"type": "message_stop"}))
    wfile.flush()


# ── Upstream request with retries ────────────────────────────────────


def _make_upstream_request(url, headers, body, timeout=300, max_retries=3):
    """Make an upstream POST request with retries and exponential backoff."""
    last_exc = None
    auth_retried = False
    attempt = 0
    while attempt < max_retries:
        try:
            req = Request(url, data=body, headers=headers, method="POST")
            return urlopen(req, timeout=timeout)
        except HTTPError as exc:
            last_exc = exc
            status = exc.code
            try:
                exc.read()
            except Exception:
                pass
            if status in {400, 403, 404}:
                raise
            if status == 401 and not auth_retried:
                auth_retried = True
                logger.warning("Retry %d/%d after 401 error", attempt + 1, max_retries)
                with _lock:
                    _cache["expires"] = 0
                token, _ = _get_token()
                headers = dict(headers)
                headers["Authorization"] = f"Bearer {token}"
                attempt += 1
                continue
            if status in {429, 502, 503, 504} and attempt < max_retries - 1:
                delay = min(2 ** attempt + random.random(), 30)
                logger.warning("Retry %d/%d after %d error", attempt + 1, max_retries, status)
                time.sleep(delay)
                attempt += 1
                continue
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                delay = min(2 ** attempt + random.random(), 30)
                logger.warning("Retry %d/%d after %s error", attempt + 1, max_retries, type(exc).__name__)
                time.sleep(delay)
                attempt += 1
                continue
            raise
    raise last_exc


# ── HTTP handler ─────────────────────────────────────────────────────

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            now = time.time()
            if _models_cache["data"] is not None and now < _models_cache["expires"]:
                self._reply(200, _models_cache["data"])
                return
            try:
                token, endpoint = _get_token()
                req = Request(
                    f"{endpoint}/models",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Editor-Version": "vscode/1.96.0",
                        "Copilot-Integration-Id": "vscode-chat",
                    },
                )
                with urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                _models_cache["data"] = data
                _models_cache["expires"] = now + MODELS_CACHE_TTL
                self._reply(200, data)
            except Exception:
                fallback = {
                    "object": "list",
                    "data": [
                        {"id": k, "object": "model", "owned_by": "anthropic"}
                        for k in MODEL_MAP
                    ],
                }
                self._reply(200, fallback)
            return
        if self.path == "/health":
            self._reply(200, {"status": "ok"})
            return
        self.send_response(302)
        self.send_header("Location", "https://github.com/features/copilot")
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/messages":
            self._handle_messages()
        elif path == "/v1/messages/count_tokens":
            self._handle_count_tokens()
        elif path == "/v1/chat/completions":
            self._handle_chat_completions()
        else:
            self._reply(404, {"error": "not found"})

    def _handle_count_tokens(self):
        """Approximate token count for Anthropic count_tokens endpoint."""
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        total_chars = 0
        system = body.get("system", "")
        if isinstance(system, list):
            total_chars += sum(len(b.get("text", "")) for b in system if isinstance(b, dict))
        elif system:
            total_chars += len(str(system))
        for msg in body.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        total_chars += len(block.get("text", ""))
        self._reply(200, {"input_tokens": max(1, total_chars // 4)})

    def _handle_messages(self):
        """Anthropic Messages API → Copilot → Anthropic response."""
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            self._reply(413, {
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "Request body too large"},
            })
            return
        body = json.loads(self.rfile.read(length))
        original_model = body.get("model", "claude-sonnet-4")
        stream = body.get("stream", False)

        openai_body = _anthropic_to_openai(body)

        try:
            token, endpoint = _get_token()
        except Exception as exc:
            self._reply(502, {
                "type": "error",
                "error": {"type": "api_error", "message": f"token error: {exc}"},
            })
            return

        if stream:
            openai_body["stream"] = True

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Editor-Version": "vscode/1.96.0",
            "Editor-Plugin-Version": "copilot-chat/0.40.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Openai-Organization": "github-copilot",
            "Openai-Intent": "conversation-agent",
            "X-Request-Id": str(uuid.uuid4()),
            "Machine-Id": MACHINE_ID,
        }

        try:
            resp = _make_upstream_request(
                f"{endpoint}/chat/completions",
                headers,
                json.dumps(openai_body).encode(),
            )

            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                _stream_from_copilot_sse(resp, self.wfile, original_model)
                self.wfile.flush()
            else:
                oai_data = json.loads(resp.read())
                anthropic_resp = _openai_to_anthropic(oai_data, original_model)
                self._reply(200, anthropic_resp)

        except HTTPError as exc:
            status = exc.code
            logger.error("Upstream error %d", status)
            self._reply(status, {
                "type": "error",
                "error": {"type": "api_error", "message": f"Upstream error (HTTP {status})"},
            })
        except Exception as exc:
            logger.error("Internal error: %s", exc)
            self._reply(500, {
                "type": "error",
                "error": {"type": "api_error", "message": "Internal proxy error"},
            })

    def _handle_chat_completions(self):
        """OpenAI-compatible pass-through (for Cursor, etc.)."""
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            token, endpoint = _get_token()
        except Exception as exc:
            self._reply(502, {"error": f"token error: {exc}"})
            return

        upstream = Request(
            f"{endpoint}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Editor-Version": "vscode/1.96.0",
                "Copilot-Integration-Id": "vscode-chat",
            },
            method="POST",
        )

        try:
            with urlopen(upstream, timeout=300) as r:
                resp_body = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(resp_body)
        except HTTPError as exc:
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(exc.read())

    def _reply(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)


# ── main ─────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        _get_token()
        logger.info("Copilot token acquired")
    except Exception as exc:
        logger.error("FATAL: cannot get Copilot token — %s", exc)
        logger.error("Run: gh auth login -h github.com -p https -w")
        sys.exit(1)

    logger.info("Listening on http://%s:%d", HOST, PORT)
    logger.info("Anthropic API:  POST http://localhost:%d/v1/messages", PORT)
    logger.info("OpenAI API:     POST http://localhost:%d/v1/chat/completions", PORT)
    logger.info("Models:         GET  http://localhost:%d/v1/models", PORT)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
    server.server_close()


if __name__ == "__main__":
    main()
