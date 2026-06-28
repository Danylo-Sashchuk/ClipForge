"""
FastAPI server. Exposes:
  GET  /                       -> the dashboard (served HTML, no build step)
  GET  /api/status             -> current status + stats
  GET  /api/clips              -> all clips
  POST /api/clips/{id}/{action}-> approve | reject | good | bad | delete
  WS   /ws                     -> real-time event stream to the dashboard
  GET  /clips/...              -> the actual clip files (video playback)
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from config import settings
from orchestrator import Orchestrator

# Directories must exist before we mount StaticFiles.
os.makedirs(settings.CLIPS_DIR, exist_ok=True)
os.makedirs(settings.SEGMENTS_DIR, exist_ok=True)
os.makedirs(settings.DATA_DIR, exist_ok=True)

clients: set[WebSocket] = set()


async def broadcast(msg: dict):
    data = json.dumps(msg, default=str)
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


orch = Orchestrator(broadcast)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await orch.start()
    yield
    orch.recorder.stop()


app = FastAPI(title="Clip Automation MVP", lifespan=lifespan)
app.mount("/clips", StaticFiles(directory=settings.CLIPS_DIR), name="clips")


def _clip_payload(c: dict) -> dict:
    rel = os.path.relpath(c["file_path"], settings.CLIPS_DIR).replace(os.sep, "/")
    payload = {
        "id": c["id"],
        "url": f"/clips/{rel}",
        "trigger": c["primary_tag"],
        "tags": c["tags"],
        "score": c["score"],
        "duration": c["duration"],
        "status": c["status"],
        "reason": c["reason"],
        "created_at": c["created_at"],
        "title": c.get("title") or "",
        "has_captions": bool(c.get("captioned_path")),
    }
    if c.get("captioned_path"):
        rc = os.path.relpath(c["captioned_path"], settings.CLIPS_DIR).replace(os.sep, "/")
        payload["captioned_url"] = f"/clips/{rc}"
    if c.get("srt_path"):
        rs = os.path.relpath(c["srt_path"], settings.CLIPS_DIR).replace(os.sep, "/")
        payload["srt_url"] = f"/clips/{rs}"
    return payload


@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(settings.BASE_DIR, "static", "index.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


@app.get("/api/status")
async def api_status():
    return orch.public_status()


@app.get("/api/clips")
async def api_clips():
    return [_clip_payload(c) for c in db.list_clips()]


class TitleIn(BaseModel):
    title: str = ""


@app.post("/api/clips/{clip_id}/{action}")
async def api_action(clip_id: str, action: str):
    if action == "purge":  # permanent delete (from the bin)
        c = db.get_clip(clip_id)
        if c:
            for p in (c.get("file_path"), c.get("captioned_path"), c.get("srt_path")):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        db.delete_clip(clip_id)
        await broadcast({"event": "clip-purged", "id": clip_id})
        return {"ok": True}

    if action == "captions":  # transcribe + (optionally) burn subtitles
        c = db.get_clip(clip_id)
        if not c:
            return JSONResponse({"error": "not found"}, status_code=404)
        asyncio.create_task(orch.generate_captions(clip_id))
        return {"ok": True, "started": True}

    mapping = {
        "approve": "approved",
        "reject": "rejected",
        "delete": "deleted",   # soft delete -> bin
        "restore": "pending",
    }
    if action not in mapping:
        return JSONResponse({"error": "unknown action"}, status_code=400)
    db.set_status(clip_id, mapping[action])
    await broadcast({"event": "clip-updated", "id": clip_id, "status": mapping[action]})
    return {"ok": True}


@app.put("/api/clips/{clip_id}/title")
async def api_title(clip_id: str, body: TitleIn):
    title = body.title.strip()
    db.set_title(clip_id, title)
    await broadcast({"event": "clip-updated", "id": clip_id, "title": title})
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps(
            {"event": "stream-status", "status": orch.public_status()}, default=str))
        while True:
            await ws.receive_text()  # we don't expect client messages; keeps it open
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        clients.discard(ws)
