"""Composition analysis: edge geometry, detail level, contrast, noise.

The orthogonality/diagonal split is what separates Swiss rigour from a
diagonal-driven constructivist or Y2K composition, so it carries real weight in
the style classifier downstream.
"""

from __future__ import annotations

import numpy as np

from ..types import CompositionProfile
from .imaging import LoadedImage, box_blur, edge_magnitude, entropy, gradients

MIN_EDGE_ENERGY = 0.04


def analyze_composition(img: LoadedImage) -> CompositionProfile:
    gray = img.gray
    gx, gy = gradients(gray)
    mag = np.hypot(gx, gy)

    strong = mag > max(MIN_EDGE_ENERGY, float(np.percentile(mag, 88)))
    edge_density = float(strong.mean())

    if strong.sum() < 16:
        return CompositionProfile(
            edge_density=round(edge_density, 4),
            detail_level=0.0,
            entropy=round(entropy(gray.ravel()), 3),
            contrast=round(float(gray.std()), 3),
            geometry="flat",
        )

    angles = np.degrees(np.arctan2(gy[strong], gx[strong])) % 180.0
    weights = mag[strong]
    hist, _ = np.histogram(angles, bins=36, range=(0.0, 180.0), weights=weights)
    hist = hist / max(float(hist.sum()), 1e-6)

    # bins 0-1 and 34-35 are ~horizontal edges; 17-19 are ~vertical
    ortho = float(hist[[0, 1, 35, 34, 17, 18, 19]].sum())
    diagonal = float(hist[[8, 9, 10, 26, 27, 28]].sum())
    ortho_expected = 7 / 36
    diagonal_expected = 6 / 36
    orthogonality = float(np.clip(ortho / ortho_expected / 3.0, 0.0, 1.0))
    diagonal_energy = float(np.clip(diagonal / diagonal_expected / 3.0, 0.0, 1.0))

    # detail = how much edge energy is destroyed by blurring, i.e. how much of
    # the image lives at fine scale
    coarse = edge_magnitude(box_blur(gray, radius=max(2, min(gray.shape) // 100)))
    detail = float(np.clip(1.0 - coarse.sum() / max(float(mag.sum()), 1e-6), 0.0, 1.0))

    # noise/grain = residual texture in the *flat* areas. Measuring it over the
    # whole image (an earlier attempt) just re-measures edge density and reports
    # ~0.9 for every design that contains text.
    flat = mag < float(np.percentile(mag, 45))
    if flat.sum() > 64:
        residual = gray - box_blur(gray, radius=2)
        noise = float(np.clip(residual[flat].std() / 0.04, 0.0, 1.0))
    else:
        noise = 0.0

    if orthogonality > 0.55 and diagonal_energy < 0.35:
        geometry = "rectilinear"
    elif diagonal_energy > 0.5 and orthogonality < 0.5:
        geometry = "diagonal"
    elif orthogonality < 0.3 and diagonal_energy < 0.4:
        geometry = "organic"
    else:
        geometry = "mixed"

    return CompositionProfile(
        orthogonality=round(orthogonality, 3),
        diagonal_energy=round(diagonal_energy, 3),
        edge_density=round(edge_density, 4),
        detail_level=round(detail, 3),
        entropy=round(entropy(gray.ravel()), 3),
        contrast=round(float(gray.std()), 3),
        noise=round(noise, 3),
        geometry=geometry,
    )
