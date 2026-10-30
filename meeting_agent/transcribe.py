"""Local speech-to-text with faster-whisper, plus optional speaker diarization."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcribe(audio_path: Path, cfg: dict[str, Any], log=print) -> tuple[list[Segment], dict[str, Any]]:
    from faster_whisper import WhisperModel

    device = cfg.get("device", "auto")
    log(f"Loading Whisper model '{cfg['model']}' (downloaded once, then cached locally)...")
    model = WhisperModel(cfg["model"], device=device, compute_type=cfg.get("compute_type", "int8"))
    seg_iter, info = model.transcribe(
        str(audio_path),
        language=cfg.get("language"),
        vad_filter=cfg.get("vad_filter", True),
        beam_size=5,
    )
    segments: list[Segment] = []
    for s in seg_iter:
        text = s.text.strip()
        if text:
            segments.append(Segment(start=s.start, end=s.end, text=text))
            log(f"  [{fmt_ts(s.start)}] {text}")
    meta = {"language": info.language, "duration": info.duration}
    return segments, meta


def diarize(audio_path: Path, segments: list[Segment], cfg: dict[str, Any], log=print) -> list[Segment]:
    """Assign SPEAKER_xx labels using pyannote (runs locally)."""
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        log("Diarization requested but pyannote.audio is not installed: pip install 'meeting-agent[diarize]'")
        return segments
    token = os.environ.get(cfg.get("hf_token_env", "HF_TOKEN"))
    log("Running speaker diarization locally...")
    pipeline = Pipeline.from_pretrained(cfg["model"], use_auth_token=token)
    wav = _ensure_wav(audio_path)
    turns = [(t.start, t.end, spk) for t, _, spk in pipeline(str(wav)).itertracks(yield_label=True)]
    return assign_speakers(segments, turns)


def assign_speakers(segments: list[Segment], turns: list[tuple[float, float, str]]) -> list[Segment]:
    """Label each segment with the speaker whose turns overlap it the most."""
    for seg in segments:
        best, best_overlap = None, 0.0
        for start, end, spk in turns:
            overlap = min(seg.end, end) - max(seg.start, start)
            if overlap > best_overlap:
                best, best_overlap = spk, overlap
        seg.speaker = best
    return segments


def _ensure_wav(audio_path: Path) -> Path:
    if audio_path.suffix.lower() == ".wav":
        return audio_path
    # Decode with PyAV (bundled with faster-whisper) so ffmpeg isn't required.
    from faster_whisper.audio import decode_audio
    import soundfile as sf

    out = audio_path.with_suffix(".wav")
    sf.write(out, decode_audio(str(audio_path), sampling_rate=16000), 16000)
    return out


def render_transcript(segments: list[Segment]) -> str:
    lines = []
    for s in segments:
        who = f"{s.speaker}: " if s.speaker else ""
        lines.append(f"[{fmt_ts(s.start)}] {who}{s.text}")
    return "\n".join(lines)
