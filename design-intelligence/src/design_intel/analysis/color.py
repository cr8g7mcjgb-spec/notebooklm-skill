"""Colour analysis: palette, roles, contrast, harmony.

Clustering happens in CIELAB rather than RGB so that "two blues a designer
would call the same blue" actually land in the same cluster.
"""

from __future__ import annotations

import numpy as np

from ..types import ColorProfile, Swatch
from .imaging import LoadedImage, kmeans, lab_to_rgb, to_hex, wcag_contrast

MAX_SAMPLES = 20000
HARMONY_TOLERANCE = 22.0  # degrees


def _sample_pixels(lab: np.ndarray, seed: int = 11) -> np.ndarray:
    flat = lab.reshape(-1, 3)
    if len(flat) <= MAX_SAMPLES:
        return flat
    rng = np.random.default_rng(seed)
    return flat[rng.choice(len(flat), MAX_SAMPLES, replace=False)]


def _hue_of(lab: np.ndarray) -> float:
    return float(np.degrees(np.arctan2(lab[2], lab[1])) % 360.0)


def _chroma_of(lab: np.ndarray) -> float:
    return float(np.hypot(lab[1], lab[2]))


def _classify_harmony(hues: list[float], chromas: list[float]) -> tuple[str, int]:
    """Name the palette's hue relationship, ignoring near-neutral swatches."""
    live = [h for h, c in zip(hues, chromas, strict=False) if c > 8.0]
    if not live:
        # every swatch is a neutral: that is still one (achromatic) family, and
        # reporting 0 would generate the rule "limit the palette to 0 hues"
        return "monochrome", 1

    # merge hues that sit within tolerance of each other (circular)
    groups: list[list[float]] = []
    for h in sorted(live):
        for g in groups:
            delta = abs(h - g[0])
            if min(delta, 360 - delta) <= HARMONY_TOLERANCE:
                g.append(h)
                break
        else:
            groups.append([h])
    if len(groups) > 1:
        first, last = groups[0][0], groups[-1][0]
        if min(abs(first - last), 360 - abs(first - last)) <= HARMONY_TOLERANCE:
            groups[0].extend(groups.pop())

    centers = sorted(float(np.mean(g)) for g in groups)
    n = len(centers)
    if n == 1:
        return "monochrome", 1
    if n == 2:
        gap = abs(centers[1] - centers[0])
        gap = min(gap, 360 - gap)
        if gap <= 45:
            return "analogous", n
        if gap >= 150:
            return "complementary", n
        return "split-complementary", n
    if n == 3:
        gaps = sorted(
            min(abs(a - b), 360 - abs(a - b))
            for a, b in ((centers[0], centers[1]), (centers[1], centers[2]), (centers[0], centers[2]))
        )
        if all(abs(g - 120) < 35 for g in gaps[:2]):
            return "triadic", n
        spread = max(centers) - min(centers)
        if spread <= 90:
            return "analogous", n
        return "split-complementary", n
    if n == 4:
        return "tetradic", n
    return "polychrome", n


def _edge_mask(shape: tuple[int, int], band: float = 0.06) -> np.ndarray:
    h, w = shape
    bh, bw = max(1, int(h * band)), max(1, int(w * band))
    mask = np.zeros((h, w), dtype=bool)
    mask[:bh, :] = mask[-bh:, :] = True
    mask[:, :bw] = mask[:, -bw:] = True
    return mask


def analyze_color(img: LoadedImage, k: int = 6) -> ColorProfile:
    lab = img.lab
    samples = _sample_pixels(lab)
    centroids, labels = kmeans(samples, k=k)

    counts = np.bincount(labels, minlength=len(centroids)).astype(np.float64)
    ratios = counts / max(1.0, counts.sum())
    order = np.argsort(-ratios)

    # which cluster owns the border? that is the background, regardless of size
    border = lab[_edge_mask(img.shape)].reshape(-1, 3)
    border_dists = ((border[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    border_counts = np.bincount(border_dists.argmin(axis=1), minlength=len(centroids))
    bg_idx = int(border_counts.argmax())

    swatches: list[Swatch] = []
    for idx in order:
        c = centroids[idx]
        rgb = lab_to_rgb(c[None, :])[0]
        swatches.append(
            Swatch(
                hex=to_hex(rgb),
                ratio=round(float(ratios[idx]), 4),
                lab=(round(float(c[0]), 2), round(float(c[1]), 2), round(float(c[2]), 2)),
                hue=round(_hue_of(c), 1),
                chroma=round(_chroma_of(c), 2),
                lightness=round(float(c[0]), 2),
                role="background" if idx == bg_idx else "support",
            )
        )

    bg_lab = centroids[bg_idx]
    bg_rgb = lab_to_rgb(bg_lab[None, :])[0]

    # foreground = the swatch with the most contrast against the background,
    # weighted by how much of the canvas it occupies (a 0.1% pure-black hairline
    # is not the text colour of the composition).
    fg_swatch, fg_rgb, best = None, bg_rgb, -1.0
    for s, idx in zip(swatches, order, strict=False):
        if idx == bg_idx:
            continue
        rgb = lab_to_rgb(centroids[idx][None, :])[0]
        score = wcag_contrast(rgb, bg_rgb) * (0.25 + min(s.ratio, 0.4))
        if score > best:
            best, fg_swatch, fg_rgb = score, s, rgb
    if fg_swatch is not None:
        fg_swatch.role = "text"

    # accent = strongest chroma that is not the background, if it stands out
    accent_swatch = None
    for s in swatches:
        if s.role == "background" or s.chroma < 18.0:
            continue
        if accent_swatch is None or s.chroma > accent_swatch.chroma:
            accent_swatch = s
    if accent_swatch is not None and accent_swatch.role != "text":
        accent_swatch.role = "accent"
    for s in swatches:
        if s.role == "support" and s.ratio > 0.08:
            s.role = "surface"

    chromas = [s.chroma for s in swatches]
    hues = [s.hue for s in swatches]
    harmony, hue_count = _classify_harmony(hues, chromas)
    contrast = wcag_contrast(fg_rgb, bg_rgb)
    mean_chroma = float(np.average(chromas, weights=[max(s.ratio, 1e-6) for s in swatches]))

    return ColorProfile(
        palette=swatches,
        background=to_hex(bg_rgb),
        foreground=to_hex(fg_rgb),
        accent=accent_swatch.hex if accent_swatch else None,
        contrast_ratio=round(contrast, 2),
        mean_lightness=round(float(lab[..., 0].mean()), 2),
        mean_chroma=round(mean_chroma, 2),
        chroma_spread=round(float(np.std(chromas)), 2),
        temperature=_temperature(lab),
        harmony=harmony,
        hue_count=hue_count,
        is_monochrome=hue_count <= 1,
        is_high_contrast=contrast >= 7.0,
    )


def _temperature(lab: np.ndarray) -> str:
    """Warm/cool from the mean b* (yellow-blue) axis, chroma-weighted."""
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    weight = chroma / max(float(chroma.sum()), 1e-6)
    b = float((lab[..., 2] * weight).sum())
    a = float((lab[..., 1] * weight).sum())
    if b > 6.0 or a > 8.0:
        return "warm"
    if b < -6.0:
        return "cool"
    return "neutral"
