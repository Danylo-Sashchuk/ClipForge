"""
Chat listener. Connects to Kick's PUBLIC Pusher websocket (the same socket the
website uses to render chat) and forwards every chat message to a callback.

No auth needed for reading a public chatroom. We only need the chatroom_id,
which kick_api gives us. Auto-reconnects on drop.

If chat suddenly stops working app-wide, Kick rotated their Pusher app key —
update KICK_PUSHER_APP_KEY in .env (see config.py for how to find it).
"""
import asyncio
import json
import time
from typing import Awaitable, Callable

import websockets

from config import settings

OnMessage = Callable[[float, str, str], Awaitable[None]]


class ChatListener:
    def __init__(self, chatroom_id: int, on_message: OnMessage):
        self.chatroom_id = chatroom_id
        self.on_message = on_message
        self.connected = False
        self._stop = False

    def _url(self) -> str:
        return settings.PUSHER_WS_URL.format(key=settings.PUSHER_APP_KEY)

    async def run(self):
        self._stop = False
        while not self._stop:
            try:
                # ping_interval=None: Pusher uses app-level ping frames, not
                # the websocket library's built-in ping.
                async with websockets.connect(self._url(), ping_interval=None, max_size=None) as ws:
                    await ws.send(json.dumps({
                        "event": "pusher:subscribe",
                        "data": {"channel": f"chatrooms.{self.chatroom_id}.v2"},
                    }))
                    self.connected = True
                    print(f"[chat] connected to chatroom {self.chatroom_id}")
                    async for raw in ws:
                        await self._handle(ws, raw)
            except Exception as e:  # noqa: BLE001
                self.connected = False
                if self._stop:
                    break
                print(f"[chat] disconnected ({e}); reconnecting in 3s")
                await asyncio.sleep(3)
        self.connected = False

    async def _handle(self, ws, raw):
        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        event = frame.get("event", "")

        if event == "pusher:ping":
            await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
            return

        if event.endswith("ChatMessageEvent"):
            data = frame.get("data")
            if isinstance(data, str):           # Pusher nests JSON as a string
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return
            if not isinstance(data, dict):
                return
            content = data.get("content", "")
            username = (data.get("sender") or {}).get("username", "?")
            try:
                await self.on_message(time.time(), username, content)
            except Exception as e:  # noqa: BLE001
                print(f"[chat] handler error: {e}")

    def stop(self):
        self._stop = True
