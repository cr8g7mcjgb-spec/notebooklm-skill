"""Layout analysis: whitespace, balance, symmetry, focal points, margins.

Everything is driven by an "ink map" — a blurred edge-energy field that stands
in for where a viewer's attention lands. It behaves far better than raw
darkness, which would call a black background "100% ink".
"""

from __future__ import annotations

import numpy as np

from ..types import LayoutProfile
from .imaging import LoadedImage, block_reduce, box_blur, edge_magnitude, entropy, normalize

GRID = 32
INK_THRESHOLD = 0.08


def ink_map(img: LoadedImage) -> np.ndarray:
    """Normalised attention/ink field in 0..1, same size as the analysis image."""
    edges = edge_magnitude(img.gray)
    edges = box_blur(edges, radius=max(1, min(img.shape) // 120))

    # solid coloured shapes carry visual weight but have no interior edges, so
    # add deviation from the background tone as a second energy term.
    lab_l = img.lab[..., 0] / 100.0
    chroma = np.hypot(img.lab[..., 1], img.lab[..., 2]) / 128.0
    bg_l = float(np.median(lab_l))
    mass = np.abs(lab_l - bg_l) + chroma
    mass = box_blur(mass, radius=max(1, min(img.shape) // 90))

    return normalize(normalize(edges) * 0.65 + normalize(mass) * 0.35)


def analyze_layout(img: LoadedImage, ink: np.ndarray | None = None) -> LayoutProfile:
    ink = ink_map(img) if ink is None else ink
    h, w = ink.shape
    cells = block_reduce(ink, GRID)

    occupied = cells > INK_THRESHOLD
    whitespace = 1.0 - float(occupied.mean())

    total = float(cells.sum())
    if total <= 1e-6:
        return LayoutProfile(
            whitespace_ratio=1.0, orientation=_orientation(img), aspect_ratio=round(w / h, 3)
        )

    ys, xs = np.mgrid[0:GRID, 0:GRID]
    cx = float((cells * xs).sum() / total) / (GRID - 1)
    cy = float((cells * ys).sum() / total) / (GRID - 1)
    balance = 1.0 - min(1.0, 2.0 * float(np.hypot(cx - 0.5, cy - 0.5)))

    v_sym = _symmetry(cells, axis=1)
    h_sym = _symmetry(cells, axis=0)

    quad = [
        float(cells[: GRID // 2, : GRID // 2].sum()),
        float(cells[: GRID // 2, GRID // 2 :].sum()),
        float(cells[GRID // 2 :, : GRID // 2].sum()),
        float(cells[GRID // 2 :, GRID // 2 :].sum()),
    ]
    quad = [round(q / total, 3) for q in quad]

    return LayoutProfile(
        whitespace_ratio=round(whitespace, 3),
        ink_density=round(float(cells.mean()), 3),
        balance=round(balance, 3),
        horizontal_symmetry=round(h_sym, 3),
        vertical_symmetry=round(v_sym, 3),
        rule_of_thirds=round(_rule_of_thirds(cells), 3),
        quadrant_weights=quad,
        margins=_margins(ink),
        focal_points=_focal_points(cells),
        density_entropy=round(entropy(cells.ravel()), 3),
        orientation=_orientation(img),
        aspect_ratio=round(w / h, 3),
    )


def _orientation(img: LoadedImage) -> str:
    ratio = img.width / max(1, img.height)
    if ratio > 1.15:
        return "landscape"
    if ratio < 0.87:
        return "portrait"
    return "square"


def _symmetry(cells: np.ndarray, axis: int) -> float:
    """1.0 when the composition mirrors itself across the given axis."""
    flipped = np.flip(cells, axis=axis)
    a, b = cells.ravel(), flipped.ravel()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-9:
        return 0.0
    return max(0.0, float(a @ b) / denom)


def _rule_of_thirds(cells: np.ndarray) -> float:
    """Share of visual mass sitting on the third lines vs. a uniform baseline."""
    thirds = [GRID // 3, 2 * GRID // 3]
    band = max(1, GRID // 12)
    mask = np.zeros_like(cells, dtype=bool)
    for t in thirds:
        mask[max(0, t - band) : t + band, :] = True
        mask[:, max(0, t - band) : t + band] = True
    total = float(cells.sum())
    if total <= 1e-6:
        return 0.0
    on_lines = float(cells[mask].sum()) / total
    expected = float(mask.mean())
    return min(1.0, on_lines / max(expected, 1e-6) / 2.0)


def _margins(ink: np.ndarray, threshold: float = 0.12) -> dict[str, float]:
    """Distance from each edge to the first row/column carrying real content."""
    h, w = ink.shape
    rows = ink.max(axis=1)
    cols = ink.max(axis=0)

    def first(sig: np.ndarray) -> int:
        hits = np.nonzero(sig > threshold)[0]
        return int(hits[0]) if len(hits) else 0

    def last(sig: np.ndarray, n: int) -> int:
        hits = np.nonzero(sig > threshold)[0]
        return n - 1 - int(hits[-1]) if len(hits) else 0

    return {
        "top": round(first(rows) / h, 3),
        "bottom": round(last(rows, h) / h, 3),
        "left": round(first(cols) / w, 3),
        "right": round(last(cols, w) / w, 3),
    }


def _focal_points(cells: np.ndarray, top_k: int = 3) -> list[list[float]]:
    """Greedy non-maximum suppression over the coarse ink map."""
    work = cells.copy()
    points: list[list[float]] = []
    radius = max(1, GRID // 8)
    for _ in range(top_k):
        idx = int(work.argmax())
        y, x = divmod(idx, work.shape[1])
        weight = float(work[y, x])
        if weight <= INK_THRESHOLD:
            break
        points.append([round(x / (GRID - 1), 3), round(y / (GRID - 1), 3), round(weight, 3)])
        work[max(0, y - radius) : y + radius + 1, max(0, x - radius) : x + radius + 1] = 0.0
    return points
