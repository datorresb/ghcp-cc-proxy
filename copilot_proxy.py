#!/usr/bin/env python3
"""
GitHub Copilot → Anthropic API Proxy for Claude Code.

Translates Anthropic Messages API requests into OpenAI chat/completions
requests that GitHub Copilot understands, then translates responses back.

Usage:
    python copilot_proxy.py              # port 4141 (default)
    PORT=3000 python copilot_proxy.py

Requires:
    - gh CLI authenticated: `gh auth login`
    - GitHub Copilot subscription
"""

import collections
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

PORT = int(os.environ.get("PORT", "4141"))
HOST = os.environ.get("HOST", "127.0.0.1")
MAX_BODY = 10 * 1024 * 1024  # 10 MB
TOKEN_TTL = 600  # refresh every 10 min

_lock = threading.Lock()
_models_lock = threading.Lock()
_request_semaphore = threading.Semaphore(int(os.environ.get("MAX_CONCURRENT", "32")))
_cache: dict = {"token": None, "endpoint": None, "expires": 0}

# ── Model mapping & config ────────────────────────────────────────────

_models_cache = {"data": None, "expires": 0}
MODELS_CACHE_TTL = int(os.environ.get("MODELS_CACHE_TTL", "300"))

_config_lock = threading.Lock()
_config: dict = {"model_map": {}, "available_targets": [], "overrides": {}}
_MODELS_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")


def _load_models():
    """Load model config from models.json."""
    try:
        with open(_MODELS_JSON_PATH) as f:
            data = json.load(f)
        model_map = data.get("model_map", {})
        if not model_map:
            logger.error("models.json has no 'model_map' key or it is empty")
            sys.exit(1)
        logger.info("Loaded %d model mappings from models.json", len(model_map))
        return {
            "model_map": model_map,
            "available_targets": data.get("available_targets", []),
            "overrides": data.get("overrides", {}),
        }
    except FileNotFoundError:
        logger.error("models.json not found — create it next to copilot_proxy.py")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error("models.json is invalid JSON: %s", e)
        sys.exit(1)


_config = _load_models()


def _save_models():
    """Persist current config to models.json."""
    with _config_lock:
        payload = json.dumps({
            "model_map": dict(_config["model_map"]),
            "available_targets": list(_config["available_targets"]),
            "overrides": dict(_config["overrides"]),
        }, indent=2) + "\n"
    with open(_MODELS_JSON_PATH, "w") as f:
        f.write(payload)


def _get_model_slot(model: str) -> str:
    """Determine which slot (main/fast) an incoming model belongs to."""
    if "haiku" in model:
        return "fast"
    return "main"


def _model_vendor(model: str) -> str:
    """Infer vendor from model name."""
    if "gpt" in model:
        return "openai"
    if "gemini" in model:
        return "google"
    return "anthropic"


def _map_model(model: str) -> str:
    with _config_lock:
        overrides = dict(_config.get("overrides", {}))
        model_map = dict(_config["model_map"])

    # Check override for this model's slot
    slot = _get_model_slot(model)
    if slot in overrides and overrides[slot]:
        mapped = overrides[slot]
        logger.debug("Model override (%s slot): %s → %s", slot, model, mapped)
        return mapped

    # Fall back to model_map
    if model in model_map:
        mapped = model_map[model]
        logger.debug("Model mapping: %s → %s", model, mapped)
        return mapped
    for prefix, mapped in model_map.items():
        if model.startswith(prefix):
            logger.debug("Model mapping (prefix): %s → %s", model, mapped)
            return mapped
    logger.warning("No model mapping found for '%s', passing through as-is", model)
    return model


# ── Usage tracking ───────────────────────────────────────────────────

_usage_lock = threading.Lock()
_usage: dict = {"total_requests": 0, "total_tokens": 0, "by_model": {}, "started": time.time()}
_request_log: collections.deque = collections.deque(maxlen=50)


def _track_usage(requested_model: str, sent_model: str, input_tokens: int, output_tokens: int, tools_count: int = 0):
    """Record a completed request in usage stats and request log."""
    total = input_tokens + output_tokens
    now = time.time()
    with _usage_lock:
        _usage["total_requests"] += 1
        _usage["total_tokens"] += total
        if sent_model not in _usage["by_model"]:
            _usage["by_model"][sent_model] = {"requests": 0, "tokens": 0}
        _usage["by_model"][sent_model]["requests"] += 1
        _usage["by_model"][sent_model]["tokens"] += total
        _request_log.append({
            "timestamp": now,
            "requested_model": requested_model,
            "sent_model": sent_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tools_count": tools_count,
        })


# ── Token management ────────────────────────────────────────────────

TOKEN_REFRESH_MARGIN = 60  # refresh 60s before expiry


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
    last_exc = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return {
                "token": data["token"],
                "endpoint": data.get("endpoints", {}).get(
                    "api", "https://api.githubcopilot.com"
                ),
                "expires": time.time() + TOKEN_TTL,
            }
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(2 ** attempt + random.random())
    raise last_exc


def _get_token():
    with _lock:
        if _cache["token"] is None or time.time() >= (_cache["expires"] - TOKEN_REFRESH_MARGIN):
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
            tool_call_parts = []
            tool_result_msgs = []
            image_parts = []
            has_images = False
            for block in content:
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_result":
                    # Convert tool_result to OpenAI tool message
                    tc = block.get("content", "")
                    if isinstance(tc, list):
                        tc = "\n".join(
                            b.get("text", "") for b in tc if b.get("type") == "text"
                        )
                    tool_result_msgs.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(tc),
                    })
                elif btype == "tool_use":
                    # Convert tool_use to OpenAI assistant tool_calls
                    tool_call_parts.append({
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
                elif btype == "image":
                    has_images = True
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        data = source.get("data", "")
                        image_parts.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}})
                    elif source.get("type") == "url":
                        image_parts.append({"type": "image_url", "image_url": {"url": source.get("url", "")}})

            # If this message has tool_use blocks, emit as assistant with tool_calls
            if tool_call_parts and role == "assistant":
                assistant_msg = {"role": "assistant", "tool_calls": tool_call_parts}
                combined_text = "\n".join(p for p in text_parts if p)
                if combined_text:
                    assistant_msg["content"] = combined_text
                else:
                    assistant_msg["content"] = None
                messages.append(assistant_msg)
            elif has_images:
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

            # Append tool_result messages after the parent message
            for tr in tool_result_msgs:
                messages.append(tr)
        else:
            messages.append({"role": role, "content": str(content)})

    result = {
        "model": _map_model(body.get("model", "claude-sonnet-4")),
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 1),
        "stream": body.get("stream", False),
    }

    # Convert Anthropic tools to OpenAI functions format
    tools = body.get("tools")
    if tools:
        oai_tools = []
        for t in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        result["tools"] = oai_tools

        # Map tool_choice
        tc = body.get("tool_choice")
        if tc == "auto" or tc is None:
            result["tool_choice"] = "auto"
        elif tc == "none":
            result["tool_choice"] = "none"
        elif tc == "any":
            result["tool_choice"] = "required"
        elif isinstance(tc, dict) and tc.get("type") == "tool":
            result["tool_choice"] = {"type": "function", "function": {"name": tc.get("name", "")}}

    # Map Anthropic thinking parameter to OpenAI reasoning_effort
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = thinking.get("budget_tokens", 0)
        if budget > 0:
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
    tool_calls = message.get("tool_calls", [])

    stop_map = {"stop": "end_turn", "length": "max_tokens"}
    usage = oai.get("usage", {})

    content = []
    if reasoning_text:
        content.append({"type": "thinking", "thinking": reasoning_text})
    if text:
        content.append({"type": "text", "text": text})

    # Convert OpenAI tool_calls to Anthropic tool_use blocks
    if tool_calls:
        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            content.append({
                "type": "tool_use",
                "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                "name": fn.get("name", ""),
                "input": args,
            })

    if not content:
        content.append({"type": "text", "text": ""})

    # Map finish_reason: tool_calls → tool_use
    if finish == "tool_calls":
        stop_reason = "tool_use"
    else:
        stop_reason = stop_map.get(finish, "end_turn")

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": stop_reason,
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


def _stream_from_copilot_sse(upstream_resp, wfile, model: str) -> int:
    """Read OpenAI SSE stream from Copilot and re-emit as Anthropic SSE.
    Returns approximate output token count."""
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
    # Tool call accumulation: {index: {id, name, arguments}}
    tool_calls_acc = {}
    finish_reason = "stop"

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
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta", {})
        fr = choice.get("finish_reason")
        if fr:
            finish_reason = fr

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

        # Accumulate streaming tool_calls
        for tc in delta.get("tool_calls", []):
            idx = tc.get("index", 0)
            if idx not in tool_calls_acc:
                tool_calls_acc[idx] = {
                    "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                    "name": "",
                    "arguments": "",
                }
            fn = tc.get("function", {})
            if "name" in fn:
                tool_calls_acc[idx]["name"] += fn["name"]
            if "arguments" in fn:
                tool_calls_acc[idx]["arguments"] += fn["arguments"]

    # Finalize thinking block
    if thinking_started and not thinking_ended:
        wfile.write(_sse_line("content_block_stop", {
            "type": "content_block_stop", "index": 0,
        }))
        content_index = 1

    # Finalize text block
    if text_started:
        wfile.write(_sse_line("content_block_stop", {
            "type": "content_block_stop", "index": content_index,
        }))
        content_index += 1
    elif not tool_calls_acc:
        # No text and no tools — emit empty text block
        wfile.write(_sse_line("content_block_start", {
            "type": "content_block_start", "index": content_index,
            "content_block": {"type": "text", "text": ""},
        }))
        wfile.write(_sse_line("content_block_stop", {
            "type": "content_block_stop", "index": content_index,
        }))
        content_index += 1

    # Emit accumulated tool_use blocks
    for idx in sorted(tool_calls_acc.keys()):
        tc = tool_calls_acc[idx]
        try:
            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except (json.JSONDecodeError, TypeError):
            args = {}
        tool_id = tc["id"] if tc["id"].startswith("toolu_") else f"toolu_{tc['id']}"
        # content_block_start for tool_use
        wfile.write(_sse_line("content_block_start", {
            "type": "content_block_start", "index": content_index,
            "content_block": {"type": "tool_use", "id": tool_id, "name": tc["name"], "input": {}},
        }))
        # Send input as a single delta
        wfile.write(_sse_line("content_block_delta", {
            "type": "content_block_delta", "index": content_index,
            "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)},
        }))
        wfile.write(_sse_line("content_block_stop", {
            "type": "content_block_stop", "index": content_index,
        }))
        content_index += 1
        output_tokens += max(1, len(tc["arguments"]) // 4)

    # Determine stop reason
    if tool_calls_acc or finish_reason == "tool_calls":
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    wfile.write(_sse_line("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }))
    wfile.write(_sse_line("message_stop", {"type": "message_stop"}))
    wfile.flush()
    return output_tokens


# ── Upstream request with retries ────────────────────────────────────


def _make_upstream_request(url, headers, body, timeout=300, max_retries=3):
    """Make an upstream POST request with retries and exponential backoff."""
    headers = dict(headers)  # copy to avoid mutating caller's dict
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

    def process_request(self, request, client_address):
        if not _request_semaphore.acquire(timeout=5):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            except Exception:
                pass
            self.close_request(request)
            return
        try:
            super().process_request(request, client_address)
        finally:
            _request_semaphore.release()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/v1/models":
            with _models_lock:
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
                    with _config_lock:
                        vals = list(_config["model_map"].values())
                    fallback = {
                        "object": "list",
                        "data": [
                            {"id": v, "object": "model", "owned_by": _model_vendor(v)}
                            for v in vals
                        ],
                    }
                    self._reply(200, fallback)
            return
        if path == "/health":
            self._reply(200, {"status": "ok"})
            return
        if path == "/api/stats":
            self._handle_api_stats()
            return
        if path == "/api/config":
            self._handle_api_config_get()
            return
        if path == "/api/auth-status":
            self._handle_api_auth_status()
            return
        if path == "/" or path == "":
            self._serve_dashboard()
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/messages":
            self._handle_messages()
        elif path == "/v1/messages/count_tokens":
            self._handle_count_tokens()
        elif path == "/v1/chat/completions":
            self._handle_chat_completions()
        elif path == "/api/config":
            self._handle_api_config_post()
        else:
            self._reply(404, {"error": "not found"})

    # ── API handlers ─────────────────────────────────────────────────

    def _handle_api_stats(self):
        with _usage_lock:
            stats = {
                "total_requests": _usage["total_requests"],
                "total_tokens": _usage["total_tokens"],
                "by_model": dict(_usage["by_model"]),
                "uptime_seconds": int(time.time() - _usage["started"]),
                "request_log": list(_request_log),
            }
        self._reply(200, stats)

    def _handle_api_config_get(self):
        with _config_lock:
            cfg = {
                "model_map": dict(_config["model_map"]),
                "available_targets": list(_config["available_targets"]),
                "overrides": dict(_config["overrides"]),
            }
        self._reply(200, cfg)

    def _handle_api_config_post(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 10240:
            self._reply(400, {"error": "request too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._reply(400, {"error": "invalid JSON"})
            return
        overrides = body.get("overrides")
        if not isinstance(overrides, dict):
            self._reply(400, {"error": "missing or invalid 'overrides' object"})
            return
        with _config_lock:
            available = list(_config["available_targets"])
            for slot, target in overrides.items():
                if slot not in ("main", "fast"):
                    self._reply(400, {"error": f"invalid slot: {slot}. Must be 'main' or 'fast'"})
                    return
                if target and available and target not in available:
                    self._reply(400, {"error": f"target '{target}' not in available_targets"})
                    return
            _config["overrides"] = {k: v for k, v in overrides.items() if v}
        try:
            _save_models()
        except Exception as exc:
            logger.error("Failed to save models.json: %s", exc)
        self._reply(200, {"status": "ok", "overrides": overrides})

    def _handle_api_auth_status(self):
        try:
            subprocess.check_output(
                ["gh", "auth", "token", "-h", "github.com"],
                text=True, timeout=5, stderr=subprocess.DEVNULL,
            )
            self._reply(200, {"authenticated": True})
        except Exception:
            self._reply(200, {"authenticated": False})

    def _serve_dashboard(self):
        html = _DASHBOARD_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _handle_count_tokens(self):
        """Approximate token count for Anthropic count_tokens endpoint."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._reply(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}})
            return
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
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._reply(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}})
            return
        original_model = body.get("model", "claude-sonnet-4")
        stream = body.get("stream", False)

        openai_body = _anthropic_to_openai(body)
        sent_model = openai_body["model"]
        tools_count = len(body.get("tools", []))

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
                out_tokens = _stream_from_copilot_sse(resp, self.wfile, original_model)
                self.wfile.flush()
                _track_usage(original_model, sent_model, 0, out_tokens, tools_count)
            else:
                oai_data = json.loads(resp.read())
                anthropic_resp = _openai_to_anthropic(oai_data, original_model)
                usage = anthropic_resp.get("usage", {})
                _track_usage(original_model, sent_model,
                             usage.get("input_tokens", 0),
                             usage.get("output_tokens", 0),
                             tools_count)
                self._reply(200, anthropic_resp)

        except HTTPError as exc:
            status = exc.code
            logger.error("Upstream error %d", status)
            try:
                self._reply(status, {
                    "type": "error",
                    "error": {"type": "api_error", "message": f"Upstream error (HTTP {status})"},
                })
            except BrokenPipeError:
                pass
        except BrokenPipeError:
            logger.debug("Client disconnected (broken pipe)")
        except Exception as exc:
            logger.error("Internal error: %s", exc)
            try:
                self._reply(500, {
                    "type": "error",
                    "error": {"type": "api_error", "message": "Internal proxy error"},
                })
            except BrokenPipeError:
                pass

    def _handle_chat_completions(self):
        """OpenAI-compatible pass-through (for Cursor, etc.)."""
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            token, endpoint = _get_token()
        except Exception as exc:
            self._reply(502, {"error": f"token error: {exc}"})
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Editor-Version": "vscode/1.96.0",
            "Copilot-Integration-Id": "vscode-chat",
        }

        try:
            resp = _make_upstream_request(
                f"{endpoint}/chat/completions", headers, body,
            )
            resp_body = resp.read()
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
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


# ── Dashboard HTML ───────────────────────────────────────────────────

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Copilot Proxy Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:1rem 2rem;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1.25rem;color:#f0f6fc}
.auth-badge{padding:4px 12px;border-radius:12px;font-size:.75rem;font-weight:600}
.auth-ok{background:#238636;color:#fff}
.auth-fail{background:#da3633;color:#fff}
.container{max-width:1100px;margin:0 auto;padding:1.5rem}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:1rem;margin-bottom:1.5rem}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1.25rem}
.card h3{font-size:.75rem;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem}
.card .val{font-size:1.75rem;font-weight:700;color:#58a6ff}
.section{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1.25rem;margin-bottom:1.5rem}
.section h2{font-size:1rem;color:#f0f6fc;margin-bottom:1rem;padding-bottom:.5rem;border-bottom:1px solid #30363d}
.model-row{display:flex;align-items:center;gap:1rem;margin-bottom:.75rem}
.model-row label{width:100px;font-size:.875rem;color:#8b949e}
.model-row select{flex:1;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:.875rem}
.model-row .current{font-size:.75rem;color:#8b949e;min-width:120px}
button.save{background:#238636;color:#fff;border:none;border-radius:6px;padding:8px 20px;font-size:.875rem;font-weight:600;cursor:pointer;margin-top:.5rem}
button.save:hover{background:#2ea043}
button.save:disabled{opacity:.5;cursor:default}
.status-msg{font-size:.75rem;margin-left:1rem;display:inline}
.status-msg.ok{color:#3fb950}
.status-msg.err{color:#f85149}
table{width:100%;border-collapse:collapse;font-size:.8125rem}
th{text-align:left;color:#8b949e;font-weight:600;padding:.5rem;border-bottom:1px solid #30363d}
td{padding:.5rem;border-bottom:1px solid #21262d;color:#c9d1d9}
tr:hover td{background:#161b22}
.empty{color:#484f58;text-align:center;padding:2rem}
</style>
</head>
<body>
<header>
  <h1>&#x1f680; Copilot Proxy</h1>
  <span id="auth" class="auth-badge">checking...</span>
</header>
<div class="container">
  <div class="cards">
    <div class="card"><h3>Requests</h3><div class="val" id="s-reqs">0</div></div>
    <div class="card"><h3>Tokens</h3><div class="val" id="s-tokens">0</div></div>
    <div class="card"><h3>Uptime</h3><div class="val" id="s-uptime">0s</div></div>
  </div>

  <div class="section">
    <h2>Model Overrides</h2>
    <div class="model-row">
      <label>Main (opus)</label>
      <select id="ov-main"><option value="">default</option></select>
      <span class="current" id="cur-main"></span>
    </div>
    <div class="model-row">
      <label>Fast (haiku)</label>
      <select id="ov-fast"><option value="">default</option></select>
      <span class="current" id="cur-fast"></span>
    </div>
    <button class="save" id="save-btn" onclick="saveConfig()">Save</button>
    <span id="save-status" class="status-msg"></span>
  </div>

  <div class="section">
    <h2>Per-Model Usage</h2>
    <table>
      <thead><tr><th>Model</th><th>Requests</th><th>Tokens</th></tr></thead>
      <tbody id="model-tbody"><tr><td colspan="3" class="empty">No data yet</td></tr></tbody>
    </table>
  </div>

  <div class="section">
    <h2>Recent Requests</h2>
    <table>
      <thead><tr><th>Time</th><th>Requested</th><th>Sent To</th><th>Tools</th><th>In</th><th>Out</th></tr></thead>
      <tbody id="log-tbody"><tr><td colspan="6" class="empty">No requests yet</td></tr></tbody>
    </table>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

function fmt(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}

function fmtTime(s) {
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}

function fmtTs(ts) {
  return new Date(ts * 1000).toLocaleTimeString();
}

async function loadAuth() {
  try {
    const r = await fetch('/api/auth-status');
    const d = await r.json();
    const el = $('auth');
    if (d.authenticated) { el.textContent = 'gh authenticated'; el.className = 'auth-badge auth-ok'; }
    else { el.textContent = 'not authenticated'; el.className = 'auth-badge auth-fail'; }
  } catch(e) { $('auth').textContent = 'error'; }
}

async function loadConfig() {
  try {
    const r = await fetch('/api/config');
    const d = await r.json();
    ['main','fast'].forEach(slot => {
      const sel = $('ov-' + slot);
      const cur = $('cur-' + slot);
      sel.innerHTML = '<option value="">default</option>';
      (d.available_targets || []).forEach(t => {
        const opt = document.createElement('option');
        opt.value = t; opt.textContent = t;
        sel.appendChild(opt);
      });
      const ov = (d.overrides || {})[slot] || '';
      sel.value = ov;
      const base = slot === 'main' ? (d.model_map['claude-opus-4-6'] || 'claude-opus-4.6') :
                                     (d.model_map['claude-haiku-4-5'] || 'claude-haiku-4.5');
      cur.textContent = ov ? '\\u2192 ' + ov : '\\u2192 ' + base + ' (default)';
    });
  } catch(e) {}
}

async function saveConfig() {
  const btn = $('save-btn');
  const status = $('save-status');
  btn.disabled = true;
  const overrides = {};
  const mainVal = $('ov-main').value;
  const fastVal = $('ov-fast').value;
  if (mainVal) overrides.main = mainVal;
  if (fastVal) overrides.fast = fastVal;
  try {
    const r = await fetch('/api/config', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({overrides})
    });
    if (r.ok) { status.textContent = 'Saved!'; status.className = 'status-msg ok'; }
    else { const e = await r.json(); status.textContent = e.error || 'Error'; status.className = 'status-msg err'; }
  } catch(e) { status.textContent = 'Network error'; status.className = 'status-msg err'; }
  btn.disabled = false;
  loadConfig();
  setTimeout(() => { status.textContent = ''; }, 3000);
}

function esc(s) { const d = document.createElement('div'); d.textContent = String(s); return d.innerHTML; }

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();
    $('s-reqs').textContent = fmt(d.total_requests);
    $('s-tokens').textContent = fmt(d.total_tokens);
    $('s-uptime').textContent = fmtTime(d.uptime_seconds);

    const mt = $('model-tbody');
    const models = Object.entries(d.by_model || {});
    if (models.length === 0) { mt.innerHTML = '<tr><td colspan="3" class="empty">No data yet</td></tr>'; }
    else { mt.innerHTML = models.map(([m,s]) => '<tr><td>'+esc(m)+'</td><td>'+esc(s.requests)+'</td><td>'+esc(fmt(s.tokens))+'</td></tr>').join(''); }

    const lt = $('log-tbody');
    const logs = (d.request_log || []).slice().reverse();
    if (logs.length === 0) { lt.innerHTML = '<tr><td colspan="6" class="empty">No requests yet</td></tr>'; }
    else { lt.innerHTML = logs.map(l => '<tr><td>'+esc(fmtTs(l.timestamp))+'</td><td>'+esc(l.requested_model)+'</td><td>'+esc(l.sent_model)+'</td><td>'+(l.tools_count||0)+'</td><td>'+esc(fmt(l.input_tokens))+'</td><td>'+esc(fmt(l.output_tokens))+'</td></tr>').join(''); }
  } catch(e) {}
}

loadAuth();
loadConfig();
loadStats();
setInterval(loadStats, 5000);
setInterval(loadAuth, 30000);
</script>
</body>
</html>
"""


# ── main ─────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "DEBUG").upper(), logging.DEBUG),
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
    logger.info("Dashboard:      http://localhost:%d/", PORT)
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
