import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meeting_agent.config import DEFAULTS
from meeting_agent.insights.analyzer import analyze, chunk_text, clean_report
from meeting_agent.insights.llm import LLMError, OllamaClient, parse_json
from meeting_agent.report import render_markdown
from meeting_agent.storage import MeetingStore, WrongPassphrase
from meeting_agent.transcribe import Segment, assign_speakers, render_transcript
from meeting_agent.bot.platforms import Jitsi, Zoom, detect_platform


def test_store_roundtrip_is_encrypted(tmp_path):
    store = MeetingStore(tmp_path, encrypt=True, passphrase="correct horse")
    store.save("m1", {"transcript": "secret words"})
    raw = (tmp_path / "m1.json.enc").read_bytes()
    assert b"secret" not in raw
    assert (tmp_path / "m1.json.enc").stat().st_mode & 0o077 == 0
    assert MeetingStore(tmp_path, passphrase="correct horse").load("m1")["transcript"] == "secret words"
    with pytest.raises(WrongPassphrase):
        MeetingStore(tmp_path, passphrase="wrong")
    assert store.list() == ["m1"]
    store.delete("m1")
    assert store.list() == []


def test_store_rejects_path_traversal(tmp_path):
    store = MeetingStore(tmp_path, encrypt=False)
    with pytest.raises(ValueError):
        store.load("../etc/passwd")


def test_chunk_text_keeps_lines_whole():
    text = "\n".join(f"[00:{i:02d}] line {i}" for i in range(100))
    chunks = chunk_text(text, 200)
    assert len(chunks) > 1
    assert "\n".join(chunks) == text
    assert all(len(c) <= 200 for c in chunks)


def test_parse_json_tolerates_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(LLMError):
        parse_json("no json here")


def test_refuses_remote_llm():
    cfg = dict(DEFAULTS["llm"], base_url="https://api.example.com")
    with pytest.raises(LLMError):
        OllamaClient(cfg)
    OllamaClient(dict(DEFAULTS["llm"], base_url="http://localhost:11434"))


def test_speaker_assignment():
    segs = [Segment(0, 2, "hi"), Segment(2, 5, "hello there")]
    assign_speakers(segs, [(0, 2.1, "A"), (2.1, 6, "B")])
    assert [s.speaker for s in segs] == ["A", "B"]
    assert render_transcript(segs).splitlines()[1] == "[00:02] B: hello there"


def test_platform_detection_and_urls():
    assert detect_platform("https://meet.google.com/abc-defg-hij") == "meet"
    assert detect_platform("https://us02web.zoom.us/j/123?pwd=x") == "zoom"
    assert detect_platform("https://meet.jit.si/Room") == "jitsi"
    assert detect_platform("https://acme.zoom.us/my/jane") == "zoom"
    assert detect_platform("https://notzoom.us.example.com/j/1") == "generic"
    zoom = Zoom(None, "bot", print)
    assert zoom.prepare_url("https://us02web.zoom.us/j/123?pwd=x") == "https://app.zoom.us/wc/join/123?pwd=x"
    assert zoom.prepare_url("https://app.zoom.us/wc/join/123") == "https://app.zoom.us/wc/join/123"
    assert zoom.prepare_url("https://acme.zoom.us/my/jane") == "https://acme.zoom.us/my/jane"
    assert "userInfo.displayName" in Jitsi(None, "bot", print).prepare_url("https://meet.jit.si/Room")


def test_clean_report_fixes_small_model_quirks():
    rep = clean_report({
        "strengths": ["Clear design explanation", "[00:31]", "00:45"],
        "key_points": ["00:00", "00:06"],
        "competencies": [{"name": "Coding", "rating": 0, "evidence": []},
                         {"name": "Design", "rating": 4, "evidence": ["[00:02] x"]}],
        "action_items": [{"owner": "I", "task": "run MLP ablation"}, {"owner": "Karen", "task": "lit review"}],
    })
    assert rep["strengths"] == ["Clear design explanation [00:31]"]
    assert rep["key_points"] == []
    assert [c["rating"] for c in rep["competencies"]] == [None, 4]
    assert [a["owner"] for a in rep["action_items"]] == [None, "Karen"]


class _FakeOllama(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": "fake:latest"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.calls.append(req)
        user = req["messages"][1]["content"]
        if "<notes>" in user:
            content = {"title": "Interview with Sam", "summary": "Good chat.",
                       "competencies": [{"name": "Coding", "rating": 4, "evidence": ["[00:01] wrote a parser"]}],
                       "recommendation": {"lean": "yes", "rationale": "solid"}}
        else:
            content = {"questions": [{"timestamp": "00:00", "question": "Tell me about yourself"}]}
        self._send({"message": {"content": json.dumps(content)}})


def test_analyze_end_to_end_with_fake_llm():
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = dict(DEFAULTS["llm"], base_url=f"http://127.0.0.1:{server.server_port}",
                   model="fake", chunk_chars=60)
        transcript = "\n".join(f"[00:{i:02d}] sentence number {i}" for i in range(10))
        ins = analyze(transcript, "interview", cfg, log=lambda *_: None)
    finally:
        server.shutdown()
    assert ins["title"] == "Interview with Sam"
    assert ins["_meta"]["chunks"] > 1
    # The transcript must be fenced off as data in every map prompt.
    assert all("<transcript>" in c["messages"][1]["content"] for c in _FakeOllama.calls[:-1])
    md = render_markdown({"id": "x", "mode": "interview", "insights": ins, "transcript": transcript})
    assert "## Competencies" in md and "**Coding**: 4/5" in md
