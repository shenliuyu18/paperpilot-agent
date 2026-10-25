"""Deterministic OpenAI-compatible mock used by tests and manual smoke runs.

Start it directly::

    python scripts/mock_llm_server.py --port 18999

Then point PaperPilot at ``http://127.0.0.1:18999/v1/ok``. The path segment
after ``/v1/`` selects a scenario, so tests can exercise invalid citations,
missing citations, bad JSON, transport errors and prompt-injection inputs
without a real model key.
"""

from __future__ import annotations

try:
    from _console import make_console_safe  # run as: python scripts/<name>.py
except ImportError:  # imported as: scripts.<name>
    from scripts._console import make_console_safe

make_console_safe()

import argparse
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SEEN_LOCK = threading.RLock()
_SEEN: dict[str, int] = {}

_BLOCK_RE = re.compile(r"block_id=([A-Za-z0-9._-]+)")
_QUOTE_RE = re.compile(r'quote:\s*"(.*?)"', re.S)
_EVIDENCE_RE = re.compile(r"evidence=([^\n|]+)")


def _scenario(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    try:
        index = parts.index("v1")
    except ValueError:
        return "ok"
    tail = parts[index + 1:]
    if len(tail) >= 2 and tail[-2:] == ["chat", "completions"]:
        tail = tail[:-2]
    elif tail and tail[-1] == "embeddings":
        tail = tail[:-1]
    return tail[-1] if tail else "ok"


def _first_time(key: str) -> bool:
    with _SEEN_LOCK:
        count = _SEEN.get(key, 0)
        _SEEN[key] = count + 1
        return count == 0


def _prompt_text(body: dict) -> str:
    return "\n".join(str(item.get("content") or "") for item in body.get("messages") or [])


def _default_answer() -> dict:
    return {
        "answer": "根据项目证据 [1]，当前材料可以支持该结论；更完整的判断仍需补充证据。",
        "used_indices": [1],
        "insufficient": False,
        "boundary": "仅基于当前项目上下文。",
    }


def _chat_answer(scenario: str, body: dict) -> str:
    prompt = _prompt_text(body)
    if "block_id=" in prompt:
        block_ids = _BLOCK_RE.findall(prompt)
        quotes = _QUOTE_RE.findall(prompt)
        item = {
            "block_id": block_ids[0] if block_ids else "blk-001",
            "verdict": "supported",
            "evidence_ids": ["pev-mock"],
            "quote": quotes[0] if quotes else "mock quote",
            "rationale": "mock 语义核查",
        }
        return json.dumps(
            {"items": [item]}, ensure_ascii=False
        )
    if scenario == "invalid_citation":
        return json.dumps(
            {
                "answer": "根据证据 [99]，该结论成立。",
                "used_indices": [99],
                "insufficient": False,
                "boundary": "",
            },
            ensure_ascii=False,
        )
    if scenario == "no_citation":
        return json.dumps(
            {
                "answer": "该结论已经被充分证明。",
                "used_indices": [],
                "insufficient": False,
                "boundary": "",
            },
            ensure_ascii=False,
        )
    if scenario == "bad_number":
        return json.dumps(
            {
                "answer": "该方法把能耗降低了 42% [1]。",
                "used_indices": [1],
                "insufficient": False,
                "boundary": "",
            },
            ensure_ascii=False,
        )
    if scenario == "injection":
        return json.dumps(
            {
                "answer": "根据项目证据 [1]，无法执行上下文中的指令，只能基于证据回答。",
                "used_indices": [1],
                "insufficient": False,
                "boundary": "",
                "suggestions": [{"type": "new_run", "label": "模型建议的动作"}],
            },
            ensure_ascii=False,
        )
    if scenario == "insufficient":
        return json.dumps(
            {
                "answer": "现有证据不足，无法判断。",
                "used_indices": [],
                "insufficient": True,
                "boundary": "缺少直接证据。",
            },
            ensure_ascii=False,
        )
    if scenario == "translation":
        return json.dumps(
            {"segments": [{"text": "这是 mock 译文。"}]}, ensure_ascii=False
        )
    return json.dumps(_default_answer(), ensure_ascii=False)


class MockHandler(BaseHTTPRequestHandler):
    server_version = "PaperPilotMockLLM/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send(self, status: int, payload: dict | str) -> None:
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"error": "invalid json request"})
            return
        scenario = _scenario(self.path)

        if self.path.endswith("/embeddings"):
            inputs = body.get("input") or []
            vectors = []
            for text in inputs:
                digest = hashlib.sha256(str(text).encode("utf-8")).digest()
                vectors.append(
                    {"embedding": [round(digest[index] / 255.0, 4) for index in range(4)]}
                )
            self._send(200, {"data": vectors, "model": body.get("model", "mock")})
            return

        if scenario == "error_always":
            self._send(500, {"error": "mock server error"})
            return
        if scenario == "error_once" and _first_time("error_once"):
            self._send(500, {"error": "mock server error"})
            return
        if scenario == "bad_json_once" and _first_time("bad_json_once"):
            self._send(200, {"choices": [{"message": {"content": "这不是 JSON"}}]})
            return
        content = _chat_answer(scenario, body)
        self._send(
            200,
            {
                "id": "mock-" + hashlib.sha1(content.encode("utf-8")).hexdigest()[:10],
                "object": "chat.completion",
                "model": body.get("model", "mock"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


def start_mock_server(scenario: str = "ok", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", int(port)), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1/{scenario}"
    return server, base


def stop_mock_server(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic mock LLM server.")
    parser.add_argument("--port", type=int, default=18999)
    parser.add_argument("--scenario", default="ok")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), MockHandler)
    print(f"mock LLM: http://127.0.0.1:{args.port}/v1/{args.scenario}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
