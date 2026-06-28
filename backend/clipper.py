"""
The clipper. Given a wall-clock time range [t0, t1], find the recorded segments
that cover it, concat them, and cut the exact range into an MP4.

Because each segment is a complete file (not the one being written right now),
this is safe to run while recording continues — important on Windows where the
actively-written file can be locked.
"""
import glob
import os
import subprocess
import tempfile
from datetime import datetime

from config import settings

SEG_PREFIX = "seg_"
SEG_FMT = "%Y%m%d_%H%M%S"


def _parse_seg_start(path: str) -> float:
    name = os.path.basename(path)            # seg_YYYYmmdd_HHMMSS.ts
    stamp = name[len(SEG_PREFIX):-3]         # strip prefix and ".ts"
    return datetime.strptime(stamp, SEG_FMT).timestamp()


def _find_segments(t0: float, t1: float) -> list[tuple[float, str]]:
    seg_len = settings.SEGMENT_SECONDS
    selected: list[tuple[float, str]] = []
    for f in glob.glob(os.path.join(settings.SEGMENTS_DIR, SEG_PREFIX + "*.ts")):
        try:
            start = _parse_seg_start(f)
        except ValueError:
            continue
        end = start + seg_len
        if start < t1 and end > t0:          # overlaps the requested range
            selected.append((start, f))
    selected.sort()
    return selected


def make_clip(t0: float, t1: float, out_path: str) -> str:
    segs = _find_segments(t0, t1)
    if not segs:
        raise RuntimeError(
            "No recorded segments cover this moment yet "
            "(stream may have just started, or retention is too short)."
        )

    first_start = segs[0][0]
    offset = max(0.0, t0 - first_start)      # seconds into the concatenated stream
    duration = max(1.0, t1 - t0)

    fd, list_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for _, f in segs:
                # ffmpeg concat wants forward slashes even on Windows
                fh.write(f"file '{os.path.abspath(f).replace(os.sep, '/')}'\n")

        cmd = [
            settings.FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-ss", f"{offset:.2f}",
            "-t", f"{duration:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr[-500:]}")
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass

    return out_path
