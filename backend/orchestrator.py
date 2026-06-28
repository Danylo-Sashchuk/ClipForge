"""
Orchestrator — the brain. Wires everything together:

  status poller  -> detects live/offline, starts/stops capture
  chat listener  -> feeds the detector
  detector       -> fires viral-moment triggers
  clip queue     -> ffmpeg renders clips OFF the event loop (in a thread)
  broadcast      -> pushes events to all dashboard websocket clients

The clip job intentionally WAITS until the post-buffer footage has been
recorded before cutting, and anchors the clip BEFORE the chat spike to correct
for stream + reaction latency.
"""
import asyncio
import os
import time
from datetime import datetime

import db
import kick_api
import captions
from chat import ChatListener
from clipper import make_clip
from config import settings
from recorder import Recorder
from scoring import Detector, reason_text


class Orchestrator:
    def __init__(self, broadcast):
        self.broadcast = broadcast  # async fn(dict)
        self.detector = Detector()
        self.recorder = Recorder()
        self.chat: ChatListener | None = None
        self._chat_task: asyncio.Task | None = None
        self.clip_queue: asyncio.Queue = asyncio.Queue()
        self._caption_lock = asyncio.Lock()  # serialize transcription (CPU heavy)
        self.status = {
            "channel": settings.CHANNEL,
            "is_live": False,
            "chat_connected": False,
            "chatroom_id": None,
            "title": None,
            "viewers": None,
            "clips_today": 0,
            "started_at": time.time(),
            "chat_velocity": 0.0,
            "live_score": 0,
        }

    async def start(self):
        db.init()
        asyncio.create_task(self._poll_status())
        asyncio.create_task(self._clip_worker())
        asyncio.create_task(self._ticker())

    # ---- chat -> detector ----
    async def on_message(self, ts: float, user: str, text: str):
        self.detector.add(ts, text)
        res = self.detector.evaluate(ts)
        if res["fired"]:
            await self._on_trigger(res)

    async def _on_trigger(self, res: dict):
        await self.broadcast({
            "event": "trigger-fired",
            "trigger": res["primary_tag"],
            "tags": res["tags"],
            "score": res["score"],
            "timestamp": res["ts"],
            "reason": reason_text(res),
        })
        await self.clip_queue.put(res)

    # ---- clip rendering ----
    async def _clip_worker(self):
        while True:
            res = await self.clip_queue.get()
            try:
                await self._make_clip(res)
            except Exception as e:  # noqa: BLE001
                await self.broadcast({"event": "clip-failed", "error": str(e)})
                print(f"[clip] failed: {e}")
            finally:
                self.clip_queue.task_done()

    async def _watch_moment_end(self, trigger_ts: float) -> float:
        """Watch chat after a trigger and return the chat-time at which the
        reaction died down (and stayed down for END_HOLD_SECONDS). Falls back
        to a hard cap so a never-ending hype train still gets cut.

        If chat re-erupts before calming, calm_since resets and the clip
        naturally extends to cover the whole sustained moment."""
        calm_since = None
        hard_cap = trigger_ts + settings.MAX_CLIP_SECONDS + settings.LATENCY_OFFSET
        while True:
            now = time.time()
            snap = self.detector.snapshot(now)
            if snap["score"] <= settings.END_SCORE_THRESHOLD:
                if calm_since is None:
                    calm_since = now
                if now - calm_since >= settings.END_HOLD_SECONDS:
                    return calm_since
            else:
                calm_since = None  # re-energized -> keep the clip rolling
            if now >= hard_cap:
                return now
            await asyncio.sleep(1)

    async def _make_clip(self, res: dict):
        score = res["score"]
        trigger_ts = res["ts"]
        center = trigger_ts - settings.LATENCY_OFFSET

        # Start is fixed: we can't see the lead-up in chat (it's quiet before
        # the moment), so we always grab PRE_PAD seconds of run-up.
        t0 = center - settings.PRE_PAD

        if settings.DYNAMIC_END:
            # End when the reaction dies down. Chat's calm-point lags the real
            # on-stream end by ~LATENCY_OFFSET, so correct for it, then add tail.
            chat_end = await self._watch_moment_end(trigger_ts)
            t1 = (chat_end - settings.LATENCY_OFFSET) + settings.TAIL_OFFSET
        else:
            t1 = center + settings.POST_PAD

        # Clamp to sane bounds.
        length = t1 - t0
        if length < settings.MIN_CLIP_SECONDS:
            t1 = t0 + settings.MIN_CLIP_SECONDS
        elif length > settings.MAX_CLIP_SECONDS:
            t1 = t0 + settings.MAX_CLIP_SECONDS
        duration = round(t1 - t0)

        # Make sure the tail footage is on disk before cutting.
        wait = (t1 + 2) - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

        dt = datetime.fromtimestamp(trigger_ts)
        date = dt.strftime("%Y-%m-%d")
        clip_id = f"clip_{dt.strftime('%H%M%S')}_{res['primary_tag']}_{duration}s"
        out_dir = os.path.join(settings.CLIPS_DIR, date)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, clip_id + ".mp4")

        await self.broadcast({"event": "clip-generating", "id": clip_id})

        # ffmpeg is blocking -> run it in a thread so the event loop stays responsive.
        await asyncio.get_running_loop().run_in_executor(None, make_clip, t0, t1, out_path)

        rel = os.path.relpath(out_path, settings.CLIPS_DIR).replace(os.sep, "/")
        rec = {
            "id": clip_id,
            "created_at": time.time(),
            "trigger_ts": res["ts"],
            "score": score,
            "primary_tag": res["primary_tag"],
            "tags": res["tags"],
            "duration": duration,
            "file_path": out_path,
            "status": "pending",
            "reason": reason_text(res),
            "features": res["features"],
        }
        db.insert_clip(rec)
        self.status["clips_today"] += 1

        await self.broadcast({
            "event": "clip-created",
            "clip": {
                "id": clip_id,
                "url": f"/clips/{rel}",
                "trigger": res["primary_tag"],
                "tags": res["tags"],
                "score": score,
                "duration": duration,
                "status": "pending",
                "reason": rec["reason"],
                "created_at": rec["created_at"],
                "title": "",
                "has_captions": False,
            },
        })
        print(f"[clip] created {clip_id} (score {score})")

    # ---- captions (approved clips) ----
    async def generate_captions(self, clip_id: str):
        c = db.get_clip(clip_id)
        if not c:
            await self.broadcast({"event": "captions-failed", "id": clip_id, "error": "clip not found"})
            return
        src = c["file_path"]
        if not src or not os.path.exists(src):
            await self.broadcast({"event": "captions-failed", "id": clip_id, "error": "clip file missing"})
            return

        async with self._caption_lock:
            loop = asyncio.get_running_loop()
            srt_path = os.path.splitext(src)[0] + ".srt"
            captioned = os.path.splitext(src)[0] + "_captioned.mp4"
            try:
                await self.broadcast({"event": "captioning", "id": clip_id, "stage": "transcribing"})
                await loop.run_in_executor(None, captions.transcribe_to_srt, src, srt_path)

                if settings.BURN_SUBTITLES:
                    await self.broadcast({"event": "captioning", "id": clip_id, "stage": "burning"})
                    await loop.run_in_executor(None, captions.burn_subtitles, src, srt_path, captioned)
                else:
                    captioned = None

                db.set_captioned(clip_id, captioned, srt_path)
            except Exception as e:  # noqa: BLE001
                await self.broadcast({"event": "captions-failed", "id": clip_id, "error": str(e)})
                print(f"[captions] failed for {clip_id}: {e}")
                return

        payload = {
            "event": "captioned-ready",
            "id": clip_id,
            "srt_url": f"/clips/{os.path.relpath(srt_path, settings.CLIPS_DIR).replace(os.sep, '/')}",
        }
        if captioned:
            payload["captioned_url"] = f"/clips/{os.path.relpath(captioned, settings.CLIPS_DIR).replace(os.sep, '/')}"
        await self.broadcast(payload)
        print(f"[captions] ready for {clip_id}")

    # ---- live status ----
    async def _poll_status(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                st = await loop.run_in_executor(None, kick_api.channel_status)
                was_live = self.status["is_live"]
                self.status.update({
                    "is_live": st["is_live"],
                    "chatroom_id": st["chatroom_id"],
                    "title": st["title"],
                    "viewers": st["viewers"],
                })
                if st["is_live"] and not was_live:
                    await self._go_live(st)
                elif not st["is_live"] and was_live:
                    await self._go_offline()
            except Exception as e:  # noqa: BLE001
                await self.broadcast({"event": "error", "where": "status", "msg": str(e)})

            self.status["chat_connected"] = bool(self.chat and self.chat.connected)
            await self.broadcast({"event": "stream-status", "status": self.public_status()})
            await asyncio.sleep(settings.STATUS_POLL_SECONDS)

    async def _go_live(self, st: dict):
        await self.broadcast({"event": "info", "msg": f"{settings.CHANNEL} is LIVE — starting capture"})
        self.recorder.start(playback_url=st.get("playback_url"))
        if st.get("chatroom_id"):
            self.chat = ChatListener(st["chatroom_id"], self.on_message)
            self._chat_task = asyncio.create_task(self.chat.run())

    async def _go_offline(self):
        await self.broadcast({"event": "info", "msg": f"{settings.CHANNEL} went offline — stopping capture"})
        self.recorder.stop()
        if self.chat:
            self.chat.stop()
        if self._chat_task:
            self._chat_task.cancel()
        self.chat = None

    # ---- live meter for the dashboard ----
    async def _ticker(self):
        while True:
            snap = self.detector.snapshot()
            self.status["chat_velocity"] = snap["features"].get("velocity", 0.0)
            self.status["live_score"] = snap["score"]
            await self.broadcast({
                "event": "chat-tick",
                "velocity": self.status["chat_velocity"],
                "score": snap["score"],
            })
            await asyncio.sleep(2)

    def public_status(self) -> dict:
        s = dict(self.status)
        s.update(db.stats())
        return s
