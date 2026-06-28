"""
The recorder. Runs the ENTIRE time the channel is live, writing rolling
~SEGMENT_SECONDS .ts files to disk named by their start wall-clock time.

This IS our rolling buffer. We never hold video in memory. When a moment fires,
the clipper just seeks into the segments that are already on disk. This is more
robust than an in-memory ring buffer, survives crashes, and reuses cleanly for
the future VOD/podcast use-case.

Two backends (configurable):
  streamlink -> ffmpeg  (default; most reliable for Kick)
  ffmpeg direct on the playback_url
"""
import glob
import os
import subprocess
import threading
import time

from config import settings


class Recorder:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.procs: list[subprocess.Popen] = []
        self.running = False
        self.playback_url: str | None = None

    def start(self, playback_url: str | None = None):
        if self.running:
            return
        self.playback_url = playback_url
        self._stop.clear()
        os.makedirs(settings.SEGMENTS_DIR, exist_ok=True)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.running = True
        print(f"[recorder] started (backend={settings.RECORDER_BACKEND})")

    def stop(self):
        self._stop.set()
        self._kill_procs()
        self.running = False
        print("[recorder] stopped")

    # ---- internals ----
    def _seg_pattern(self) -> str:
        # ffmpeg -strftime expands this. Names encode the segment START time.
        return os.path.join(settings.SEGMENTS_DIR, "seg_%Y%m%d_%H%M%S.ts")

    def _ffmpeg_segment_cmd(self, input_url: str) -> list[str]:
        cmd = [settings.FFMPEG_BIN, "-y"]
        # -user_agent only applies to HTTP/HLS inputs. With the streamlink
        # backend the input is pipe:0 and ffmpeg rejects the option.
        if input_url.startswith("http"):
            cmd += [
                "-user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            ]
        cmd += [
            "-i", input_url,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(settings.SEGMENT_SECONDS),
            "-segment_format", "mpegts",
            "-reset_timestamps", "1",
            "-strftime", "1",
            self._seg_pattern(),
        ]
        return cmd

    def _run(self):
        while not self._stop.is_set():
            try:
                if settings.RECORDER_BACKEND == "ffmpeg" and self.playback_url:
                    ff = subprocess.Popen(self._ffmpeg_segment_cmd(self.playback_url))
                    self.procs = [ff]
                    watch = ff
                else:
                    channel_url = f"https://kick.com/{settings.CHANNEL}"
                    sl = subprocess.Popen(
                        [settings.STREAMLINK_BIN, channel_url, settings.STREAM_QUALITY,
                         "--stdout", "--loglevel", "error"],
                        stdout=subprocess.PIPE,
                    )
                    ff = subprocess.Popen(self._ffmpeg_segment_cmd("pipe:0"), stdin=sl.stdout)
                    if sl.stdout:
                        sl.stdout.close()  # let ff own the pipe; sl gets SIGPIPE on stop
                    self.procs = [sl, ff]
                    watch = ff

                self._supervise(watch)

            except FileNotFoundError as e:
                print(f"[recorder] binary not found: {e}. Is ffmpeg / streamlink on PATH?")
                return
            except Exception as e:  # noqa: BLE001
                print(f"[recorder] error: {e}")

            self._kill_procs()
            if not self._stop.is_set():
                # stream may have briefly dropped; retry
                time.sleep(3)

    def _supervise(self, proc: subprocess.Popen):
        last_prune = 0.0
        while not self._stop.is_set():
            if proc.poll() is not None:
                break  # recorder process died -> outer loop retries
            now = time.time()
            if now - last_prune > 10:
                self._prune()
                last_prune = now
            time.sleep(1)

    def _kill_procs(self):
        for p in self.procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:  # noqa: BLE001
                pass
        self.procs = []

    def _prune(self):
        """Delete segments older than the retention window so disk stays bounded."""
        cutoff = time.time() - settings.SEGMENT_RETENTION_SECONDS
        for f in glob.glob(os.path.join(settings.SEGMENTS_DIR, "seg_*.ts")):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except OSError:
                pass
