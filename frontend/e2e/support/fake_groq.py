"""Fake Groq API for end-to-end tests (standard library only; no network, no key).

The backend's Groq SDK honours GROQ_BASE_URL, so pointing it here makes every LLM
call deterministic. Responses are OpenAI-shaped, including real tool calls, so the
LangGraph agent runs for real:

  agent turn, user asks to log  -> tool call log_interaction(hcp_id, raw_text)
  agent turn after a tool result -> "Logged it." (or asks the rep to pick a product)
  extraction prompt              -> structured JSON
  follow-up suggestions prompt   -> {"follow_ups": [...]}
  voice-note summary prompt      -> one-line summary
  /audio/transcriptions          -> fixed transcript

Usage: python fake_groq.py [port]   (default 9999)
"""

from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TRANSCRIPT = "E2E voice note: met the doctor, discussed CardioMax pricing and the dosing guide."


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def completion(message: dict, finish: str) -> dict:
    return {
        "id": f"chatcmpl-e2e-{time.time_ns()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "fake-e2e",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def answer(body: dict) -> dict:
    messages = body.get("messages") or []
    last = messages[-1] if messages else {}
    prompt = _text(last.get("content"))

    if body.get("tools"):  # the LangGraph agent
        if last.get("role") == "tool":
            result = _text(last.get("content"))
            if "needs_product_choice" in result:
                return completion({"role": "assistant", "content": "Which catalog product was that sample?"}, "stop")
            return completion({"role": "assistant", "content": "Logged it."}, "stop")
        hcp = re.search(r"Selected HCP ID: (\S+)", prompt)
        user = re.search(r"User message:\n(.*?)\n\nIMPORTANT", prompt, flags=re.S)
        user_text = (user.group(1) if user else prompt).strip()
        if hcp and "log" in user_text.lower():
            call = {
                "id": f"call_{time.time_ns()}",
                "type": "function",
                "function": {
                    "name": "log_interaction",
                    "arguments": json.dumps({"hcp_id": hcp.group(1), "raw_text": user_text, "interaction_type": "Call"}),
                },
            }
            return completion({"role": "assistant", "content": "", "tool_calls": [call]}, "tool_calls")
        return completion({"role": "assistant", "content": "How can I help with this HCP?"}, "stop")

    if "data-extraction" in prompt:
        products = ["CardioMax"] if "cardiomax" in prompt.lower() else []
        data = {
            "summary": "E2E chat summary: discussed CardioMax pricing.",
            "products_discussed": products,
            "samples_dropped": [],
            "materials_shared": [],
            "key_topics": ["pricing"],
            "attendees": [],
            "outcomes": None,
            "follow_up_actions": None,
            "hcp_sentiment": "Positive",
            "sentiment_score": 0.5,
        }
        return completion({"role": "assistant", "content": json.dumps(data)}, "stop")
    if '"follow_ups"' in prompt:
        data = {"follow_ups": [{"reason": "Send the CardioMax dosing guide", "days_from_now": 3}]}
        return completion({"role": "assistant", "content": json.dumps(data)}, "stop")
    if "Voice note transcript" in prompt:
        return completion({"role": "assistant", "content": "E2E summary: discussed CardioMax pricing and the dosing guide."}, "stop")
    return completion({"role": "assistant", "content": "Bring the latest CardioMax study next visit."}, "stop")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"status": "fake-groq"})

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/audio/transcriptions"):
            return self._send(200, {"text": TRANSCRIPT})
        if self.path.endswith("/chat/completions"):
            return self._send(200, answer(json.loads(body or b"{}")))
        self._send(404, {"error": {"message": f"fake groq: no route {self.path}"}})

    def log_message(self, *_args) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    print(f"fake groq on :{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
