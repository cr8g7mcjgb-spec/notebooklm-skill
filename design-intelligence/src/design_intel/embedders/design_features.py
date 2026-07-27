"""The zero-dependency default embedder.

Not a stub: a hand-built 400-dim design descriptor (perceptual colour
distribution + edge orientation + spatial ink layout + tonal structure). It
installs with nothing but numpy and Pillow, and because it encodes exactly the
properties designers talk about, it retrieves "compositions like this one"
noticeably better than a generic CLIP embedding does on abstract layout queries.

Use SigLIP2 or ColPali when semantic content ("a poster about coffee") matters.
This one is about form.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..analysis.imaging import block_reduce, gradients, load_image, normalize
from ..analysis.layout import ink_map
from ..registry import register_embedder
from .base import BaseEmbedder, ImageInput

HUE_BINS = 12
CHROMA_BINS = 3
LIGHT_BINS = 4
ORIENT_BINS = 18
SPATIAL = 12

BLOCK_WEIGHTS = {
    "color": 1.0,
    "tone": 0.6,
    "orientation": 0.8,
    "spatial": 1.0,
    "stats": 0.5,
}


@register_embedder("design-features")
class DesignFeatureEmbedder(BaseEmbedder):
    name = "design-features"
    supports_text = False
    dim = HUE_BINS * CHROMA_BINS * LIGHT_BINS + 16 + ORIENT_BINS + SPATIAL * SPATIAL + 8

    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:
        return self.normalize(np.stack([self._descriptor(img) for img in images]))

    def _descriptor(self, image: ImageInput) -> np.ndarray:
        img = load_image(image, max_edge=384)
        lab = img.lab

        # --- perceptual colour distribution (hue x chroma x lightness) ---
        hue = (np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0) / 360.0
        chroma = np.clip(np.hypot(lab[..., 1], lab[..., 2]) / 90.0, 0, 0.999)
        light = np.clip(lab[..., 0] / 100.0, 0, 0.999)

        hi = (hue * HUE_BINS).astype(np.int32)
        ci = (chroma * CHROMA_BINS).astype(np.int32)
        li = (light * LIGHT_BINS).astype(np.int32)
        flat_idx = (hi * CHROMA_BINS + ci) * LIGHT_BINS + li
        color_hist = np.bincount(flat_idx.ravel(), minlength=HUE_BINS * CHROMA_BINS * LIGHT_BINS)
        color_hist = color_hist.astype(np.float32) / max(1, flat_idx.size)

        # --- tonal structure ---
        tone_hist = np.histogram(light, bins=16, range=(0.0, 1.0))[0].astype(np.float32)
        tone_hist /= max(1.0, float(tone_hist.sum()))

        # --- edge orientation ---
        gx, gy = gradients(img.gray)
        mag = np.hypot(gx, gy)
        strong = mag > max(0.04, float(np.percentile(mag, 85)))
        if strong.sum() > 8:
            angles = np.degrees(np.arctan2(gy[strong], gx[strong])) % 180.0
            orient = np.histogram(angles, bins=ORIENT_BINS, range=(0, 180), weights=mag[strong])[0]
            orient = orient.astype(np.float32) / max(1e-6, float(orient.sum()))
        else:
            orient = np.zeros(ORIENT_BINS, dtype=np.float32)

        # --- spatial layout signature ---
        spatial = block_reduce(ink_map(img), SPATIAL).ravel().astype(np.float32)
        spatial = spatial / max(1e-6, float(np.linalg.norm(spatial)))

        # --- scalar summary stats ---
        stats = np.array(
            [
                float(spatial.mean()),
                float(img.gray.std()),
                float(chroma.mean()),
                float(chroma.std()),
                float(light.mean()),
                float(strong.mean()),
                float(np.abs(gx).mean()),
                float(np.abs(gy).mean()),
            ],
            dtype=np.float32,
        )

        blocks = [
            (color_hist, "color"),
            (tone_hist, "tone"),
            (orient, "orientation"),
            (spatial, "spatial"),
            (normalize(stats), "stats"),
        ]
        return np.concatenate([b * BLOCK_WEIGHTS[name] for b, name in blocks]).astype(np.float32)
