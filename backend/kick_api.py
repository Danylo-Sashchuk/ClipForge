"""
Talks to Kick's channel API to learn (a) whether the channel is live,
(b) the chatroom id we need for the chat websocket, and (c) the HLS
playback_url for the recorder.

IMPORTANT: Kick puts Cloudflare bot-protection in front of these endpoints.
A plain requests.get() returns 403. curl_cffi impersonates a real Chrome TLS
fingerprint, which gets through. This is the single most common reason a
naive Kick integration fails.
"""
from curl_cffi import requests as crequests

from config import settings

API_V2 = "https://kick.com/api/v2/channels/{slug}"


def get_channel(slug: str | None = None) -> dict:
    slug = slug or settings.CHANNEL
    url = API_V2.format(slug=slug)
    r = crequests.get(url, impersonate="chrome", timeout=20)
    r.raise_for_status()
    return r.json()


def channel_status(slug: str | None = None) -> dict:
    data = get_channel(slug)
    chatroom = data.get("chatroom") or {}
    livestream = data.get("livestream")  # null when offline
    is_live = bool(livestream and livestream.get("is_live"))
    return {
        "slug": data.get("slug"),
        "chatroom_id": chatroom.get("id"),
        "is_live": is_live,
        "title": (livestream or {}).get("session_title"),
        "viewers": (livestream or {}).get("viewer_count"),
        "playback_url": data.get("playback_url"),
    }


if __name__ == "__main__":
    # Quick manual check: python kick_api.py
    import json
    print(json.dumps(channel_status(), indent=2))
