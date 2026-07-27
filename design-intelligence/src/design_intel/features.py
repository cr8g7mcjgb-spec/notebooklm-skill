"""The feature dictionary shared by consensus, comparison and brief generation.

One table defines, for every measurable trait: its human label, its plausible
range (needed to normalise agreement and deltas), and how to phrase it as an
instruction. Adding a trait here makes it flow through pattern extraction,
design briefs and comparison output at once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class FeatureSpec:
    path: str
    label: str
    kind: str = "numeric"  # numeric | categorical
    lo: float = 0.0
    hi: float = 1.0
    importance: float = 1.0
    phrase: Callable[[Any], str] | None = None

    def normalize(self, value: float) -> float:
        span = max(self.hi - self.lo, 1e-6)
        return float(min(1.0, max(0.0, (float(value) - self.lo) / span)))

    def say(self, value: Any) -> str:
        if self.phrase:
            return self.phrase(value)
        if self.kind == "numeric":
            return f"{self.label}: {float(value):.3g}"
        return f"{self.label}: {value}"


def _pct(template: str) -> Callable[[Any], str]:
    """`template` must contain a single {} placeholder for the percentage."""
    return lambda v: template.format(f"{float(v) * 100:.0f}%")


FEATURE_SPECS: list[FeatureSpec] = [
    # layout
    FeatureSpec(
        "layout.whitespace_ratio",
        "whitespace",
        "numeric",
        0.0,
        1.0,
        1.6,
        _pct("keep roughly {} of the canvas empty"),
    ),
    FeatureSpec("layout.ink_density", "ink density", "numeric", 0.0, 1.0, 0.8),
    FeatureSpec(
        "layout.balance",
        "compositional balance",
        "numeric",
        0.0,
        1.0,
        1.1,
        lambda v: (
            "centre the visual mass" if float(v) > 0.75 else "hold the mass deliberately off-centre"
        ),
    ),
    FeatureSpec("layout.rule_of_thirds", "thirds adherence", "numeric", 0.0, 1.0, 0.7),
    FeatureSpec("layout.density_entropy", "density spread", "numeric", 0.0, 1.0, 0.6),
    FeatureSpec("layout.vertical_symmetry", "vertical symmetry", "numeric", 0.0, 1.0, 0.7),
    FeatureSpec("layout.orientation", "orientation", "categorical", importance=0.6),
    # grid
    FeatureSpec(
        "grid.columns",
        "column count",
        "numeric",
        1.0,
        16.0,
        1.5,
        lambda v: f"lay out on a {int(round(float(v)))}-column grid",
    ),
    FeatureSpec("grid.column_confidence", "grid strictness", "numeric", 0.0, 1.0, 1.2),
    FeatureSpec("grid.alignment_score", "alignment discipline", "numeric", 0.0, 1.0, 1.2),
    FeatureSpec(
        "grid.gutter_ratio",
        "gutter width",
        "numeric",
        0.0,
        0.15,
        0.8,
        _pct("set gutters to {} of the canvas width"),
    ),
    FeatureSpec(
        "grid.baseline_unit",
        "baseline rhythm",
        "numeric",
        0.0,
        0.2,
        0.8,
        _pct("lock a baseline step of {} of the canvas height"),
    ),
    # typography
    FeatureSpec(
        "typography.hierarchy_depth",
        "type levels",
        "numeric",
        1.0,
        7.0,
        1.5,
        lambda v: f"use {int(round(float(v)))} distinct type sizes, no more",
    ),
    FeatureSpec(
        "typography.scale_ratio",
        "type scale ratio",
        "numeric",
        1.0,
        2.6,
        1.4,
        lambda v: f"step the type scale by ×{float(v):.3g}",
    ),
    FeatureSpec(
        "typography.text_coverage",
        "text coverage",
        "numeric",
        0.0,
        0.4,
        1.0,
        _pct("let text cover about {} of the canvas"),
    ),
    FeatureSpec(
        "typography.measure",
        "line measure",
        "numeric",
        0.0,
        1.0,
        0.9,
        _pct("hold line length near {} of the content width"),
    ),
    FeatureSpec(
        "typography.stroke_weight",
        "stroke weight",
        "numeric",
        0.0,
        0.6,
        0.9,
        lambda v: (
            "set type light"
            if float(v) < 0.10
            else "set type regular"
            if float(v) < 0.22
            else "set type bold"
        ),
    ),
    FeatureSpec("typography.stroke_contrast", "stroke contrast", "numeric", 0.0, 1.2, 0.7),
    FeatureSpec(
        "typography.dominant_alignment",
        "text alignment",
        "categorical",
        importance=1.3,
        phrase=lambda v: f"set text {v}-aligned",
    ),
    FeatureSpec("typography.letterform_class", "letterform", "categorical", importance=1.0),
    FeatureSpec("typography.density", "text density", "categorical", importance=0.8),
    FeatureSpec("typography.scale_name", "named scale", "categorical", importance=0.9),
    # colour
    FeatureSpec(
        "color.contrast_ratio",
        "text/background contrast",
        "numeric",
        1.0,
        21.0,
        1.4,
        lambda v: f"target ≈{float(v):.1f}:1 contrast between text and ground",
    ),
    FeatureSpec(
        "color.hue_count",
        "hue count",
        "numeric",
        0.0,
        8.0,
        1.5,
        lambda v: f"limit the palette to {int(round(float(v)))} hue famil"
        + ("y" if round(float(v)) == 1 else "ies"),
    ),
    FeatureSpec(
        "color.mean_chroma",
        "saturation",
        "numeric",
        0.0,
        80.0,
        1.2,
        lambda v: (
            "keep colour near-neutral (low chroma)"
            if float(v) < 12
            else "use moderately saturated colour"
            if float(v) < 35
            else "push colour to full saturation"
        ),
    ),
    FeatureSpec("color.chroma_spread", "chroma spread", "numeric", 0.0, 60.0, 0.8),
    FeatureSpec("color.mean_lightness", "overall lightness", "numeric", 0.0, 100.0, 1.0),
    FeatureSpec("color.harmony", "colour harmony", "categorical", importance=1.3),
    FeatureSpec("color.temperature", "colour temperature", "categorical", importance=0.9),
    # composition
    FeatureSpec("composition.orthogonality", "rectilinearity", "numeric", 0.0, 1.0, 1.1),
    FeatureSpec("composition.diagonal_energy", "diagonal energy", "numeric", 0.0, 1.0, 0.9),
    FeatureSpec("composition.detail_level", "detail level", "numeric", 0.0, 1.0, 1.0),
    FeatureSpec("composition.edge_density", "edge density", "numeric", 0.0, 0.3, 0.8),
    FeatureSpec("composition.noise", "grain/noise", "numeric", 0.0, 1.0, 0.8),
    FeatureSpec("composition.entropy", "visual entropy", "numeric", 0.0, 1.0, 0.8),
    FeatureSpec("composition.geometry", "geometry", "categorical", importance=1.1),
]

SPEC_INDEX: dict[str, FeatureSpec] = {spec.path: spec for spec in FEATURE_SPECS}
NUMERIC_SPECS = [s for s in FEATURE_SPECS if s.kind == "numeric"]
CATEGORICAL_SPECS = [s for s in FEATURE_SPECS if s.kind == "categorical"]
