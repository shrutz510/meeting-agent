"""Command-line interface.

    meeting-agent join  <url> --mode interview|paper|general
    meeting-agent record --device <n> --mode ...
    meeting-agent process <audio-file> --mode ...
    meeting-agent list | show <id> | ask <id> "question" | analyze <id> | delete <id>
    meeting-agent login | devices | doctor
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .config import load_config
from .insights.analyzer import MODES


def _add_context_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", choices=MODES, default="general")
    p.add_argument("--title", default="")
    p.add_argument("--paper", help="PDF or text of the paper being discussed (paper mode)")
    p.add_argument("--rubric", help="Text file listing competencies to evaluate (interview mode)")
    p.add_argument("--role", help="Role being interviewed for (interview mode)")
    p.add_argument("--participants", help="Comma-separated names, helps attribute speakers")


def _session_dir(cfg) -> Path:
    cfg.ensure_dirs()
    d = Path(tempfile.mkdtemp(prefix="session-", dir=cfg.work_dir))
    os.chmod(d, 0o700)
    return d


def cmd_join(args, cfg):
    from .bot.runner import join_and_record
    from .pipeline import context_from_args, open_store, process_audio

    store = open_store(cfg)  # ask for the passphrase up front, not after a 1h meeting
    context = context_from_args(args.mode, args.paper, args.rubric, args.role, args.participants)
    bot_cfg = dict(cfg["bot"])
    if args.headless:
        bot_cfg["headless"] = True
    if args.name:
        bot_cfg["name"] = args.name
    session = _session_dir(cfg)
    audio = join_and_record(args.url, session, cfg.browser_profile_dir, cfg.work_dir, bot_cfg)
    if audio is None:
        shutil.rmtree(session, ignore_errors=True)
        return 1
    mid = process_audio(cfg, store, audio, args.mode, title=args.title, source=args.url,
                        context=context, cleanup=True)
    shutil.rmtree(session, ignore_errors=True)
    print(f"\nDone. View with: meeting-agent show {mid}")


def cmd_record(args, cfg):
    from .pipeline import context_from_args, open_store, process_audio
    from .recorder import record

    store = open_store(cfg)
    context = context_from_args(args.mode, args.paper, args.rubric, args.role, args.participants)
    session = _session_dir(cfg)
    audio = record(session / "recording.wav", device=args.device, max_minutes=args.max_minutes)
    mid = process_audio(cfg, store, audio, args.mode, title=args.title, source="local recording",
                        context=context, cleanup=True)
    shutil.rmtree(session, ignore_errors=True)
    print(f"\nDone. View with: meeting-agent show {mid}")


def cmd_process(args, cfg):
    from .pipeline import context_from_args, open_store, process_audio

    store = open_store(cfg)
    context = context_from_args(args.mode, args.paper, args.rubric, args.role, args.participants)
    mid = process_audio(cfg, store, Path(args.audio), args.mode, title=args.title or Path(args.audio).stem,
                        source=str(args.audio), context=context, cleanup=False)
    print(f"\nDone. View with: meeting-agent show {mid}")


def cmd_analyze(args, cfg):
    from .insights.analyzer import analyze
    from .pipeline import context_from_args, open_store

    store = open_store(cfg)
    rec = store.load(args.id)
    mode = args.mode or rec.get("mode", "general")
    context = rec.get("context", "")
    if args.paper or args.rubric or args.role or args.participants:
        context = context_from_args(mode, args.paper, args.rubric, args.role, args.participants)
    rec["mode"], rec["context"] = mode, context
    rec["insights"] = analyze(rec["transcript"], mode, cfg["llm"], context=context)
    store.save(args.id, rec)
    print(f"Updated insights for {args.id}")


def cmd_list(args, cfg):
    from .pipeline import open_store

    store = open_store(cfg)
    for mid in store.list():
        rec = store.load(mid)
        title = (rec.get("insights") or {}).get("title") or rec.get("title") or ""
        flag = "" if rec.get("insights") else "  (no insights yet)"
        print(f"{mid}  [{rec.get('mode')}]  {title}{flag}")


def cmd_show(args, cfg):
    from .pipeline import open_store
    from .report import render_markdown

    md = render_markdown(open_store(cfg).load(args.id), include_transcript=not args.no_transcript)
    if args.out:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Wrote {args.out} (unencrypted; delete it when you're done)")
    else:
        print(md)


def cmd_ask(args, cfg):
    from .insights.qa import ask
    from .pipeline import open_store

    print(ask(open_store(cfg).load(args.id), args.question, cfg["llm"]))


def cmd_delete(args, cfg):
    from .pipeline import open_store

    if not args.yes and input(f"Permanently delete {args.id}? [y/N] ").lower() != "y":
        return
    open_store(cfg).delete(args.id)
    print("Deleted.")


def cmd_login(args, cfg):
    from .bot.runner import login

    cfg.ensure_dirs()
    login(cfg.browser_profile_dir, cfg.work_dir, args.url)


def cmd_devices(args, cfg):
    from .recorder import list_devices

    print(list_devices())


def cmd_doctor(args, cfg):
    from .insights.llm import LLMError, OllamaClient

    ok = True
    for mod, hint in [("faster_whisper", "pip install faster-whisper"),
                      ("playwright", "pip install playwright && playwright install chromium"),
                      ("cryptography", "pip install cryptography"),
                      ("sounddevice", "pip install sounddevice")]:
        try:
            __import__(mod)
            print(f"[ok]   {mod}")
        except ImportError:
            ok = False
            print(f"[fail] {mod}: {hint}")
    try:
        OllamaClient(cfg["llm"]).check()
        print(f"[ok]   Ollama with model {cfg['llm']['model']}")
    except LLMError as e:
        ok = False
        print(f"[fail] {e}")
    print(f"[info] data directory: {cfg.home}")
    print(f"[info] encryption at rest: {'on' if cfg['storage']['encrypt'] else 'OFF'}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="meeting-agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="Path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("join", help="Join a meeting link, record, transcribe and analyse")
    p.add_argument("url")
    p.add_argument("--name", help="Bot display name")
    p.add_argument("--headless", action="store_true")
    _add_context_args(p)
    p.set_defaults(fn=cmd_join)

    p = sub.add_parser("record", help="Record from a local audio device (in-person or loopback)")
    p.add_argument("--device", help="Device index or name (see `devices`)")
    p.add_argument("--max-minutes", type=float)
    _add_context_args(p)
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("process", help="Transcribe and analyse an existing audio/video file")
    p.add_argument("audio")
    _add_context_args(p)
    p.set_defaults(fn=cmd_process)

    p = sub.add_parser("analyze", help="(Re)generate insights for a stored meeting")
    p.add_argument("id")
    p.add_argument("--mode", choices=MODES)
    for flag in ("--paper", "--rubric", "--role", "--participants"):
        p.add_argument(flag)
    p.set_defaults(fn=cmd_analyze)

    sub.add_parser("list", help="List stored meetings").set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="Print a meeting report as Markdown")
    p.add_argument("id")
    p.add_argument("--no-transcript", action="store_true")
    p.add_argument("--out", help="Write to a file instead of stdout")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("ask", help="Ask a question about a meeting")
    p.add_argument("id")
    p.add_argument("question")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("delete", help="Securely delete a meeting")
    p.add_argument("id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(fn=cmd_delete)

    p = sub.add_parser("login", help="Sign the bot's browser profile into an account (manually)")
    p.add_argument("--url", default="https://accounts.google.com")
    p.set_defaults(fn=cmd_login)

    sub.add_parser("devices", help="List audio input devices").set_defaults(fn=cmd_devices)
    sub.add_parser("doctor", help="Check that local dependencies are ready").set_defaults(fn=cmd_doctor)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    try:
        return args.fn(args, cfg) or 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # keep tracebacks out of the way unless asked
        if os.environ.get("MEETING_AGENT_DEBUG"):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
