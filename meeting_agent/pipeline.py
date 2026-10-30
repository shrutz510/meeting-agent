"""Audio -> transcript -> insights -> encrypted store."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .insights.analyzer import analyze, build_context, load_paper
from .insights.llm import LLMError
from .storage import MeetingStore, secure_delete
from .transcribe import diarize, render_transcript, transcribe


def open_store(cfg: Config) -> MeetingStore:
    cfg.ensure_dirs()
    return MeetingStore(cfg.store_dir, encrypt=cfg["storage"]["encrypt"])


def context_from_args(mode: str, paper: str | None, rubric: str | None, role: str | None,
                      participants: str | None) -> str:
    paper_text = load_paper(Path(paper)) if paper else ""
    rubric_text = Path(rubric).read_text(encoding="utf-8") if rubric else ""
    return build_context(mode, paper_text=paper_text, rubric=rubric_text, role=role or "",
                         participants=participants or "")


def process_audio(cfg: Config, store: MeetingStore, audio: Path, mode: str, title: str = "",
                  source: str = "", context: str = "", cleanup: bool = False, log=print) -> str:
    meeting_id = store.new_id(title)
    segments, meta = transcribe(audio, cfg["transcription"], log=log)
    if cfg["diarization"]["enabled"]:
        segments = diarize(audio, segments, cfg["diarization"], log=log)
    transcript = render_transcript(segments)

    record: dict[str, Any] = {
        "id": meeting_id,
        "title": title,
        "mode": mode,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audio_meta": meta,
        "segments": [s.to_dict() for s in segments],
        "transcript": transcript,
        "context": context,
        "insights": None,
    }
    # Save the transcript first so nothing is lost if the analysis step fails.
    store.save(meeting_id, record)
    log(f"Saved transcript as {meeting_id}")

    try:
        record["insights"] = analyze(transcript, mode, cfg["llm"], context=context, log=log)
        store.save(meeting_id, record)
        log("Insights saved.")
    except LLMError as e:
        log(f"Insight generation skipped: {e}\nRun later with: meeting-agent analyze {meeting_id}")

    if cfg["storage"]["keep_audio"]:
        store.encrypt_file(audio, store.root / f"{meeting_id}.audio{audio.suffix}"
                           + (".enc" if store.encrypt else ""))
    if cleanup:
        secure_delete(audio)
        for sidecar in (audio.with_suffix(".wav"),):
            if sidecar != audio and sidecar.exists():
                secure_delete(sidecar)
    return meeting_id
