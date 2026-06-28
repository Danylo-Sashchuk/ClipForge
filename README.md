# Live Clip Automation — MVP

Watches a **live Kick stream**, detects viral moments from **real-time chat reactions**,
and auto-cuts clips with FFmpeg into a live dashboard where you approve / reject them.

This is the wedge: tools like Opus Clip work on VODs and have to *guess* what will go
viral. A live tool *measures* the crowd reacting in real time — that crowd reaction is
free ground-truth that a VOD tool structurally can't get. Every clip's signals are logged
so you can train a real model on them later.

---

## What it does (the loop)

1. Polls Kick to detect when your channel goes live.
2. Continuously records the stream to disk as rolling ~4s segments (your buffer).
3. Connects to Kick chat (public Pusher websocket) in real time.
4. A detector scores chat every message on **spike** (chat acceleration),
   **homogeneity** (everyone saying the same thing), and **keywords** (light tagging).
5. When the score crosses the threshold, it cuts a clip from the recorded segments —
   anchored **before** the chat spike (chat lags the real moment) — and shows it instantly.
6. You approve / reject / rate each clip. Those labels + the signal data become your dataset.

---

## Prerequisites (Windows)

You need three things installed and on your PATH:

1. **Python 3.11+** — https://www.python.org/downloads/
   During install, **check "Add python.exe to PATH"**.
   Verify in a new terminal: `python --version`

2. **FFmpeg** — https://www.gyan.dev/ffmpeg/builds/ (grab "release full")
   Unzip it, then add its `bin` folder to your PATH (or set `FFMPEG_BIN` in `.env`).
   Verify: `ffmpeg -version`

3. **Streamlink** — installed automatically by `pip install -r requirements.txt`.
   Verify: `streamlink --version`

> **Node.js is NOT required.** The dashboard is a single served HTML page so you can
> see it working today. A polished React frontend is a later upgrade, not a dependency.

A modern browser (Chrome/Edge/Firefox) for the dashboard.

---

## Install & run

```bat
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
:: open .env and set KICK_CHANNEL_USERNAME to the channel you want
python run.py
```

Then open **http://127.0.0.1:3001**

When the channel is live, the status dots go green, the chat-intensity meter moves,
and clips appear the instant a moment fires. When the channel is offline it just waits.

---

## How it works (files)

```
backend/
  config.py        all tunable settings (read from .env)
  kick_api.py      channel status + chatroom id (curl_cffi beats Cloudflare)
  recorder.py      continuous stream -> timestamped .ts segments (the buffer)
  chat.py          Kick chat via public Pusher websocket
  scoring.py       the detector (spike + homogeneity + keyword tags -> 0..100)
  clipper.py       cut a clip from segments with ffmpeg (concat + seek)
  db.py            SQLite: clip index + status + feature vectors (future ML)
  orchestrator.py  the brain: wires status/chat/detector/clip-queue together
  server.py        FastAPI: REST + websocket + serves clips & dashboard
  static/index.html  the dashboard
  run.py           entry point
```

**Why record-to-disk instead of an in-memory buffer:** it's crash-safe, avoids Windows
file locks on the actively-written file (clips read *finished* segments), and the exact
same pipeline reuses for the future VOD / podcast mode.

**The training path is built in:** `db.clips.features` stores the full signal vector for
every clip. Today the label is your approve/reject. Later you swap that label for real
posted-clip performance (views/retention) and train a model — no re-architecting.

---

## Top tuning knobs (in `.env`)

You will tune these against a real stream — defaults are deliberately conservative
(fewer, higher-quality clips) so you can loosen from there.

| Setting | Does what | If clips are… |
|---|---|---|
| `MIN_TRIGGER_SCORE` (75) | firing threshold | too few → lower (70); too many junk → raise (80) |
| `LATENCY_OFFSET_SECONDS` (10) | how far chat lags the moment | clip starts too late → raise; too early → lower |
| `PRE_PAD_SECONDS` (22) / `POST_PAD_SECONDS` (8) | clip shape around the moment | missing the setup → raise PRE |
| `TRIGGER_COOLDOWN_SECONDS` (30) | min gap between clips | duplicate clips → raise |
| `MIN_MESSAGES_IN_WINDOW` (8) | activity floor | small channel missing moments → lower |
| `WEIGHT_SPIKE / HOMOGENEITY / KEYWORD` | signal mix (sum ~1.0) | bias toward what works for your streamer |

---

## Troubleshooting

- **403 / can't read channel** → handled by `curl_cffi`. If it still fails, update it:
  `pip install -U curl_cffi`.
- **Chat dot never turns green** → Kick rotated their Pusher app key. Open kick.com,
  F12 → Network → filter `pusher`, copy the app id from the `wss://` URL into
  `KICK_PUSHER_APP_KEY` in `.env`.
- **"ffmpeg/streamlink not found"** → not on PATH. Set `FFMPEG_BIN` / `STREAMLINK_BIN`
  to the full `.exe` path in `.env`.
- **"No recorded segments cover this moment"** early on → normal in the first ~30s of a
  stream before enough buffer exists. It self-resolves.
- **Recorder won't start** → flip `RECORDER_BACKEND` between `streamlink` and `ffmpeg`.

---

## What's verified vs. not

- ✅ All modules compile; server boots; DB initializes.
- ✅ The detector is tested on simulated chat: silent on calm chat, fires correctly on a
  reaction burst (right tags, right clip length), cooldown suppresses duplicates.
- ⚠️ **Live capture against Kick is untested here** (no live stream / Kick network access
  in the build sandbox). The Kick API, recorder, and chat paths are written to spec and
  will need a first run against a real live channel — expect to adjust `STREAM_QUALITY`,
  the Pusher key if rotated, and the timing knobs.

---

## Next steps (roadmap)

1. Run against a live `deenthegreat` stream; tune timing + threshold.
2. Add 9:16 auto-reframe + animated captions (Whisper) to compete on polish.
3. Add social auto-posting → closes the loop on *real* performance data.
4. Train a model on the logged feature vectors (heuristics → logistic/XGBoost → multimodal).
5. Upgrade the dashboard to React; add multi-streamer support.

## Deploy later

For a server deployment, containerize with Docker (Python + ffmpeg + streamlink base
image), run the same `run.py`, and put it behind a reverse proxy. Local Windows is the
fastest path to your first paying streamer — ship that first.
