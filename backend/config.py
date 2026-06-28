"""
Central configuration. Everything tunable lives here and can be overridden
in a .env file (copy .env.example -> .env). Sensible defaults are baked in so
the app runs even with an empty .env.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _str(name, default):
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name, default):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


_HERE = os.path.abspath(os.path.dirname(__file__))


class Settings:
    # --- server ---
    HOST = _str("HOST", "127.0.0.1")
    PORT = _int("PORT", 3001)

    # --- kick connection ---
    CHANNEL = _str("KICK_CHANNEL_USERNAME", "deenthegreat")
    # Kick's public Pusher app key (the same one the website uses). If chat
    # ever stops connecting, Kick changed this -> open kick.com, F12 -> Network
    # -> filter "pusher", copy the new app id from the wss URL.
    PUSHER_APP_KEY = _str("KICK_PUSHER_APP_KEY", "32cbd69e4b950bf97679")
    PUSHER_WS_URL = _str(
        "KICK_PUSHER_WS_URL",
        "wss://ws-us2.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0-rc2&flash=false",
    )
    STATUS_POLL_SECONDS = _int("STATUS_POLL_SECONDS", 20)

    # --- paths ---
    BASE_DIR = _HERE
    DATA_DIR = _str("DATA_DIR", os.path.join(_HERE, "data"))
    CLIPS_DIR = _str("CLIPS_DIR", os.path.join(_HERE, "clips"))
    SEGMENTS_DIR = _str("SEGMENTS_DIR", os.path.join(_HERE, "segments"))
    DB_PATH = _str("DATABASE_PATH", os.path.join(DATA_DIR, "app.sqlite"))

    # --- recorder ---
    # "streamlink" = streamlink pulls the HLS stream and pipes it to ffmpeg
    #                (most reliable for Kick, handles their headers/plugin).
    # "ffmpeg"     = ffmpeg reads the playback_url (m3u8) directly. Simpler,
    #                but more likely to hit Kick edge protection. Try this only
    #                if streamlink misbehaves.
    RECORDER_BACKEND = _str("RECORDER_BACKEND", "streamlink")
    STREAM_QUALITY = _str("STREAM_QUALITY", "720p60,720p,best")
    SEGMENT_SECONDS = _int("SEGMENT_SECONDS", 4)
    # How much rolling history to keep on disk. Must comfortably exceed your
    # largest possible pre-buffer so a clip always has source footage.
    SEGMENT_RETENTION_SECONDS = _int("SEGMENT_RETENTION_SECONDS", 600)
    FFMPEG_BIN = _str("FFMPEG_BIN", "ffmpeg")
    STREAMLINK_BIN = _str("STREAMLINK_BIN", "streamlink")

    # --- clip timing (the part most live-clippers get wrong) ---
    # Chat reacts AFTER the moment because of stream delivery latency + human
    # reaction/typing time. So the real moment is ~LATENCY_OFFSET seconds before
    # the chat spike, and we weight the clip heavily BEFORE the spike.
    LATENCY_OFFSET = _float("LATENCY_OFFSET_SECONDS", 10.0)
    PRE_PAD = _float("PRE_PAD_SECONDS", 22.0)   # seconds of run-up before the moment

    # Dynamic clip end: keep the clip open until the chat reaction dies down,
    # then add a small tail. The on-stream moment is "over" when chat calms.
    DYNAMIC_END = _bool("DYNAMIC_END", True)
    END_SCORE_THRESHOLD = _int("END_SCORE_THRESHOLD", 35)   # chat considered calm below this
    END_HOLD_SECONDS = _float("END_HOLD_SECONDS", 4.0)      # must stay calm this long to end
    TAIL_OFFSET = _float("TAIL_OFFSET_SECONDS", 3.0)        # the "little bit after" you asked for
    MIN_CLIP_SECONDS = _int("MIN_CLIP_SECONDS", 12)         # never cut a tiny clip
    MAX_CLIP_SECONDS = _int("MAX_CLIP_SECONDS", 90)         # hard cap on a long hype train

    # Used only when DYNAMIC_END=false (fixed-length fallback).
    POST_PAD = _float("POST_PAD_SECONDS", 8.0)

    # --- detection engine ---
    SHORT_WINDOW = _float("SHORT_WINDOW_SECONDS", 5.0)   # "right now"
    LONG_WINDOW = _float("LONG_WINDOW_SECONDS", 60.0)    # baseline reference
    MIN_MESSAGES = _int("MIN_MESSAGES_IN_WINDOW", 8)     # absolute activity floor
    MIN_BASELINE_VEL = _float("MIN_BASELINE_VELOCITY", 0.3)  # msgs/sec floor (avoids div-by-zero spikes)
    SPIKE_RATIO_FOR_MAX = _float("SPIKE_RATIO_FOR_MAX", 4.0)  # 4x baseline = full spike score
    HOMO_FOR_MAX = _float("HOMOGENEITY_FOR_MAX", 0.5)        # 50% same token = full homogeneity score
    KW_FOR_MAX = _int("KEYWORD_MATCHES_FOR_MAX", 8)
    WEIGHT_SPIKE = _float("WEIGHT_SPIKE", 0.5)
    WEIGHT_HOMO = _float("WEIGHT_HOMOGENEITY", 0.3)
    WEIGHT_KW = _float("WEIGHT_KEYWORD", 0.2)
    MIN_TRIGGER_SCORE = _int("MIN_TRIGGER_SCORE", 75)
    COOLDOWN_SECONDS = _float("TRIGGER_COOLDOWN_SECONDS", 30.0)

    # --- subtitles / captions (approved clips) ---
    # faster-whisper model: tiny | base | small | medium. base is a good
    # speed/quality tradeoff on CPU. Model auto-downloads on first use.
    WHISPER_MODEL = _str("WHISPER_MODEL", "base")
    WHISPER_DEVICE = _str("WHISPER_DEVICE", "cpu")          # cpu | cuda
    WHISPER_COMPUTE = _str("WHISPER_COMPUTE_TYPE", "int8")  # int8 is CPU-friendly
    # Burn the subtitles into a separate *_captioned.mp4 (keeps the clean original).
    BURN_SUBTITLES = _bool("BURN_SUBTITLES", True)
    SUBTITLE_FONT_SIZE = _int("SUBTITLE_FONT_SIZE", 18)


settings = Settings()
