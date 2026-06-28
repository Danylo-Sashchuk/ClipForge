"""
Subtitle generation for approved clips.

  transcribe_to_srt: clip audio -> .srt  (faster-whisper, local, no API key)
  burn_subtitles:    clip + .srt -> *_captioned.mp4  (ffmpeg libass)

We always keep the clean original and write captions to a SEPARATE file, so you
can re-run / restyle captions without having destroyed the source.

faster-whisper is optional — if it isn't installed, transcription raises a clear
message and the rest of the app keeps working.
"""
import os
import subprocess

from config import settings

_model = None  # cached; loading the model is the slow part


def _get_model():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper is not installed. Run:  pip install faster-whisper"
            ) from e
        _model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE,
        )
    return _model


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_to_srt(video_path: str, srt_path: str) -> str:
    model = _get_model()
    segments, _info = model.transcribe(video_path, vad_filter=True)
    blocks = []
    i = 1
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        blocks.append(f"{i}\n{_srt_ts(seg.start)} --> {_srt_ts(seg.end)}\n{text}\n")
        i += 1
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))
    return srt_path


def burn_subtitles(video_path: str, srt_path: str, out_path: str) -> str:
    """Burn the SRT into the video. We run ffmpeg from the SRT's folder and
    reference it by bare filename, which sidesteps the well-known Windows
    path-escaping problems with the subtitles filter (drive-letter colons)."""
    work = os.path.dirname(os.path.abspath(srt_path)) or "."
    srt_name = os.path.basename(srt_path)
    style = (
        f"FontName=Arial,FontSize={settings.SUBTITLE_FONT_SIZE},"
        "PrimaryColour=&H00FFFFFF&,OutlineColour=&H90000000&,"
        "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=30"
    )
    vf = f"subtitles={srt_name}:force_style='{style}'"
    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-i", os.path.abspath(video_path),
        "-vf", vf,
        "-c:a", "copy",
        os.path.abspath(out_path),
    ]
    proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg subtitle burn failed: {proc.stderr[-500:]}")
    return out_path
