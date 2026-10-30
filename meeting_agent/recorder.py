"""Record from a local audio device.

Use this for in-person interviews, or as a fallback for any meeting app: route
system audio into a free loopback device (BlackHole on macOS, PulseAudio/PipeWire
monitor on Linux, "Stereo Mix"/VB-Cable on Windows) and record that.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path

import sounddevice as sd
import soundfile as sf


def list_devices() -> str:
    return str(sd.query_devices())


def record(out_path: Path, device: str | int | None = None, samplerate: int = 16000,
           channels: int = 1, max_minutes: float | None = None, log=print) -> Path:
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    q: queue.Queue = queue.Queue()

    def callback(indata, frames, t, status):
        if status:
            log(f"audio: {status}")
        q.put(indata.copy())

    deadline = time.time() + max_minutes * 60 if max_minutes else None
    log(f"Recording to {out_path}. Press Ctrl+C to stop.")
    with sf.SoundFile(out_path, mode="w", samplerate=samplerate, channels=channels, subtype="PCM_16") as f:
        with sd.InputStream(samplerate=samplerate, device=device, channels=channels, callback=callback):
            try:
                while deadline is None or time.time() < deadline:
                    try:
                        f.write(q.get(timeout=0.5))
                    except queue.Empty:
                        pass
            except KeyboardInterrupt:
                log("Stopped.")
        while not q.empty():
            f.write(q.get())
    return out_path
