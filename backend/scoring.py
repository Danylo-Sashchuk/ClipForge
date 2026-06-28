"""
The detection engine.

Design (per our plan): there is really ONE detector — a "viral moment"
detector driven by chat *statistics*, not keyword lists. Keywords only TAG
what kind of moment it was (and give a small score nudge). This is far more
robust on stream chat (which is mostly emote spam and copypasta) than 25
separate keyword triggers.

Three signals:
  1. SPIKE      - acceleration of chat. current msgs/sec vs the recent baseline.
  2. HOMOGENEITY- is everyone saying the SAME thing? (crowd reacting in unison)
  3. KEYWORDS   - light secondary signal, mostly for labeling the moment.

Everything we compute is stored in a feature dict and saved with the clip, so
that later you can train a real model on these features without re-architecting.
"""
import re
import time
from collections import Counter, deque

from config import settings

# Small, focused keyword set (down from 25). These TAG moments; they don't drive
# detection. Add/trim freely — they only affect the label and a minor score bump.
KEYWORDS = {
    "laugh": ["lol", "lmao", "lmfao", "haha", "dead", "crying", "💀", "😭", "😂", "🤣"],
    "shock": ["omg", "wtf", "no way", "nah", "insane", "crazy", "wild", "sheesh", "what"],
    "hype":  ["lets go", "letsgo", "goat", "fire", "🔥", "w", "dub", "clutch", "valid"],
    "money": ["money", "cash", "rich", "broke", "bet", "bands", "racks", "paid"],
    "beef":  ["fight", "beef", "pressed", "swing", "mad", "angry", "violation", "cooked"],
    "fail":  ["fail", "fell", "sold", "choked", "fumbled", "bruh", "L ", "rip"],
}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def clip_duration_for_score(score: int) -> int:
    """Dynamic clip length based on intensity (seconds)."""
    if score >= 95:
        return 60
    if score >= 90:
        return 45
    if score >= 80:
        return 30
    return 15


class Detector:
    def __init__(self):
        self.msgs: deque[tuple[float, str]] = deque()  # (epoch, lowercased text)
        self.last_trigger = 0.0

    def add(self, epoch: float, text: str):
        self.msgs.append((epoch, (text or "").lower()))

    def _evict(self, now: float):
        cutoff = now - settings.LONG_WINDOW
        while self.msgs and self.msgs[0][0] < cutoff:
            self.msgs.popleft()

    def snapshot(self, now: float | None = None) -> dict:
        """Non-firing read of the current state (for the live dashboard meter)."""
        return self.evaluate(now=now, allow_fire=False)

    def evaluate(self, now: float | None = None, allow_fire: bool = True) -> dict:
        now = now if now is not None else time.time()
        self._evict(now)

        short = [m for m in self.msgs if m[0] >= now - settings.SHORT_WINDOW]
        short_count = len(short)

        base = {
            "fired": False,
            "score": 0,
            "primary_tag": "hype",
            "tags": [],
            "ts": now,
            "features": {"short_count": short_count},
        }

        if short_count < settings.MIN_MESSAGES:
            return base

        # --- 1. spike (acceleration) ---
        velocity = short_count / settings.SHORT_WINDOW
        prev = [m for m in self.msgs if m[0] < now - settings.SHORT_WINDOW]
        prev_span = max(1e-6, settings.LONG_WINDOW - settings.SHORT_WINDOW)
        baseline_vel = max(len(prev) / prev_span, settings.MIN_BASELINE_VEL)
        spike_ratio = velocity / baseline_vel

        # --- 2. homogeneity (are people saying the same thing?) ---
        tokens: list[str] = []
        for _, t in short:
            tokens.extend(t.split())
        homogeneity = 0.0
        if tokens:
            homogeneity = Counter(tokens).most_common(1)[0][1] / len(tokens)

        # --- 3. keyword tagging ---
        kw_counts: Counter = Counter()
        total_kw = 0
        for _, t in short:
            for cat, words in KEYWORDS.items():
                if any(w in t for w in words):
                    kw_counts[cat] += 1
                    total_kw += 1

        # --- combine into 0..100 score ---
        spike_c = _clamp((spike_ratio - 1.0) / (settings.SPIKE_RATIO_FOR_MAX - 1.0))
        homo_c = _clamp(homogeneity / settings.HOMO_FOR_MAX)
        kw_c = _clamp(total_kw / settings.KW_FOR_MAX)
        score = round(100 * (
            settings.WEIGHT_SPIKE * spike_c
            + settings.WEIGHT_HOMO * homo_c
            + settings.WEIGHT_KW * kw_c
        ))

        tags = [c for c, _ in kw_counts.most_common()]
        primary = tags[0] if tags else "hype"

        features = {
            "short_count": short_count,
            "velocity": round(velocity, 3),
            "baseline_velocity": round(baseline_vel, 3),
            "spike_ratio": round(spike_ratio, 2),
            "homogeneity": round(homogeneity, 3),
            "keyword_total": total_kw,
            "keyword_counts": dict(kw_counts),
        }

        fired = False
        if allow_fire and score >= settings.MIN_TRIGGER_SCORE \
                and (now - self.last_trigger) >= settings.COOLDOWN_SECONDS:
            fired = True
            self.last_trigger = now

        return {
            "fired": fired,
            "score": score,
            "primary_tag": primary,
            "tags": tags,
            "ts": now,
            "features": features,
        }


def reason_text(res: dict) -> str:
    """Human-readable explanation for the dashboard feed."""
    f = res.get("features", {})
    bits = []
    if f.get("spike_ratio", 0) >= 1.5:
        bits.append(f"{f['spike_ratio']:.1f}x chat spike")
    if f.get("homogeneity", 0) >= 0.25:
        bits.append("crowd reacting in unison")
    if res.get("tags"):
        bits.append("+".join(res["tags"][:3]))
    return ", ".join(bits) or "chat activity"
