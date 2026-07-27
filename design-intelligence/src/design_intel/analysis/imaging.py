"""Image loading plus the small numeric kernels every analyzer shares.

Deliberately dependency-light: numpy + Pillow only. No OpenCV, no scipy, so the
analysis layer installs and runs anywhere (including inside the MCP container
without a GPU stack).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

MAX_EDGE = 768


@dataclass
class LoadedImage:
    """A design image pre-converted into every representation we need."""

    rgb: np.ndarray  # float32 (h, w, 3) in 0..1, sRGB
    gray: np.ndarray  # float32 (h, w) in 0..1, luminance
    lab: np.ndarray  # float32 (h, w, 3), CIELAB
    width: int  # original pixel width
    height: int  # original pixel height
    path: str = ""

    @property
    def shape(self) -> tuple[int, int]:
        return self.gray.shape[0], self.gray.shape[1]


def load_image(source: str | Path | Image.Image, max_edge: int = MAX_EDGE) -> LoadedImage:
    """Load and normalise an image for analysis.

    EXIF orientation is applied, alpha is flattened onto white (design exports
    are routinely transparent PNGs and a black flatten would wreck the palette).
    """
    if isinstance(source, Image.Image):
        img = source
        path = ""
    else:
        path = str(source)
        img = Image.open(path)

    img = ImageOps.exif_transpose(img)
    orig_w, orig_h = img.size

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        canvas = Image.new("RGBA", img.size, (255, 255, 255, 255))
        canvas.alpha_composite(img)
        img = canvas.convert("RGB")
    else:
        img = img.convert("RGB")

    scale = max_edge / max(img.size)
    if scale < 1.0:
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.LANCZOS,
        )

    rgb = np.asarray(img, dtype=np.float32) / 255.0
    return LoadedImage(
        rgb=rgb,
        gray=rgb_to_luminance(rgb),
        lab=rgb_to_lab(rgb),
        width=orig_w,
        height=orig_h,
        path=path,
    )


# --------------------------------------------------------------------------
# colour space
# --------------------------------------------------------------------------

_M_RGB2XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float32,
)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4).astype(np.float32)


def rgb_to_luminance(rgb: np.ndarray) -> np.ndarray:
    """Perceptual luminance in 0..1 (gamma-encoded, good enough for edges)."""
    return (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]).astype(np.float32)


def relative_luminance(rgb: np.ndarray) -> np.ndarray:
    """WCAG relative luminance (linearised)."""
    lin = srgb_to_linear(rgb)
    return (0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]).astype(np.float32)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0..1) -> CIELAB. Works on (..., 3) arrays of any rank."""
    xyz = srgb_to_linear(rgb) @ _M_RGB2XYZ.T
    xyz = xyz / _WHITE
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(np.maximum(xyz, 1e-9)), (kappa * xyz + 16) / 116)
    lab = np.empty_like(f)
    lab[..., 0] = 116 * f[..., 1] - 16
    lab[..., 1] = 500 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200 * (f[..., 1] - f[..., 2])
    return lab.astype(np.float32)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """CIELAB -> sRGB (0..1), clipped to gamut."""
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    eps = 6 / 29

    def finv(t: np.ndarray) -> np.ndarray:
        return np.where(t > eps, t**3, 3 * eps**2 * (t - 4 / 29))

    xyz = np.stack([finv(fx), finv(fy), finv(fz)], axis=-1) * _WHITE
    lin = xyz @ np.linalg.inv(_M_RGB2XYZ).T
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.maximum(lin, 0) ** (1 / 2.4) - 0.055)
    return np.clip(srgb, 0.0, 1.0).astype(np.float32)


def to_hex(rgb: np.ndarray) -> str:
    v = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0) * 255.0
    return "#{:02x}{:02x}{:02x}".format(*(int(round(float(c))) for c in v[:3]))


def from_hex(value: str) -> np.ndarray:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return np.array([int(v[i : i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


def wcag_contrast(a: np.ndarray, b: np.ndarray) -> float:
    la = float(relative_luminance(np.asarray(a, dtype=np.float32)))
    lb = float(relative_luminance(np.asarray(b, dtype=np.float32)))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------
# spatial kernels
# --------------------------------------------------------------------------


def _convolve2d(img: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Valid-mode 2D correlation via stride tricks. Kernels here are 3x3."""
    kh, kw = kernel.shape
    padded = np.pad(img, ((kh // 2, kh // 2), (kw // 2, kw // 2)), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (kh, kw))
    return np.einsum("ijkl,kl->ij", windows, kernel).astype(np.float32)


_SOBEL_X = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
_SOBEL_Y = _SOBEL_X.T


def gradients(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sobel gradients (gx, gy)."""
    return _convolve2d(gray, _SOBEL_X), _convolve2d(gray, _SOBEL_Y)


def edge_magnitude(gray: np.ndarray) -> np.ndarray:
    gx, gy = gradients(gray)
    return np.hypot(gx, gy).astype(np.float32)


def box_blur(img: np.ndarray, radius: int) -> np.ndarray:
    """Separable box blur through cumulative sums — O(n) regardless of radius."""
    if radius < 1:
        return img
    out = img.astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius, radius)
        padded = np.pad(out, pad, mode="edge")
        cum = np.cumsum(padded, axis=axis)
        zero = np.zeros_like(np.take(cum, [0], axis=axis))
        cum = np.concatenate([zero, cum], axis=axis)
        n = out.shape[axis]
        hi = np.take(cum, np.arange(2 * radius + 1, 2 * radius + 1 + n), axis=axis)
        lo = np.take(cum, np.arange(0, n), axis=axis)
        out = (hi - lo) / (2 * radius + 1)
    return out.astype(np.float32)


def block_reduce(img: np.ndarray, blocks: int, how: str = "mean") -> np.ndarray:
    """Downsample to roughly (blocks, blocks) by averaging/maxing tiles."""
    h, w = img.shape
    bh, bw = max(1, h // blocks), max(1, w // blocks)
    trimmed = img[: bh * blocks, : bw * blocks]
    if trimmed.size == 0:
        return img.reshape(1, -1).mean(axis=1, keepdims=True)
    tiles = trimmed.reshape(blocks, bh, blocks, bw)
    return (tiles.max(axis=(1, 3)) if how == "max" else tiles.mean(axis=(1, 3))).astype(np.float32)


def normalize(arr: np.ndarray) -> np.ndarray:
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-8:
        return np.zeros_like(arr, dtype=np.float32)
    return ((arr - lo) / (hi - lo)).astype(np.float32)


def entropy(values: np.ndarray, bins: int = 32) -> float:
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-9:
        return 0.0  # a constant field (e.g. a blank canvas) carries no information
    hist, _ = np.histogram(values, bins=bins, range=(lo, hi))
    p = hist.astype(np.float64)
    total = p.sum()
    if total <= 0:
        return 0.0
    p = p[p > 0] / total
    return float(-(p * np.log2(p)).sum() / np.log2(bins))


def kmeans(data: np.ndarray, k: int, iters: int = 25, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """k-means++ over (n, d) data. Returns (centroids, labels).

    Seeded so that indexing the same image twice yields the same palette —
    a moving palette would silently invalidate every cached design brief.
    """
    rng = np.random.default_rng(seed)
    n = data.shape[0]
    k = max(1, min(k, n))

    centroids = np.empty((k, data.shape[1]), dtype=np.float32)
    centroids[0] = data[rng.integers(n)]
    closest = ((data - centroids[0]) ** 2).sum(axis=1)
    for i in range(1, k):
        total = float(closest.sum())
        probs = closest / total if total > 0 else np.full(n, 1.0 / n)
        centroids[i] = data[rng.choice(n, p=probs)]
        closest = np.minimum(closest, ((data - centroids[i]) ** 2).sum(axis=1))

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        dists = ((data[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        new_labels = dists.argmin(axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        for i in range(k):
            members = data[labels == i]
            if len(members):
                centroids[i] = members.mean(axis=0)
    return centroids, labels


def connected_components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Two-pass run-length connected components.

    Returns (x0, y0, x1, y1, area) per component, 8-connected across rows.
    """
    h, w = mask.shape
    parent: list[int] = []

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    runs: list[tuple[int, int, int, int]] = []  # (row, start, end_exclusive, id)
    prev_row: list[tuple[int, int, int]] = []

    for y in range(h):
        row = mask[y]
        if not row.any():
            prev_row = []
            continue
        padded = np.concatenate([[False], row, [False]])
        edges = np.diff(padded.astype(np.int8))
        starts = np.nonzero(edges == 1)[0]
        ends = np.nonzero(edges == -1)[0]

        current: list[tuple[int, int, int]] = []
        for s, e in zip(starts, ends, strict=False):
            rid = len(parent)
            parent.append(rid)
            runs.append((y, int(s), int(e), rid))
            for ps, pe, pid in prev_row:
                if int(s) <= pe and ps <= int(e):  # 8-connected overlap
                    union(rid, pid)
            current.append((int(s), int(e), rid))
        prev_row = current

    boxes: dict[int, list[int]] = {}
    for y, s, e, rid in runs:
        root = find(rid)
        box = boxes.get(root)
        if box is None:
            boxes[root] = [s, y, e, y, e - s]
        else:
            box[0] = min(box[0], s)
            box[1] = min(box[1], y)
            box[2] = max(box[2], e)
            box[3] = max(box[3], y)
            box[4] += e - s
    return [(b[0], b[1], b[2], b[3], b[4]) for b in boxes.values()]
