"""Drive a real Chromium browser into a meeting and record what it hears."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf

from .platforms import PLATFORMS, detect_platform

CAPTURE_JS = (Path(__file__).parent / "capture.js").read_text(encoding="utf-8")


def _chromium_args(silence_wav: Path) -> list[str]:
    return [
        "--use-fake-ui-for-media-stream",          # auto-grant mic/cam prompts
        "--use-fake-device-for-media-stream",      # never touch your real mic/camera
        f"--use-file-for-fake-audio-capture={silence_wav}",  # the bot's "mic" is silence
        "--autoplay-policy=no-user-gesture-required",
        "--disable-blink-features=AutomationControlled",
    ]


def _launch(pw, profile_dir: Path, work_dir: Path, headless: bool):
    silence = work_dir / "silence.wav"
    if not silence.exists():
        sf.write(silence, np.zeros(48000, dtype="float32"), 48000)
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        args=_chromium_args(silence),
        ignore_default_args=["--enable-automation"],
        permissions=["microphone", "camera"],
        viewport={"width": 1280, "height": 800},
    )


def login(profile_dir: Path, work_dir: Path, url: str = "https://accounts.google.com") -> None:
    """Open the bot's browser profile so you can sign in yourself (e.g. a dedicated
    Google account for the notetaker). Credentials never pass through this code."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _launch(pw, profile_dir, work_dir, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        print("Sign in in the browser window, then close it to save the session.")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()


def join_and_record(
    url: str,
    out_dir: Path,
    profile_dir: Path,
    work_dir: Path,
    bot_cfg: dict[str, Any],
    log: Callable[[str], None] = print,
) -> Path | None:
    """Join ``url``, record remote audio until the meeting ends, return the audio path."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    platform_key = detect_platform(url)
    log(f"Platform: {platform_key}")
    files: dict[str, Path] = {}
    handles: dict[str, Any] = {}

    def on_chunk(_source, frame_id: str, b64: str) -> None:
        if frame_id not in handles:
            files[frame_id] = out_dir / f"capture-{frame_id}.webm"
            handles[frame_id] = open(files[frame_id], "ab")
        handles[frame_id].write(base64.b64decode(b64))
        handles[frame_id].flush()

    with sync_playwright() as pw:
        ctx = _launch(pw, profile_dir, work_dir, headless=bot_cfg.get("headless", False))
        plat_cls = PLATFORMS[platform_key]
        ctx.expose_binding("__maChunk", on_chunk)
        if plat_cls.hook_webaudio:
            ctx.add_init_script("window.__maHookWebAudio = true;")
        ctx.add_init_script(CAPTURE_JS)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        plat = plat_cls(page, bot_cfg["name"], log)
        page.goto(plat.prepare_url(url), wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        plat.join()

        log("Waiting to be admitted...")
        if not plat.wait_until_in_call(timeout_s=600):
            log("Not admitted within 10 minutes (or join UI not recognised). Continuing anyway; "
                "press Ctrl+C to stop.")
        else:
            log("In the meeting. Recording.")

        msg = bot_cfg.get("consent_message")
        if msg:
            if plat.post_chat(msg):
                log("Posted recording notice in chat.")
            else:
                log("WARNING: could not post the recording notice in chat. Please tell participants "
                    "they are being recorded.")

        deadline = time.time() + bot_cfg.get("max_minutes", 180) * 60
        silence_limit = bot_cfg.get("leave_after_silence_seconds", 300)
        last_sound = time.time()
        try:
            while time.time() < deadline:
                if page.is_closed():
                    log("Browser closed.")
                    break
                page.wait_for_timeout(2000)
                if plat.has_ended():
                    log("Meeting ended.")
                    break
                level = _max_level(page)
                if level > 0.003:
                    last_sound = time.time()
                elif silence_limit and time.time() - last_sound > silence_limit:
                    log(f"No audio for {silence_limit}s; assuming the meeting is over.")
                    break
            else:
                log("Reached max_minutes.")
        except KeyboardInterrupt:
            log("Stopping (Ctrl+C).")
        finally:
            _stop_all(page)
            if not page.is_closed():
                plat.leave()
            ctx.close()
            for h in handles.values():
                h.close()

    if not files:
        log("No audio was captured.")
        return None
    # Several frames may have recorded; keep the one that got the most audio.
    best = max(files.values(), key=lambda p: p.stat().st_size)
    for p in files.values():
        if p != best:
            p.unlink(missing_ok=True)
    return best


def _frames(page):
    try:
        return [] if page.is_closed() else page.frames
    except Exception:
        return []


def _max_level(page) -> float:
    level = 0.0
    for f in _frames(page):
        try:
            level = max(level, float(f.evaluate("window.__maLevel ? window.__maLevel() : 0")))
        except Exception:
            pass
    return level


def _stop_all(page) -> None:
    for f in _frames(page):
        try:
            f.evaluate("window.__maStop ? window.__maStop() : false")
        except Exception:
            pass
