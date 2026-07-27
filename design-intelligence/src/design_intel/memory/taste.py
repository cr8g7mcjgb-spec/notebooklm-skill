"""Taste memory — the part that makes the system learn *your* eye.

Two mechanisms, both cheap and both inspectable:

1. A Rocchio-style taste vector in embedding space: liked references pull it
   toward them, disliked ones push it away. It re-ranks search results.
2. A feature preference profile: the median of each measured trait across the
   designs you kept. It feeds directly into generated briefs, so "you always
   pick the airier option" becomes an explicit constraint rather than a vibe.

Everything is an append-only JSONL you can read, edit or delete by hand. No
model weights are changed anywhere — this is retrieval-side personalisation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..features import CATEGORICAL_SPECS, NUMERIC_SPECS
from ..style.classifier import resolve
from ..types import Reference

POSITIVE = {"like", "keep", "used", "shipped", "up"}
NEGATIVE = {"dislike", "reject", "skip", "down"}
ALPHA = 0.75  # weight on liked centroid
BETA = 0.35  # weight subtracted for disliked centroid


class TasteMemory:
    def __init__(self, path: str | Path = ".design-memory", enabled: bool = True) -> None:
        self.root = Path(path)
        self.enabled = enabled
        self.events_path = self.root / "feedback.jsonl"
        self.vector_path = self.root / "taste_vector.npy"
        self._vector: np.ndarray | None = None
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.vector_path.exists():
                try:
                    self._vector = np.load(self.vector_path).astype(np.float32)
                except (ValueError, OSError):
                    self._vector = None

    # -- recording -------------------------------------------------------

    def record(
        self,
        reference: Reference | None,
        verdict: str,
        note: str = "",
        context: str = "",
        vector: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """Log one judgement. `vector` is the reference's embedding, if known."""
        if not self.enabled:
            return {"recorded": False, "reason": "memory disabled"}

        verdict = verdict.lower().strip()
        polarity = 1 if verdict in POSITIVE else -1 if verdict in NEGATIVE else 0
        event: dict[str, Any] = {
            "ts": time.time(),
            "ref_id": reference.id if reference else None,
            "path": reference.path if reference else None,
            "verdict": verdict,
            "polarity": polarity,
            "note": note,
            "context": context,
        }
        if reference is not None:
            event["features"] = {
                spec.path: resolve(reference.features, spec.path)
                for spec in NUMERIC_SPECS + CATEGORICAL_SPECS
            }
            event["style"] = reference.features.style.primary
        if vector is not None:
            event["vector"] = np.asarray(vector, dtype=np.float32).round(5).tolist()

        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._vector = None  # recompute lazily
        return {"recorded": True, "polarity": polarity, "events": self.count()}

    # -- reading ---------------------------------------------------------

    def events(self) -> list[dict[str, Any]]:
        if not self.enabled or not self.events_path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.events_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def count(self) -> int:
        return len(self.events())

    def taste_vector(self) -> np.ndarray | None:
        """Rocchio centroid of liked minus disliked embeddings."""
        if not self.enabled:
            return None
        if self._vector is not None:
            return self._vector

        liked, disliked = [], []
        for event in self.events():
            vec = event.get("vector")
            if not vec:
                continue
            polarity = event.get("polarity", 0)
            bucket = liked if polarity > 0 else disliked if polarity < 0 else None
            if bucket is not None:
                bucket.append(np.asarray(vec, dtype=np.float32))
        if not liked:
            return None

        dim = len(liked[0])
        if any(len(v) != dim for v in liked + disliked):
            # embedder changed under us; ignore stale-dimension events
            liked = [v for v in liked if len(v) == dim]
            disliked = [v for v in disliked if len(v) == dim]

        vector = ALPHA * np.mean(liked, axis=0)
        if disliked:
            vector = vector - BETA * np.mean(disliked, axis=0)
        norm = float(np.linalg.norm(vector))
        if norm < 1e-8:
            return None
        vector = (vector / norm).astype(np.float32)
        np.save(self.vector_path, vector)
        self._vector = vector
        return vector

    def affinity(self, vector: np.ndarray | None) -> float:
        """0..1 taste alignment for one candidate embedding."""
        taste = self.taste_vector()
        if taste is None or vector is None:
            return 0.0
        candidate = np.asarray(vector, dtype=np.float32).ravel()
        if candidate.shape != taste.shape:
            return 0.0
        candidate = candidate / max(float(np.linalg.norm(candidate)), 1e-8)
        return float((float(taste @ candidate) + 1.0) / 2.0)

    # -- profile ---------------------------------------------------------

    def profile(self, min_events: int = 3) -> dict[str, Any]:
        """What your kept designs have in common — the learned aesthetic."""
        events = [e for e in self.events() if e.get("polarity", 0) > 0 and e.get("features")]
        rejected = [e for e in self.events() if e.get("polarity", 0) < 0 and e.get("features")]
        if len(events) < min_events:
            return {
                "ready": False,
                "liked": len(events),
                "rejected": len(rejected),
                "message": f"need at least {min_events} positive judgements to form a profile",
            }

        preferences: dict[str, Any] = {}
        for spec in NUMERIC_SPECS:
            values = [
                float(e["features"][spec.path])
                for e in events
                if isinstance(e["features"].get(spec.path), (int, float))
            ]
            if len(values) < min_events:
                continue
            arr = np.array(values)
            q1, q3 = (float(x) for x in np.percentile(arr, [25, 75]))
            span = max(spec.hi - spec.lo, 1e-6)
            consistency = float(np.clip(1.0 - (q3 - q1) / (span * 0.35), 0.0, 1.0))
            if consistency < 0.5:
                continue  # you are not consistent about this, so do not assert it
            preferences[spec.path] = {
                "label": spec.label,
                "preferred": round(float(np.median(arr)), 4),
                "range": [round(q1, 4), round(q3, 4)],
                "consistency": round(consistency, 3),
            }

        styles: dict[str, int] = {}
        for event in events:
            key = event.get("style")
            if key:
                styles[key] = styles.get(key, 0) + 1

        return {
            "ready": True,
            "liked": len(events),
            "rejected": len(rejected),
            "preferred_styles": sorted(styles.items(), key=lambda kv: -kv[1])[:4],
            "preferences": dict(
                sorted(preferences.items(), key=lambda kv: -kv[1]["consistency"])[:12]
            ),
            "has_taste_vector": self.taste_vector() is not None,
        }

    def reset(self) -> None:
        if self.events_path.exists():
            self.events_path.unlink()
        if self.vector_path.exists():
            self.vector_path.unlink()
        self._vector = None
