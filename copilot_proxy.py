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
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PORT = int(os.environ.get("PORT", "8080"))
TOKEN_TTL = 600  # refresh every 10 min

_lock = threading.Lock()
_cache: dict = {"token": None, "endpoint": None, "expires": 0}

# ── Model mapping ────────────────────────────────────────────────────

MODEL_MAP = {
    "claude-opus-4-5":          "claude-opus-4.5",
    "claude-sonnet-4-5":        "claude-sonnet-4.5",
    "claude-sonnet-4":          "claude-sonnet-4",
    "claude-haiku-4-5":         "claude-haiku-4.5",
    "opus":                     "claude-opus-4.5",
    "sonnet":                   "claude-sonnet-4",
    "haiku":                    "claude-haiku-4.5",
}


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

    # System prompt
    system = body.get("system")
    if system:
        if isinstance(system, list):
            # Anthropic allows system as array of content blocks
            text = "\n".join(
                b.get("text", "") for b in system if b.get("type") == "text"
            )
        else:
            text = system
        if text:
            messages.append({"role": "system", "content": text})

    # Messages
    for msg in body.get("messages", []):
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            parts = []
            for block in content:
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_result":
                    # Flatten tool results to text
                    tc = block.get("content", "")
                    if isinstance(tc, list):
                        tc = "\n".join(
                            b.get("text", "") for b in tc if b.get("type") == "text"
                        )
                    parts.append(tc)
                elif btype == "tool_use":
                    parts.append(json.dumps({"tool": block.get("name"), "input": block.get("input")}))
            text = "\n".join(p for p in parts if p)
            if text:
                messages.append({"role": role, "content": text})
        else:
            messages.append({"role": role, "content": str(content)})

    return {
        "model": _map_model(body.get("model", "claude-sonnet-4")),
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 1),
        "stream": body.get("stream", False),
    }


def _openai_to_anthropic(oai: dict, model: str) -> dict:
    """Convert OpenAI chat/completions response → Anthropic Messages API."""
    choice = (oai.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content", "")
    finish = choice.get("finish_reason", "stop")

    stop_map = {"stop": "end_turn", "length": "max_tokens"}
    usage = oai.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}] if text else [],
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
    wfile.write(_sse_line("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    }))
    wfile.write(_sse_line("ping", {"type": "ping"}))
    wfile.flush()

    output_tokens = 0
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
        text = delta.get("content", "")
        if text:
            output_tokens += max(1, len(text) // 4)
            wfile.write(_sse_line("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": text},
            }))
            wfile.flush()

    # Finalize
    wfile.write(_sse_line("content_block_stop", {
        "type": "content_block_stop", "index": 0,
    }))
    wfile.write(_sse_line("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }))
    wfile.write(_sse_line("message_stop", {"type": "message_stop"}))
    wfile.flush()


# ── HTTP handler ─────────────────────────────────────────────────────

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            self._reply(200, {
                "object": "list",
                "data": [
                    {"id": k, "object": "model", "owned_by": "anthropic"}
                    for k in MODEL_MAP
                ],
            })
            return
        if self.path == "/health":
            self._reply(200, {"status": "ok"})
            return
        self.send_response(302)
        self.send_header("Location", "https://github.com/features/copilot")
        self.end_headers()

    def do_POST(self):
        if self.path == "/v1/messages":
            self._handle_messages()
        elif self.path == "/v1/chat/completions":
            self._handle_chat_completions()
        else:
            self._reply(404, {"error": "not found"})

    def _handle_messages(self):
        """Anthropic Messages API → Copilot → Anthropic response."""
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
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

        upstream = Request(
            f"{endpoint}/chat/completions",
            data=json.dumps(openai_body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Editor-Version": "vscode/1.96.0",
                "Editor-Plugin-Version": "copilot-chat/0.40.0",
                "Copilot-Integration-Id": "vscode-chat",
                "Openai-Organization": "github-copilot",
                "Openai-Intent": "conversation-agent",
                "X-Request-Id": str(uuid.uuid4()),
                "Machine-Id": MACHINE_ID,
            },
            method="POST",
        )

        try:
            resp = urlopen(upstream, timeout=300)

            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                _stream_from_copilot_sse(resp, self.wfile, original_model)
            else:
                oai_data = json.loads(resp.read())
                anthropic_resp = _openai_to_anthropic(oai_data, original_model)
                self._reply(200, anthropic_resp)

        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps({
                    "type": "error",
                    "error": {"type": "api_error", "message": err_body},
                }).encode())
            except Exception:
                self.wfile.write(err_body.encode())
        except Exception as exc:
            self._reply(500, {
                "type": "error",
                "error": {"type": "api_error", "message": str(exc)},
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
        print(f"[proxy] {args[0]}")


# ── main ─────────────────────────────────────────────────────────────

def main():
    try:
        _get_token()
        print("[proxy] ✓ Copilot token acquired")
    except Exception as exc:
        print(f"[proxy] FATAL: cannot get Copilot token — {exc}", file=sys.stderr)
        print("[proxy] Run: gh auth login -h github.com -p https -w", file=sys.stderr)
        sys.exit(1)

    print(f"[proxy] Listening on http://0.0.0.0:{PORT}")
    print(f"[proxy] Anthropic API:  POST http://localhost:{PORT}/v1/messages")
    print(f"[proxy] OpenAI API:     POST http://localhost:{PORT}/v1/chat/completions")
    print(f"[proxy] Models:         GET  http://localhost:{PORT}/v1/models")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] Shutting down.")
    server.server_close()


if __name__ == "__main__":
    main()
