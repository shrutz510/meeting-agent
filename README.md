# meeting-agent

An open-source agent that joins your meetings, transcribes them, and turns them into
structured notes. It is built for two kinds of meetings in particular:

- **Interviews**: question-by-question notes, competency ratings with timestamped
  evidence, strengths, concerns, claims worth verifying, follow-up questions, and
  feedback for the interviewer.
- **Paper discussions** (reading groups, lab meetings, reviews): the paper's core claim,
  key points, critiques and who raised them, disagreements, open questions, related work,
  research ideas, action items. If you give it the PDF, it also checks what was said
  against what the paper actually says.

It costs nothing to run and nothing leaves your machine.

| Piece | Tool | Cost | Where it runs |
|---|---|---|---|
| Joining the meeting | Chromium via [Playwright](https://playwright.dev) | free, Apache-2.0 | your machine |
| Speech-to-text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (OpenAI Whisper weights, MIT) | free | your machine |
| Speaker labels (optional) | [pyannote.audio](https://github.com/pyannote/pyannote-audio) | free (HF token to download weights) | your machine |
| Insights | [Ollama](https://ollama.com) + any open model (Llama, Qwen, Mistral...) | free | your machine |
| Storage | Fernet encryption (AES + HMAC), scrypt key derivation | free | your disk |

There are no paid APIs, no meeting-bot SaaS, and no cloud transcription.

## How it works

```
meeting link ──► Chromium bot joins as a muted guest, posts a recording notice in chat
                  │  an injected script taps the audio the browser *receives* from
                  │  other participants (WebRTC / WebAudio) and records it locally
                  ▼
           audio file (private temp dir) ──► faster-whisper ──► transcript with timestamps
                                                                    │
                       (optional) pyannote speaker labels ◄─────────┤
                                                                    ▼
                         local LLM: map (notes per chunk) → reduce (structured report)
                                                                    ▼
                       encrypted record in ~/.meeting-agent/store; temp audio is wiped
```

Because audio is captured inside the bot's browser, you don't need a virtual audio
driver, and the bot's fake microphone only ever sends silence. For apps the bot can't
join, or in-person interviews, `record` captures a local device instead.

## Install

Requires Python 3.10+.

```bash
git clone <this repo> meeting-agent && cd meeting-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # Intel Mac: pip install --only-binary cryptography -e .
playwright install chromium

# Local LLM (https://ollama.com/download), then:
ollama pull llama3.1:8b

meeting-agent doctor
```

The Whisper model is downloaded once on first use and cached. After that,
everything works offline.

## Use

```bash
# Interview: give it the role and, optionally, a rubric
meeting-agent join "https://meet.google.com/abc-defg-hij" --mode interview \
    --title "Backend eng - Sam" --role "Senior Backend Engineer" \
    --rubric examples/rubric-backend.txt --participants "Shruti (interviewer), Sam (candidate)"

# Paper discussion: give it the paper
meeting-agent join "https://meet.jit.si/MyReadingGroup" --mode paper \
    --title "Attention Is All You Need" --paper ~/papers/attention.pdf

# Already have a recording? (any audio/video format)
meeting-agent process interview.m4a --mode interview

# In-person, or any app via a loopback device
meeting-agent devices
meeting-agent record --device 3 --mode paper --paper paper.pdf

# Review
meeting-agent list
meeting-agent show 20261029-150000-backend-eng-sam
meeting-agent show <id> --no-transcript --out notes.md
meeting-agent ask <id> "What did the candidate say about scaling the queue?"
meeting-agent ask <id> "Who disagreed with the ablation results, and why?"
meeting-agent analyze <id> --mode paper --paper paper.pdf   # re-run with new context
meeting-agent delete <id>
```

Set `MEETING_AGENT_PASSPHRASE` to skip the passphrase prompt (e.g. in a script).
Otherwise you're asked once per command. The passphrase is never stored.
**If you lose it, the store can't be decrypted.**

### Supported platforms

| Platform | Status | Notes |
|---|---|---|
| Jitsi Meet (incl. self-hosted) | best | Fully open source. Self-host it for end-to-end control. |
| Google Meet | good | Run `meeting-agent login` once and sign the bot's browser profile into a Google account (ideally a dedicated one). The host has to admit the bot. |
| Zoom (web client) | experimental | Zoom links are rewritten to the browser client. The host must allow web client joins. |
| Microsoft Teams (web) | experimental | Guest join through the browser. |
| Anything else | fallback | Use `record` with a loopback device (below). |

Meeting UIs change often. If the bot can't find a button, it says so and waits.
The browser window is visible by default, so you can click it yourself.

**Loopback capture (fallback):** on macOS install [BlackHole](https://existential.audio/blackhole/)
(free, open source) and create a Multi-Output Device in Audio MIDI Setup. On Linux use the
PulseAudio/PipeWire "Monitor" source. On Windows use "Stereo Mix" or VB-Cable.

### Speaker labels

```bash
pip install -e '.[diarize]'
export HF_TOKEN=...   # free read token; accept the pyannote/speaker-diarization-3.1 terms once
```

Then set `diarization.enabled: true` in the config. Without diarization the transcript
has timestamps but no speaker names, and the LLM works out who's speaking from context
(passing `--participants` helps).

## Configuration

Copy `config.example.yaml` to `~/.meeting-agent/config.yaml` and edit it. The settings
you're most likely to change:

- `transcription.model`: `small.en` is a good default on a laptop CPU. Use `medium.en`
  or `large-v3` for accuracy, and `large-v3` for non-English meetings.
- `llm.model`: any Ollama model. Bigger models give better insights. `qwen2.5:14b` or
  `llama3.1:70b` work well if your hardware can run them.
- `bot.consent_message`: posted in chat when the bot joins.

## Security and privacy

- **Local only.** Audio, transcripts and analysis never leave your machine. The LLM
  client refuses any non-loopback endpoint unless you set `llm.allow_remote: true`.
- **Encrypted at rest.** Each meeting is stored as one Fernet-encrypted file, with the
  key derived from your passphrase via scrypt. Files and directories are owner-only
  (`0600`/`0700`).
- **No lingering plaintext.** Raw audio goes to a private temp directory and is
  overwritten and deleted after transcription, unless `storage.keep_audio: true`, in
  which case it's stored encrypted. On SSDs, overwriting is best-effort, so keep full-disk
  encryption (FileVault, LUKS, BitLocker) on.
- **Isolated bot.** The bot uses its own browser profile (`~/.meeting-agent/browser-profile`)
  and fake media devices. It never touches your real camera, microphone or browser
  cookies. Signing in happens by hand in that window; the code never handles passwords.
- **Prompt-injection hardening.** Transcripts are treated as untrusted data. Prompts fence
  them off and tell the model to ignore instructions spoken in the meeting. The LLM has
  no tools and can't take any actions.
- **Minimal dependencies**, all open source. The Ollama client uses only the Python
  standard library.

## Consent and law

Recording people without consent is illegal in many places: several US states, the EU
(GDPR) and others require **all-party consent**. The bot posts a notice in chat when it
joins. Still tell people out loud, and stop if anyone objects. For interviews, tell
candidates in advance. Use the generated recommendation as one input for a human
decision, never as the decision itself. The interview prompt tells the model to ignore
protected characteristics, but you should still review its output for bias.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Layout:

```
meeting_agent/
  bot/capture.js      in-page audio tap (WebRTC + WebAudio → MediaRecorder)
  bot/platforms.py    join flows for Meet / Jitsi / Zoom / Teams
  bot/runner.py       Playwright driver, consent notice, end-of-meeting detection
  recorder.py         local device / loopback recording
  transcribe.py       faster-whisper + optional pyannote
  insights/           Ollama client, prompts, map-reduce analyser, Q&A
  storage.py          encrypted store
  report.py           Markdown rendering
  cli.py              command line
```

Contributions welcome, especially join-flow fixes when meeting UIs change.

## License

MIT
