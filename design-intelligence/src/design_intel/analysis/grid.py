"""Grid inference: column count, gutters, baseline rhythm, alignment discipline.

The method is the one a designer uses by eye — find the left and right edges of
the composition's blocks, then ask which column count explains those edges best.
Candidate counts are scored by how tightly the edges snap to their grid lines,
chance-corrected so that a 16-column grid cannot win simply by having more lines
to snap to.
"""

from __future__ import annotations

import numpy as np

from ..types import GridProfile
from .imaging import LoadedImage, box_blur, connected_components, gradients, normalize

CANDIDATE_COLUMNS = (1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 16)
SNAP_TOLERANCE = 0.012  # fraction of content width
COMPLEXITY_PENALTY = 0.004  # tiebreaker only; chance-correction does the real work


def _element_edges(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Left/right edges of the composition's blocks, weighted by block height.

    Reading the grid off raw gradient peaks was tried first and fails: body text
    contributes hundreds of edges that drown out the handful of structural ones.
    Designers read a grid off *element* boundaries, so that is what is measured
    — ink is closed into blocks, and each block contributes exactly two edges.
    """
    h, w = ink.shape
    blocks = box_blur((ink > 0.12).astype(np.float32), radius=max(2, min(h, w) // 60)) > 0.10
    min_area = 0.0008 * h * w

    positions: list[float] = []
    weights: list[float] = []
    for x0, y0, x1, y1, area in connected_components(blocks):
        if area < min_area or (x1 - x0) < 2:
            continue
        weight = float((y1 - y0 + 1) / h)
        positions.extend([float(x0), float(x1)])
        weights.extend([weight, weight])
    return np.array(positions, dtype=np.float32), np.array(weights, dtype=np.float32)


def _peaks(signal: np.ndarray, min_distance: int, threshold: float) -> np.ndarray:
    """Indices of local maxima above `threshold`, thinned by `min_distance`."""
    idx = np.nonzero(
        (signal > threshold)
        & (signal >= np.concatenate([[0.0], signal[:-1]]))
        & (signal >= np.concatenate([signal[1:], [0.0]]))
    )[0]
    if len(idx) == 0:
        return idx
    keep: list[int] = []
    for i in idx[np.argsort(-signal[idx])]:
        if all(abs(int(i) - k) >= min_distance for k in keep):
            keep.append(int(i))
    return np.array(sorted(keep), dtype=np.int32)


def _content_box(signal: np.ndarray, threshold: float) -> tuple[int, int]:
    hits = np.nonzero(signal > threshold)[0]
    if len(hits) < 2:
        return 0, len(signal) - 1
    return int(hits[0]), int(hits[-1])


def _score_columns(peaks: np.ndarray, weights: np.ndarray, x0: int, x1: int, cols: int) -> float:
    """How well the detected edges snap onto a `cols`-column grid, chance-corrected.

    Raw snap rate is monotonically increasing in `cols` — a 16-column grid has
    17 lines and would win every time. So the score is the *lift* over what
    randomly-placed edges would score against the same number of lines:
    (observed - expected) / (1 - expected).
    """
    span = max(1, x1 - x0)
    lines = x0 + np.linspace(0.0, 1.0, cols + 1) * span
    if len(peaks) == 0:
        return 0.0
    dist = np.abs(peaks[:, None] - lines[None, :]).min(axis=1) / span
    snap = np.exp(-(dist**2) / (2 * SNAP_TOLERANCE**2))
    observed = float((snap * weights).sum() / max(weights.sum(), 1e-6))

    # a gaussian kernel of width sigma covers sqrt(2*pi)*sigma of the axis per line
    expected = min(0.95, len(lines) * float(np.sqrt(2 * np.pi)) * SNAP_TOLERANCE)
    return float(max(0.0, (observed - expected) / (1.0 - expected)))


def _dominant_period(signal: np.ndarray, min_period: int, max_period: int) -> tuple[float, float]:
    """First strong autocorrelation peak — the baseline/rhythm unit."""
    sig = signal - signal.mean()
    if float(np.abs(sig).sum()) < 1e-6 or max_period <= min_period:
        return 0.0, 0.0
    ac = np.correlate(sig, sig, mode="full")[len(sig) - 1 :]
    if ac[0] <= 1e-9:
        return 0.0, 0.0
    ac = ac / ac[0]
    window = ac[min_period : min(max_period, len(ac))]
    if len(window) == 0:
        return 0.0, 0.0
    best = int(window.argmax())
    return float(best + min_period), float(max(0.0, window[best]))


def analyze_grid(img: LoadedImage, ink: np.ndarray | None = None) -> GridProfile:
    from .layout import ink_map

    ink = ink_map(img) if ink is None else ink
    h, w = ink.shape
    gx, gy = gradients(img.gray)

    col_signal = normalize(np.abs(gx).sum(axis=0))
    row_signal = normalize(np.abs(gy).sum(axis=1))

    x0, x1 = _content_box(normalize(ink.max(axis=0)), 0.12)

    peaks, weights = _element_edges(ink)
    if len(peaks) < 4:
        # too few blocks to read a grid from — fall back to strong vertical edges
        peaks = _peaks(col_signal, min_distance=max(2, w // 120), threshold=0.18).astype(np.float32)
        weights = col_signal[peaks.astype(np.int32)] if len(peaks) else np.array([], dtype=np.float32)
    if len(peaks):
        keep = (peaks >= x0 - 2) & (peaks <= x1 + 2)
        peaks, weights = peaks[keep], weights[keep]

    candidates: list[dict[str, float | int]] = []
    for cols in CANDIDATE_COLUMNS:
        raw = _score_columns(peaks, weights, x0, x1, cols)
        candidates.append(
            {"columns": cols, "score": round(raw, 4), "adjusted": round(raw - COMPLEXITY_PENALTY * cols, 4)}
        )
    best = max(candidates, key=lambda c: c["adjusted"])
    columns = int(best["columns"])
    confidence = float(best["score"])

    # gutters: median gap between neighbouring vertical edges, as a fraction of
    # one column, only counting gaps small enough to be gutters rather than columns.
    gutter_ratio = 0.0
    if len(peaks) >= 3:
        gaps = np.diff(np.unique(np.sort(peaks))).astype(np.float32)
        col_width = max(1.0, (x1 - x0) / max(columns, 1))
        small = gaps[gaps < col_width * 0.8]
        if len(small):
            gutter_ratio = float(np.median(small) / max(1.0, x1 - x0))

    period, strength = _dominant_period(row_signal, min_period=max(4, h // 90), max_period=max(6, h // 6))

    alignment = _alignment_score(peaks, weights, x0, x1, columns)

    return GridProfile(
        columns=columns,
        column_confidence=round(confidence, 3),
        gutter_ratio=round(gutter_ratio, 4),
        baseline_unit=round(period / h, 4) if period else 0.0,
        alignment_score=round(alignment, 3),
        modular=bool(confidence > 0.55 and strength > 0.25 and columns >= 3),
        candidates=sorted(candidates, key=lambda c: -c["adjusted"])[:5],
    )


def _alignment_score(peaks: np.ndarray, weights: np.ndarray, x0: int, x1: int, columns: int) -> float:
    """Discipline: do edges cluster on a few axes, or scatter everywhere?

    Distinct from column_confidence — a layout can be rigorously aligned to 3
    arbitrary axes without being on a regular column grid.
    """
    if len(peaks) < 2:
        return 0.0
    span = max(1, x1 - x0)
    positions = (peaks - x0) / span
    hist, _ = np.histogram(positions, bins=24, range=(0.0, 1.0), weights=weights)
    total = float(hist.sum())
    if total <= 1e-6:
        return 0.0
    p = hist / total
    p = p[p > 0]
    concentration = 1.0 - float(-(p * np.log2(p)).sum() / np.log2(24))
    return float(np.clip(concentration * (0.7 + 0.3 * min(1.0, columns / 6.0)), 0.0, 1.0))
