"""Configuration loading.

Everything defaults to local, offline operation. Config is read from
``~/.meeting-agent/config.yaml`` (or ``$MEETING_AGENT_HOME/config.yaml``) and
merged over the defaults below.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "bot": {
        "name": "Notetaker (meeting-agent)",
        # Posted in the meeting chat when the bot joins, so everyone knows
        # they are being recorded. Set to "" to disable (not recommended).
        "consent_message": (
            "Hi, I'm an automated notetaker. This meeting is being recorded and "
            "transcribed locally for note-taking. Nothing is sent to any cloud "
            "service. Please say so if you'd prefer not to be recorded."
        ),
        "headless": False,
        # auto = installed Google Chrome if present, else Playwright's Chromium.
        "browser": "auto",
        "admit_timeout_minutes": 15,
        "max_minutes": 180,
        # Leave after this many seconds with no incoming audio (0 = never).
        "leave_after_silence_seconds": 300,
    },
    "transcription": {
        "model": "small.en",  # tiny/base/small/medium/large-v3, or *.en variants
        "device": "auto",     # auto / cpu / cuda
        "compute_type": "int8",
        "language": None,     # None = auto-detect
        "vad_filter": True,
    },
    "diarization": {
        "enabled": False,
        "hf_token_env": "HF_TOKEN",
        "model": "pyannote/speaker-diarization-3.1",
    },
    "llm": {
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3.1:8b",
        "temperature": 0.2,
        "num_ctx": 8192,
        "chunk_chars": 10000,
        "timeout_seconds": 600,
        # Refuse to send transcripts to a non-loopback host unless explicitly allowed.
        "allow_remote": False,
    },
    "storage": {
        "encrypt": True,
        "keep_audio": False,
    },
}


def home_dir() -> Path:
    return Path(os.environ.get("MEETING_AGENT_HOME", Path.home() / ".meeting-agent"))


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    home: Path = field(default_factory=home_dir)

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self.data[key]

    @property
    def store_dir(self) -> Path:
        return self.home / "store"

    @property
    def work_dir(self) -> Path:
        return self.home / "work"

    @property
    def browser_profile_dir(self) -> Path:
        return self.home / "browser-profile"

    def ensure_dirs(self) -> None:
        for d in (self.home, self.store_dir, self.work_dir, self.browser_profile_dir):
            d.mkdir(parents=True, exist_ok=True)
            # Owner-only access: transcripts and session cookies live here.
            os.chmod(d, 0o700)


def load_config(path: str | os.PathLike | None = None) -> Config:
    home = home_dir()
    cfg_path = Path(path) if path else home / "config.yaml"
    data = copy.deepcopy(DEFAULTS)
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = _deep_merge(data, yaml.safe_load(f) or {})
    return Config(data=data, home=home)
