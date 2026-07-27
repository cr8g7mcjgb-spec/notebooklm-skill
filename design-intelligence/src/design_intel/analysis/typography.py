"""Typography analysis without OCR.

Glyphs are found as connected components in a local-contrast mask, grouped into
lines, and lines into blocks. From the distribution of line heights we recover
the type scale — which is the single most transferable thing about a reference:
"this poster runs a 1.5 scale across 4 levels" is a rule Claude can apply.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ..types import TypographyProfile
from .imaging import LoadedImage, box_blur, connected_components

NAMED_SCALES = {
    1.067: "minor second",
    1.125: "major second",
    1.200: "minor third",
    1.250: "major third",
    1.333: "perfect fourth",
    1.414: "augmented fourth",
    1.500: "perfect fifth",
    1.618: "golden ratio",
    2.000: "octave",
}


def _text_mask(img: LoadedImage) -> np.ndarray:
    """Local-contrast mask — catches dark-on-light and light-on-dark alike.

    The radius is small (stroke scale, not glyph scale): large text then shows
    up as a hollow outline, whose bounding box is still exactly right, while
    small text fills in solid. A percentile threshold was tried first and is
    wrong here — it moves with how much text the page happens to contain, so
    text-dense pages threshold themselves out of existence.
    """
    h, w = img.shape
    radius = max(2, min(h, w) // 200)
    deviation = np.abs(img.gray - box_blur(img.gray, radius))
    lo, hi = (float(x) for x in np.percentile(img.gray, [2, 98]))
    return deviation > max(0.055, 0.10 * (hi - lo))



def _classify_box(box: tuple[int, int, int, int, int], h: int, w: int) -> str | None:
    """Sort a component into 'glyph', 'line' or discard.

    Both granularities occur in practice: display type breaks into per-letter
    components, while body copy at small sizes merges into one component per
    line. Accepting only glyphs loses every text-dense page; accepting only
    lines loses every headline.
    """
    x0, y0, x1, y1, area = box
    bh, bw = y1 - y0 + 1, x1 - x0 + 1
    fill = area / max(1, bh * bw)
    aspect = bw / max(1, bh)

    if bh < max(4, h * 0.005) or bh > h * 0.40:
        return None  # hairline noise, or too tall to be type
    if fill > 0.93 and aspect > 14 and bh < h * 0.022:
        return None  # a solid rule or divider, not a line of text
    if aspect > 8 and 0.12 <= fill <= 0.95:
        return "line"
    if aspect <= 12 and 0.06 <= fill <= 0.98 and bw < w * 0.55:
        return "glyph"
    return None


def _group_lines(boxes: list[tuple[int, int, int, int, int]]) -> list[dict[str, float]]:
    """Merge glyph boxes that share a baseline band into text lines.

    The band tolerance is derived from the *glyph* height, never from the line's
    accumulated bounding box — otherwise each merge widens the tolerance and a
    dense text column collapses into a single "line".
    """
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: (b[1], b[0]))
    lines: list[list[tuple[int, int, int, int, int]]] = []
    anchors: list[tuple[float, float]] = []  # (running centre y, median glyph height)

    for box in boxes:
        _, y0, _, y1, _ = box
        cy, height = (y0 + y1) / 2, y1 - y0 + 1
        for i, (lcy, lheight) in enumerate(anchors):
            if abs(cy - lcy) < 0.45 * lheight and 0.5 <= height / max(1.0, lheight) <= 2.0:
                lines[i].append(box)
                members = lines[i]
                anchors[i] = (
                    float(np.mean([(b[1] + b[3]) / 2 for b in members])),
                    float(np.median([b[3] - b[1] + 1 for b in members])),
                )
                break
        else:
            lines.append([box])
            anchors.append((cy, float(height)))

    out: list[dict[str, float]] = []
    for line in lines:
        if len(line) < 2 and (line[0][2] - line[0][0]) < 3 * (line[0][3] - line[0][1]):
            continue  # a lone blob is a bullet or an icon, not a line of text
        x0 = min(b[0] for b in line)
        x1 = max(b[2] for b in line)
        y0 = min(b[1] for b in line)
        y1 = max(b[3] for b in line)
        out.append(
            {
                "x0": float(x0),
                "x1": float(x1),
                "y0": float(y0),
                "y1": float(y1),
                "height": float(y1 - y0 + 1),
                "width": float(x1 - x0),
                "glyphs": float(len(line)),
                "area": float(sum(b[4] for b in line)),
            }
        )
    return sorted(out, key=lambda line: line["y0"])


def _as_line(box: tuple[int, int, int, int, int]) -> dict[str, float]:
    """Wrap a component that is already a whole text line."""
    x0, y0, x1, y1, area = box
    return {
        "x0": float(x0),
        "x1": float(x1),
        "y0": float(y0),
        "y1": float(y1),
        "height": float(y1 - y0 + 1),
        "width": float(x1 - x0),
        "glyphs": 0.0,
        "area": float(area),
    }


def _cluster_levels(heights: list[float], tolerance: float = 0.14) -> list[float]:
    """Agglomerate line heights into distinct type-scale levels."""
    if not heights:
        return []
    levels: list[list[float]] = []
    for value in sorted(heights, reverse=True):
        for group in levels:
            if abs(value - group[0]) / max(group[0], 1e-6) <= tolerance:
                group.append(value)
                break
        else:
            levels.append([value])
    # drop levels supported by a single line unless they are the largest
    kept = [g for i, g in enumerate(levels) if len(g) > 1 or i == 0 or len(levels) <= 3]
    return [float(np.median(g)) for g in kept]


def _scale_ratio(levels: list[float]) -> tuple[float, str]:
    if len(levels) < 2:
        return 0.0, "single level"
    ratios = [levels[i] / max(levels[i + 1], 1e-6) for i in range(len(levels) - 1)]
    ratios = [r for r in ratios if 1.02 < r < 3.0]
    if not ratios:
        return 0.0, "flat"
    geo = float(np.exp(np.mean(np.log(ratios))))
    nearest = min(NAMED_SCALES, key=lambda r: abs(r - geo))
    name = NAMED_SCALES[nearest] if abs(nearest - geo) / nearest < 0.08 else "custom"
    return round(geo, 3), name


def _alignment(lines: list[dict[str, float]], width: int) -> str:
    """Dominant text alignment, decided per column and then voted.

    Measuring the spread of every line's left edge across the whole page reads a
    clean two-column Swiss layout as "right-aligned", because the two columns
    start at different x. Alignment is a property of a text block, so each block
    is judged on its own and blocks vote by line count.
    """
    if len(lines) < 3:
        return "unknown"

    votes: dict[str, int] = {}
    for group in _column_groups(lines, width):
        if len(group) < 3:
            continue
        left = np.array([line["x0"] for line in group])
        right = np.array([line["x1"] for line in group])
        center = (left + right) / 2
        tol = max(width * 0.012, float(np.median(right - left)) * 0.06)
        left_std, right_std, center_std = float(left.std()), float(right.std()), float(center.std())

        if left_std < tol and right_std < tol:
            verdict = "justified"
        elif center_std < tol and left_std > tol and right_std > tol:
            verdict = "center"
        elif left_std <= right_std:
            verdict = "left"
        else:
            verdict = "right"
        votes[verdict] = votes.get(verdict, 0) + len(group)

    if not votes:
        return "unknown"
    return max(votes.items(), key=lambda kv: kv[1])[0]


def _column_groups(lines: list[dict[str, float]], width: int) -> list[list[dict[str, float]]]:
    """Split lines into columns by horizontal overlap with the group's span."""
    groups: list[list[dict[str, float]]] = []
    spans: list[tuple[float, float]] = []
    for line in sorted(lines, key=lambda item: item["x0"]):
        for i, (gx0, gx1) in enumerate(spans):
            overlap = min(gx1, line["x1"]) - max(gx0, line["x0"])
            if overlap > 0.45 * min(gx1 - gx0, max(line["x1"] - line["x0"], 1.0)):
                groups[i].append(line)
                spans[i] = (min(gx0, line["x0"]), max(gx1, line["x1"]))
                break
        else:
            groups.append([line])
            spans.append((line["x0"], line["x1"]))
    return groups


def _stroke_stats(mask: np.ndarray, lines: list[dict[str, float]]) -> tuple[float, float]:
    """Median stroke width and its coefficient of variation (weight contrast)."""
    widths: list[int] = []
    for line in lines[:40]:
        y0, y1 = int(line["y0"]), int(line["y1"])
        for y in range(y0, min(y1 + 1, mask.shape[0]), max(1, (y1 - y0 + 1) // 4)):
            row = mask[y, int(line["x0"]) : int(line["x1"]) + 1]
            if not row.any():
                continue
            padded = np.concatenate([[False], row, [False]])
            edges = np.diff(padded.astype(np.int8))
            starts = np.nonzero(edges == 1)[0]
            ends = np.nonzero(edges == -1)[0]
            widths.extend(int(e - s) for s, e in zip(starts, ends, strict=False) if 0 < e - s < 40)
    if len(widths) < 8:
        return 0.0, 0.0
    arr = np.array(widths, dtype=np.float32)
    median = float(np.median(arr))
    contrast = float(np.std(arr) / max(median, 1e-6))
    return median, contrast


def analyze_typography(img: LoadedImage) -> TypographyProfile:
    h, w = img.shape
    mask = _text_mask(img)

    glyphs: list[tuple[int, int, int, int, int]] = []
    prebuilt: list[tuple[int, int, int, int, int]] = []
    for box in connected_components(mask):
        kind = _classify_box(box, h, w)
        if kind == "glyph":
            glyphs.append(box)
        elif kind == "line":
            prebuilt.append(box)

    lines = _group_lines(glyphs) + [_as_line(b) for b in prebuilt]
    lines.sort(key=lambda line: line["y0"])

    if not lines:
        return TypographyProfile(density="none")

    heights = [line["height"] for line in lines]
    levels = _cluster_levels(heights)
    ratio, scale_name = _scale_ratio(levels)

    text_area = sum(line["area"] for line in lines)
    coverage = float(text_area / (h * w))
    stroke, stroke_contrast = _stroke_stats(mask, lines)

    blocks = _count_blocks(lines)
    measure = float(np.median([line["width"] for line in lines]) / w)

    if coverage < 0.015:
        density = "sparse"
    elif coverage > 0.09:
        density = "dense"
    else:
        density = "balanced"

    normalized_stroke = stroke / max(1.0, float(np.median(heights)))
    if stroke_contrast > 0.55:
        letterform = "serif_display"
    elif normalized_stroke > 0.22:
        letterform = "grotesque_bold"
    elif normalized_stroke > 0.10:
        letterform = "grotesque"
    else:
        letterform = "humanist_light"

    return TypographyProfile(
        text_blocks=blocks,
        text_lines=len(lines),
        text_coverage=round(coverage, 4),
        scale_levels=[round(level / h, 4) for level in levels],
        scale_ratio=ratio,
        scale_name=scale_name,
        hierarchy_depth=len(levels),
        dominant_alignment=_alignment(lines, w),
        stroke_contrast=round(stroke_contrast, 3),
        stroke_weight=round(normalized_stroke, 3),
        letterform_class=letterform,
        measure=round(measure, 3),
        density=density,
    )


def _count_blocks(lines: list[dict[str, float]]) -> int:
    """Lines separated by more than 1.6x their height start a new block."""
    if not lines:
        return 0
    blocks, groups = 1, defaultdict(list)
    for i in range(1, len(lines)):
        gap = lines[i]["y0"] - lines[i - 1]["y1"]
        if gap > 1.6 * max(lines[i]["height"], lines[i - 1]["height"]):
            blocks += 1
        groups[blocks].append(i)
    return blocks
