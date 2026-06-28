"""
SQLite storage. Stores the clip index, approve/reject status, and — crucially —
the full feature vector for every clip in `features`. That feature log is your
future training set: when you're ready for real ML, you already have the data.
"""
import json
import os
import sqlite3
import threading

from config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id          TEXT PRIMARY KEY,
    created_at  REAL,
    trigger_ts  REAL,
    score       INTEGER,
    primary_tag TEXT,
    tags        TEXT,
    duration    REAL,
    file_path   TEXT,
    status      TEXT,
    reason      TEXT,
    features    TEXT,
    title       TEXT,
    captioned_path TEXT,
    srt_path    TEXT
);
"""

# Applied on init so existing databases gain the newer columns.
MIGRATIONS = [
    "ALTER TABLE clips ADD COLUMN title TEXT",
    "ALTER TABLE clips ADD COLUMN captioned_path TEXT",
    "ALTER TABLE clips ADD COLUMN srt_path TEXT",
]


def _exec(query, params=(), fetch=None, script=False):
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    with _lock:
        conn = sqlite3.connect(settings.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.executescript(query) if script else conn.execute(query, params)
            result = None
            if fetch == "one":
                result = cur.fetchone()
            elif fetch == "all":
                result = cur.fetchall()
            conn.commit()
            return result
        finally:
            conn.close()


def init():
    _exec(SCHEMA, script=True)
    for m in MIGRATIONS:
        try:
            _exec(m)
        except Exception:  # noqa: BLE001  (column already exists)
            pass


def insert_clip(rec: dict):
    _exec(
        """INSERT OR REPLACE INTO clips
           (id, created_at, trigger_ts, score, primary_tag, tags,
            duration, file_path, status, reason, features)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rec["id"], rec["created_at"], rec["trigger_ts"], rec["score"],
            rec["primary_tag"], json.dumps(rec["tags"]), rec["duration"],
            rec["file_path"], rec["status"], rec["reason"],
            json.dumps(rec["features"]),
        ),
    )


def _row(r) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["features"] = json.loads(d.get("features") or "{}")
    return d


def list_clips(limit: int = 300) -> list[dict]:
    rows = _exec("SELECT * FROM clips ORDER BY created_at DESC LIMIT ?", (limit,), fetch="all")
    return [_row(r) for r in (rows or [])]


def get_clip(clip_id: str):
    r = _exec("SELECT * FROM clips WHERE id=?", (clip_id,), fetch="one")
    return _row(r) if r else None


def set_status(clip_id: str, status: str):
    _exec("UPDATE clips SET status=? WHERE id=?", (status, clip_id))


def set_title(clip_id: str, title: str):
    _exec("UPDATE clips SET title=? WHERE id=?", (title, clip_id))


def set_captioned(clip_id: str, captioned_path, srt_path):
    _exec(
        "UPDATE clips SET captioned_path=?, srt_path=? WHERE id=?",
        (captioned_path, srt_path, clip_id),
    )


def delete_clip(clip_id: str):
    """Hard delete (used by 'purge' / empty-bin). Soft delete is set_status('deleted')."""
    _exec("DELETE FROM clips WHERE id=?", (clip_id,))


def stats() -> dict:
    total = (_exec("SELECT COUNT(*) c FROM clips", fetch="one") or {"c": 0})["c"]
    approved = (_exec("SELECT COUNT(*) c FROM clips WHERE status='approved'", fetch="one") or {"c": 0})["c"]
    rejected = (_exec("SELECT COUNT(*) c FROM clips WHERE status='rejected'", fetch="one") or {"c": 0})["c"]
    avg = (_exec("SELECT AVG(score) a FROM clips", fetch="one") or {"a": None})["a"]
    top = _exec(
        "SELECT primary_tag, COUNT(*) c FROM clips GROUP BY primary_tag ORDER BY c DESC LIMIT 1",
        fetch="one",
    )
    return {
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "avg_score": round(avg, 1) if avg else 0,
        "top_trigger": top["primary_tag"] if top else None,
    }
