"""Ask free-form questions about a stored meeting."""

from __future__ import annotations

import json
import re
from typing import Any

from .analyzer import chunk_text
from .llm import OllamaClient
from .prompts import GUARD

SYSTEM = ("You answer questions about a recorded meeting using only the transcript excerpts "
          "and notes provided. Cite timestamps like [12:34]. If the answer is not in the "
          "material, say you can't find it. " + GUARD.replace(
              "Respond with a single JSON object and nothing else.", "Answer in plain prose."))


def _score(chunk: str, terms: set[str]) -> int:
    words = re.findall(r"\w+", chunk.lower())
    return sum(1 for w in words if w in terms)


def ask(record: dict[str, Any], question: str, llm_cfg: dict[str, Any]) -> str:
    client = OllamaClient(llm_cfg)
    transcript = record.get("transcript", "")
    budget = llm_cfg.get("chunk_chars", 10000)
    if len(transcript) > budget:
        # Simple keyword retrieval: pick the transcript chunks that best match the question.
        terms = {w for w in re.findall(r"\w+", question.lower()) if len(w) > 3}
        chunks = chunk_text(transcript, budget // 3)
        ranked = sorted(range(len(chunks)), key=lambda i: _score(chunks[i], terms), reverse=True)[:3]
        transcript = "\n...\n".join(chunks[i] for i in sorted(ranked))
    notes = json.dumps({k: v for k, v in (record.get("insights") or {}).items() if k != "_meta"},
                       ensure_ascii=False)[:budget // 2]
    user = (f"<notes>\n{notes}\n</notes>\n\n<transcript>\n{transcript}\n</transcript>\n\n"
            f"Question: {question}")
    return client.chat(SYSTEM, user)
