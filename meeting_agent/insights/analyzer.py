"""Turn a transcript into structured insights with a local LLM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm import LLMError, OllamaClient
from .prompts import map_prompt, reduce_prompt

MODES = ("interview", "paper", "general")


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split on line boundaries so no transcript line is cut in half."""
    chunks, cur, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) + 1 > max_chars and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def load_paper(path: Path, max_chars: int = 12000) -> str:
    """Extract text from a PDF/text file of the paper under discussion."""
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    # Keep the start (abstract/intro/method) and the end (results/conclusion).
    head = int(max_chars * 0.7)
    return text[:head] + " ... [truncated] ... " + text[-(max_chars - head):]


def build_context(mode: str, paper_text: str = "", rubric: str = "", role: str = "",
                  participants: str = "") -> str:
    parts = []
    if participants:
        parts.append(f"Participants: {participants}")
    if mode == "interview":
        if role:
            parts.append(f"Role being interviewed for: {role}")
        if rubric:
            parts.append(f"Evaluate these competencies (use these names):\n{rubric}")
    if mode == "paper" and paper_text:
        parts.append(f"Paper text (excerpt):\n{paper_text}")
    return "\n\n".join(parts)


def analyze(transcript: str, mode: str, llm_cfg: dict[str, Any], context: str = "",
            log=print) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not transcript.strip():
        raise LLMError("Transcript is empty; nothing to analyse.")
    client = OllamaClient(llm_cfg)
    client.check()

    chunks = chunk_text(transcript, llm_cfg.get("chunk_chars", 10000))
    notes: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        log(f"Analysing part {i + 1}/{len(chunks)} with {client.model}...")
        system, user = map_prompt(mode, chunk, i, len(chunks))
        try:
            notes.append(client.chat_json(system, user))
        except LLMError as e:
            log(f"  part {i + 1} failed ({e}); skipping")

    log("Synthesising report...")
    system, user = reduce_prompt(mode, json.dumps(notes, ensure_ascii=False), context)
    report = client.chat_json(system, user)
    report["_meta"] = {"mode": mode, "model": client.model, "chunks": len(chunks)}
    return report
