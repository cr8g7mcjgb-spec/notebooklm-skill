"""Consensus palette: merge many references' palettes into one usable set.

Naive averaging turns every palette beige. Instead, pool all swatches as
ratio-weighted points in CIELAB, cluster them, and take each cluster's
weighted centroid — so a red that appears in 6 of 8 references survives as red,
while one-off colours drop out.
"""

from __future__ import annotations

import numpy as np

from ..analysis.imaging import kmeans, lab_to_rgb, to_hex
from ..types import Reference, Swatch

ROLE_PRIORITY = {"background": 0, "text": 1, "accent": 2, "surface": 3, "support": 4}


def consensus_palette(references: list[Reference], size: int = 6) -> list[Swatch]:
    points: list[list[float]] = []
    weights: list[float] = []
    roles: list[str] = []

    for ref in references:
        for swatch in ref.features.color.palette:
            lab = list(swatch.lab)
            if len(lab) != 3:
                continue
            points.append(lab)
            weights.append(max(float(swatch.ratio), 1e-3))
            roles.append(swatch.role)

    if len(points) < 2:
        return []

    data = np.array(points, dtype=np.float32)
    weight_arr = np.array(weights, dtype=np.float32)
    k = min(size, len(data))
    centroids, labels = kmeans(data, k=k)

    swatches: list[Swatch] = []
    total_weight = float(weight_arr.sum())
    for i in range(len(centroids)):
        mask = labels == i
        if not mask.any():
            continue
        cluster_weight = float(weight_arr[mask].sum())
        centroid = (data[mask] * weight_arr[mask, None]).sum(axis=0) / max(cluster_weight, 1e-6)

        member_roles = [r for r, m in zip(roles, mask, strict=False) if m]
        role = min(member_roles, key=lambda r: ROLE_PRIORITY.get(r, 9)) if member_roles else "support"

        rgb = lab_to_rgb(centroid[None, :])[0]
        swatches.append(
            Swatch(
                hex=to_hex(rgb),
                ratio=round(cluster_weight / max(total_weight, 1e-6), 4),
                lab=(
                    round(float(centroid[0]), 2),
                    round(float(centroid[1]), 2),
                    round(float(centroid[2]), 2),
                ),
                hue=round(float(np.degrees(np.arctan2(centroid[2], centroid[1])) % 360.0), 1),
                chroma=round(float(np.hypot(centroid[1], centroid[2])), 2),
                lightness=round(float(centroid[0]), 2),
                role=role,
            )
        )

    swatches.sort(key=lambda s: (ROLE_PRIORITY.get(s.role, 9), -s.ratio))
    return swatches
