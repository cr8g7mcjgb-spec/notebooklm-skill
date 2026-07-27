"""The analysis pipeline: image in, complete DesignFeatures out."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from ..style.classifier import classify, zero_shot_scores
from ..types import DesignFeatures
from .color import analyze_color
from .composition import analyze_composition
from .grid import analyze_grid
from .imaging import LoadedImage, load_image
from .layout import analyze_layout, ink_map
from .typography import analyze_typography

ANALYZER_VERSION = "1.0"


def analyze_image(
    source: str | Path | Image.Image | LoadedImage,
    embedder: Any | None = None,
    image_vector: Any | None = None,
) -> DesignFeatures:
    """Run every analyzer over one design.

    Pass `embedder` + `image_vector` to ensemble zero-shot style scores on top
    of the rule-based classifier; omit them for the fast deterministic path.
    """
    img = source if isinstance(source, LoadedImage) else load_image(source)

    # the ink map is expensive and both layout and grid want it
    ink = ink_map(img)

    features = DesignFeatures(
        color=analyze_color(img),
        layout=analyze_layout(img, ink=ink),
        grid=analyze_grid(img, ink=ink),
        typography=analyze_typography(img),
        composition=analyze_composition(img),
        width=img.width,
        height=img.height,
        analyzer_version=ANALYZER_VERSION,
    )

    zero_shot = None
    if embedder is not None and image_vector is not None:
        try:
            zero_shot = zero_shot_scores(embedder, image_vector) or None
        except Exception:  # a missing text tower must never break indexing
            zero_shot = None

    features.style = classify(features, zero_shot=zero_shot)
    return features
