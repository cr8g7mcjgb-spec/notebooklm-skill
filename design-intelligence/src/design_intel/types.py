"""Core data structures shared by every layer of the Design Intelligence System.

Everything is a plain dataclass with `to_dict()` so the MCP layer can hand
Claude JSON without a serialization framework in the middle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _clean(value: Any) -> Any:
    """Recursively convert dataclasses / numpy scalars into JSON-safe values."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return value
    return value


@dataclass
class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return {k: _clean(v) for k, v in asdict(self).items()}


@dataclass
class Swatch(_Serializable):
    """A single colour in an extracted palette."""

    hex: str
    ratio: float
    lab: tuple[float, float, float]
    hue: float
    chroma: float
    lightness: float
    role: str = "support"  # background | surface | text | accent | support


@dataclass
class ColorProfile(_Serializable):
    palette: list[Swatch] = field(default_factory=list)
    background: str = "#ffffff"
    foreground: str = "#000000"
    accent: str | None = None
    contrast_ratio: float = 1.0
    mean_lightness: float = 0.0
    mean_chroma: float = 0.0
    chroma_spread: float = 0.0
    temperature: str = "neutral"  # warm | cool | neutral
    harmony: str = "unclassified"
    hue_count: int = 0
    is_monochrome: bool = False
    is_high_contrast: bool = False


@dataclass
class LayoutProfile(_Serializable):
    whitespace_ratio: float = 0.0
    ink_density: float = 0.0
    balance: float = 0.0  # 1.0 = mass perfectly centred
    horizontal_symmetry: float = 0.0
    vertical_symmetry: float = 0.0
    rule_of_thirds: float = 0.0
    quadrant_weights: list[float] = field(default_factory=lambda: [0.25] * 4)
    margins: dict[str, float] = field(default_factory=dict)  # fractions of w/h
    focal_points: list[list[float]] = field(default_factory=list)  # [[x, y, w]]
    density_entropy: float = 0.0
    orientation: str = "square"  # portrait | landscape | square
    aspect_ratio: float = 1.0


@dataclass
class GridProfile(_Serializable):
    columns: int = 0
    column_confidence: float = 0.0
    gutter_ratio: float = 0.0
    baseline_unit: float = 0.0
    alignment_score: float = 0.0
    modular: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TypographyProfile(_Serializable):
    text_blocks: int = 0
    text_lines: int = 0
    text_coverage: float = 0.0
    scale_levels: list[float] = field(default_factory=list)  # relative heights
    scale_ratio: float = 0.0
    scale_name: str = "unclassified"
    hierarchy_depth: int = 0
    dominant_alignment: str = "unknown"  # left | center | right | justified
    stroke_contrast: float = 0.0
    stroke_weight: float = 0.0
    letterform_class: str = "unknown"  # grotesque | humanist | serif_display
    measure: float = 0.0  # avg line width as fraction of content width
    density: str = "balanced"  # sparse | balanced | dense


@dataclass
class CompositionProfile(_Serializable):
    orthogonality: float = 0.0  # how much energy is on 0/90 degree edges
    diagonal_energy: float = 0.0
    edge_density: float = 0.0
    detail_level: float = 0.0
    entropy: float = 0.0
    contrast: float = 0.0
    noise: float = 0.0
    geometry: str = "unclassified"  # rectilinear | diagonal | organic | mixed


@dataclass
class StyleScore(_Serializable):
    style: str
    score: float
    label: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class StyleProfile(_Serializable):
    primary: str = "unclassified"
    confidence: float = 0.0
    scores: list[StyleScore] = field(default_factory=list)
    descriptors: list[str] = field(default_factory=list)
    method: str = "rules"  # rules | zeroshot | ensemble


@dataclass
class DesignFeatures(_Serializable):
    """The complete deterministic analysis of one design image."""

    color: ColorProfile = field(default_factory=ColorProfile)
    layout: LayoutProfile = field(default_factory=LayoutProfile)
    grid: GridProfile = field(default_factory=GridProfile)
    typography: TypographyProfile = field(default_factory=TypographyProfile)
    composition: CompositionProfile = field(default_factory=CompositionProfile)
    style: StyleProfile = field(default_factory=StyleProfile)
    width: int = 0
    height: int = 0
    analyzer_version: str = "1.0"


@dataclass
class Reference(_Serializable):
    """One indexed design reference."""

    id: str
    path: str
    content_hash: str
    features: DesignFeatures = field(default_factory=DesignFeatures)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    caption: str = ""
    collection: str = "default"
    indexed_at: float = 0.0
    embedder: str = ""


@dataclass
class SearchHit(_Serializable):
    reference: Reference
    score: float
    rank: int = 0
    signals: dict[str, float] = field(default_factory=dict)
    why: list[str] = field(default_factory=list)


@dataclass
class ConsensusItem(_Serializable):
    """One agreed-upon trait across a set of references."""

    feature: str
    value: Any
    agreement: float
    spread: Any = None
    samples: int = 0
    kind: str = "numeric"  # numeric | categorical | palette


@dataclass
class PatternReport(_Serializable):
    sample_size: int = 0
    consensus: list[ConsensusItem] = field(default_factory=list)
    divergence: list[ConsensusItem] = field(default_factory=list)
    palette: list[Swatch] = field(default_factory=list)
    dominant_styles: list[StyleScore] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class ComparisonResult(_Serializable):
    similarity: float = 0.0
    style_alignment: float = 0.0
    deltas: list[dict[str, Any]] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    verdict: str = ""
