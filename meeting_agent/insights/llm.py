"""Minimal client for a local Ollama server (free, open-source, runs offline).

Uses only the standard library. By default it refuses any non-loopback URL so a
config typo can't ship your transcripts to someone else's server.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    pass


def _is_loopback(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            return all(ipaddress.ip_address(info[4][0]).is_loopback for info in socket.getaddrinfo(host, None))
        except OSError:
            return False


class OllamaClient:
    def __init__(self, cfg: dict[str, Any]):
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.temperature = cfg.get("temperature", 0.2)
        self.num_ctx = cfg.get("num_ctx", 8192)
        self.timeout = cfg.get("timeout_seconds", 600)
        if not cfg.get("allow_remote") and not _is_loopback(self.base_url):
            raise LLMError(f"Refusing non-local LLM endpoint {self.base_url}. "
                           "Set llm.allow_remote: true only for a server you control.")

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as e:
            raise LLMError(f"Could not reach Ollama at {self.base_url} ({e}). "
                           f"Is it running? Try: ollama serve && ollama pull {self.model}") from e

    def check(self) -> None:
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=5) as resp:
                tags = json.loads(resp.read())
        except Exception as e:
            raise LLMError(f"Ollama not reachable at {self.base_url}: {e}") from e
        names = {m.get("name") for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            raise LLMError(f"Model '{self.model}' not pulled. Run: ollama pull {self.model}")

    def chat(self, system: str, user: str) -> str:
        out = self._post("/api/chat", {
            "model": self.model,
            "stream": False,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        return out.get("message", {}).get("content", "").strip()

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        out = self._post("/api/chat", {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        return parse_json(out.get("message", {}).get("content", ""))


def parse_json(text: str) -> dict[str, Any]:
    """Parse model output as JSON, tolerating code fences and leading chatter."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        val = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"Model did not return JSON: {text[:200]!r}")
        val = json.loads(text[start:end + 1])
    if not isinstance(val, dict):
        raise LLMError("Model returned JSON that is not an object")
    return val
