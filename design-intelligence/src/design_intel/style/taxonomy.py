"""The style taxonomy: what each movement actually looks like, numerically.

Each style is a set of soft constraints over measured features. A constraint is
`(feature_path, low, high, weight)` with trapezoidal membership — full credit
inside [low, high], decaying over a shoulder outside it. This keeps the
classifier explainable: every score comes with the constraints that fired,
which is what `extract_style` hands back to Claude as evidence.

Categorical constraints are `(feature_path, {accepted values}, weight)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Range:
    path: str
    low: float
    high: float
    weight: float = 1.0
    shoulder: float = 0.35  # fraction of the span the score decays over
    note: str = ""


@dataclass
class Category:
    path: str
    accepted: set[str]
    weight: float = 1.0
    note: str = ""


@dataclass
class Style:
    key: str
    label: str
    description: str
    descriptors: list[str] = field(default_factory=list)
    ranges: list[Range] = field(default_factory=list)
    categories: list[Category] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)  # for zero-shot VLM scoring
    era: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "descriptors": self.descriptors,
            "era": self.era,
        }


STYLES: list[Style] = [
    Style(
        key="swiss_international",
        label="Swiss / International Typographic",
        description=(
            "Objective, grid-driven composition. Flush-left sans-serif, generous "
            "whitespace, strict column alignment, restrained palette with one accent."
        ),
        descriptors=["grid-driven", "flush-left", "objective", "asymmetric balance", "neutral sans"],
        era="1950s–",
        ranges=[
            Range("grid.column_confidence", 0.35, 1.0, 2.0, note="strong column grid"),
            Range("grid.alignment_score", 0.5, 1.0, 1.6, note="edges snap to few axes"),
            Range("layout.whitespace_ratio", 0.45, 0.85, 1.6, note="generous whitespace"),
            Range("composition.orthogonality", 0.5, 1.0, 1.4, note="rectilinear geometry"),
            Range("color.hue_count", 1, 3, 1.2, note="restrained palette"),
            Range("typography.hierarchy_depth", 2, 4, 1.0, note="clear type hierarchy"),
            Range("composition.detail_level", 0.0, 0.45, 0.8),
        ],
        categories=[
            Category("typography.dominant_alignment", {"left", "justified"}, 1.4, note="flush-left setting"),
            Category("composition.geometry", {"rectilinear"}, 1.0),
            Category("typography.letterform_class", {"grotesque", "grotesque_bold"}, 1.0),
        ],
        prompts=[
            "a Swiss international style poster with a strict typographic grid",
            "Helvetica flush-left layout with generous white space",
        ],
    ),
    Style(
        key="bauhaus",
        label="Bauhaus",
        description=(
            "Primary colours on flat ground, elementary geometry (circle, square, "
            "triangle), diagonal energy, and geometric sans typography set as form."
        ),
        descriptors=["primary colours", "elementary geometry", "diagonal axis", "flat planes"],
        era="1919–1933",
        ranges=[
            Range("color.mean_chroma", 22.0, 70.0, 1.8, note="saturated primaries"),
            Range("color.hue_count", 2, 4, 1.5, note="two to four flat hues"),
            Range("composition.diagonal_energy", 0.3, 0.9, 1.3, note="diagonal composition"),
            Range("composition.detail_level", 0.0, 0.4, 1.2, note="flat, low-detail planes"),
            Range("layout.whitespace_ratio", 0.25, 0.7, 0.9),
            Range("composition.entropy", 0.2, 0.7, 0.8),
        ],
        categories=[
            Category("color.harmony", {"triadic", "complementary", "split-complementary"}, 1.4),
            Category("composition.geometry", {"mixed", "diagonal", "rectilinear"}, 0.6),
        ],
        prompts=[
            "a Bauhaus poster with primary colours and geometric shapes",
            "constructivist geometric composition with circle square triangle",
        ],
    ),
    Style(
        key="brutalism",
        label="Brutalism / Raw Web",
        description=(
            "Deliberately unrefined: extreme contrast, system defaults, dense text, "
            "little whitespace discipline, visible structure with no decoration."
        ),
        descriptors=["raw", "extreme contrast", "system type", "dense", "undecorated"],
        era="2014–",
        ranges=[
            Range("color.contrast_ratio", 10.0, 21.0, 1.8, note="near-maximum contrast"),
            Range("color.hue_count", 0, 2, 1.4, note="black/white dominant"),
            Range("layout.whitespace_ratio", 0.0, 0.45, 2.6, note="little breathing room"),
            Range("typography.text_coverage", 0.05, 0.35, 1.3, note="dense text"),
            Range("grid.alignment_score", 0.0, 0.45, 0.9, note="loose alignment"),
            Range("color.mean_chroma", 0.0, 14.0, 1.0),
        ],
        categories=[Category("color.is_high_contrast", {"True"}, 1.0)],
        prompts=[
            "a brutalist website with raw black and white system typography",
            "harsh high contrast undecorated layout",
        ],
    ),
    Style(
        key="neo_brutalism",
        label="Neo-Brutalism",
        description=(
            "Brutalism made playful: thick black outlines, hard offset shadows, "
            "loud saturated blocks on flat ground, chunky geometric sans."
        ),
        descriptors=["thick outlines", "hard shadows", "loud blocks", "chunky sans"],
        era="2020–",
        ranges=[
            Range("color.mean_chroma", 25.0, 80.0, 1.7, note="loud saturated blocks"),
            Range("color.contrast_ratio", 8.0, 21.0, 1.5, note="black outlines on colour"),
            Range("composition.orthogonality", 0.45, 1.0, 1.2),
            Range("typography.stroke_weight", 0.16, 0.6, 1.2, note="heavy letterforms"),
            Range("composition.detail_level", 0.0, 0.45, 0.9),
            Range("color.hue_count", 2, 5, 1.0),
        ],
        categories=[Category("typography.letterform_class", {"grotesque_bold"}, 1.2)],
        prompts=[
            "neo-brutalist UI with thick black borders and hard drop shadows",
            "bold saturated blocks with heavy outlines",
        ],
    ),
    Style(
        key="editorial",
        label="Editorial / Magazine",
        description=(
            "Print-derived hierarchy: multi-column text, a dominant display size "
            "against small body copy, wide measure, serif or high-contrast display face."
        ),
        descriptors=["multi-column", "display/body contrast", "print rhythm", "wide measure"],
        era="print tradition",
        ranges=[
            Range("typography.hierarchy_depth", 3, 6, 1.8, note="deep type hierarchy"),
            Range("typography.scale_ratio", 1.3, 2.6, 1.5, note="dramatic display jump"),
            Range("grid.columns", 2, 12, 1.3, note="multi-column setting"),
            Range("typography.text_coverage", 0.03, 0.22, 1.2),
            Range("typography.text_lines", 8, 200, 1.2, note="substantial copy"),
            Range("layout.whitespace_ratio", 0.2, 0.65, 0.8),
        ],
        categories=[
            Category("typography.letterform_class", {"serif_display", "humanist_light"}, 1.2),
            Category("typography.dominant_alignment", {"justified", "left"}, 0.8),
        ],
        prompts=[
            "an editorial magazine spread with headline and multi-column body text",
            "print layout with serif display type and columns",
        ],
    ),
    Style(
        key="minimal",
        label="Minimal / Reductive",
        description=(
            "Very high whitespace, one or two elements, tiny type, near-neutral "
            "palette. Everything non-essential removed."
        ),
        descriptors=["extreme whitespace", "single focus", "near-neutral", "quiet"],
        era="—",
        ranges=[
            Range("layout.whitespace_ratio", 0.7, 1.0, 2.2, note="dominant empty space"),
            Range("typography.text_coverage", 0.0, 0.035, 1.5, note="minimal text"),
            Range("typography.text_lines", 0, 8, 1.6, note="almost no content"),
            Range("color.mean_chroma", 0.0, 16.0, 1.3, note="desaturated"),
            Range("composition.edge_density", 0.0, 0.1, 1.2),
            Range("color.hue_count", 0, 2, 1.0),
            Range("composition.entropy", 0.0, 0.5, 0.9),
        ],
        categories=[],
        prompts=["a minimalist layout with vast empty space", "quiet reductive composition"],
    ),
    Style(
        key="corporate_modern",
        label="Corporate Modern / SaaS",
        description=(
            "Product-marketing default: soft neutral ground, one brand hue, "
            "moderate contrast, card grids, balanced density, rounded geometry."
        ),
        descriptors=["brand hue + neutrals", "card grid", "balanced density", "safe contrast"],
        era="2015–",
        ranges=[
            Range("color.contrast_ratio", 3.5, 12.0, 1.3, note="accessible but not harsh"),
            Range("color.hue_count", 1, 3, 1.2, note="one brand hue over neutrals"),
            Range("layout.whitespace_ratio", 0.4, 0.75, 1.3),
            Range("grid.columns", 2, 12, 1.2),
            Range("color.mean_chroma", 6.0, 32.0, 1.1),
            Range("layout.balance", 0.55, 1.0, 1.0),
            Range("typography.hierarchy_depth", 2, 4, 0.9),
        ],
        categories=[Category("composition.geometry", {"rectilinear", "mixed"}, 0.8)],
        prompts=["a modern SaaS landing page with cards and a single brand colour"],
    ),
    Style(
        key="japanese_minimal",
        label="Japanese Minimal / Ma",
        description=(
            "Asymmetric emptiness used as an active element. Off-centre focal point, "
            "very low chroma, fine hairline rules, small quiet type."
        ),
        descriptors=["ma (negative space)", "asymmetry", "hairlines", "low chroma"],
        era="—",
        ranges=[
            Range("layout.whitespace_ratio", 0.65, 0.98, 2.0),
            Range("typography.text_lines", 0, 10, 1.4, note="almost no content"),
            Range("layout.balance", 0.0, 0.6, 1.5, note="deliberately off-centre"),
            Range("color.mean_chroma", 0.0, 12.0, 1.5),
            Range("typography.text_coverage", 0.0, 0.05, 1.2),
            Range("color.mean_lightness", 60.0, 100.0, 1.0, note="light ground"),
        ],
        categories=[],
        prompts=["a Japanese minimal poster with asymmetric negative space"],
    ),
    Style(
        key="y2k",
        label="Y2K / Chrome",
        description=(
            "Gradients, chrome, glow, heavy detail, cool metallic hues, "
            "diagonal energy and layered noise."
        ),
        descriptors=["gradients", "chrome", "glow", "layered", "metallic"],
        era="1998–2005",
        ranges=[
            Range("composition.detail_level", 0.4, 1.0, 1.6, note="dense fine detail"),
            Range("composition.noise", 0.25, 1.0, 1.3),
            Range("color.chroma_spread", 12.0, 60.0, 1.3, note="wide chroma range"),
            Range("composition.diagonal_energy", 0.3, 1.0, 1.0),
            Range("composition.entropy", 0.5, 1.0, 1.2),
        ],
        categories=[Category("color.temperature", {"cool"}, 0.8)],
        prompts=["a Y2K chrome gradient poster with glossy metallic type"],
    ),
    Style(
        key="memphis",
        label="Memphis / Postmodern",
        description=(
            "Clashing saturated hues, scattered geometric confetti, patterned "
            "grounds, no grid discipline, playful asymmetry."
        ),
        descriptors=["clashing hues", "scattered shapes", "pattern", "anti-grid"],
        era="1981–1988",
        ranges=[
            Range("color.hue_count", 4, 8, 1.8, note="many competing hues"),
            Range("color.mean_chroma", 25.0, 80.0, 1.5),
            Range("grid.column_confidence", 0.0, 0.5, 1.2, note="no strict grid"),
            Range("layout.density_entropy", 0.5, 1.0, 1.1),
            Range("composition.diagonal_energy", 0.25, 1.0, 0.9),
        ],
        categories=[Category("color.harmony", {"polychrome", "tetradic"}, 1.2)],
        prompts=["a Memphis design poster with scattered colourful geometric shapes"],
    ),
    Style(
        key="glassmorphism",
        label="Glassmorphism / Soft UI",
        description=(
            "Translucent layered surfaces over a blurred colourful ground, soft "
            "gradients, low local contrast, rounded rectangles."
        ),
        descriptors=["translucency", "blur", "soft gradient", "layered surfaces"],
        era="2020–",
        ranges=[
            Range("color.contrast_ratio", 1.0, 4.5, 1.7, note="soft, low contrast"),
            Range("composition.detail_level", 0.0, 0.35, 1.4, note="blurred, low detail"),
            Range("color.mean_chroma", 10.0, 45.0, 1.2),
            Range("composition.edge_density", 0.0, 0.09, 1.2),
            Range("color.chroma_spread", 6.0, 35.0, 0.9),
        ],
        categories=[],
        prompts=["a glassmorphism UI with frosted translucent cards over a gradient"],
    ),
    Style(
        key="retro_print",
        label="Retro Print / Risograph",
        description=(
            "Limited spot-ink palette, visible grain and misregistration, warm "
            "paper ground, flat overprinted shapes."
        ),
        descriptors=["spot inks", "grain", "overprint", "warm paper"],
        era="—",
        ranges=[
            Range("color.hue_count", 2, 4, 1.5, note="two to three spot inks"),
            Range("composition.noise", 0.3, 1.0, 1.6, note="visible grain"),
            Range("color.mean_chroma", 15.0, 55.0, 1.1),
            Range("composition.detail_level", 0.3, 0.9, 1.0),
        ],
        categories=[Category("color.temperature", {"warm"}, 1.1)],
        prompts=["a risograph print poster with two spot inks and visible grain"],
    ),
]

STYLE_INDEX: dict[str, Style] = {s.key: s for s in STYLES}


def get_style(key: str) -> Style | None:
    key = key.lower().strip().replace("-", "_").replace(" ", "_")
    if key in STYLE_INDEX:
        return STYLE_INDEX[key]
    for style in STYLES:
        if key in style.key or key in style.label.lower().replace(" ", "_"):
            return style
    return None
