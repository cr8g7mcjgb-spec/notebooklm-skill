"""Design brief generation — the payload that actually changes what Claude makes.

A retrieved image is not usable guidance: Claude cannot see the grid in a JPEG
while it writes CSS. A brief is. It converts the consensus of the top-N
references into (a) hard constraints in words and (b) concrete design tokens,
so the next generated artifact inherits the references' proportions, palette and
rhythm instead of falling back to model defaults.
"""

from __future__ import annotations

from typing import Any

from ..features import SPEC_INDEX
from ..types import PatternReport

BASE_FONT_PX = 16.0
STEP_NAMES = ["body", "lead", "h3", "h2", "h1", "display"]


def build_tokens(report: PatternReport) -> dict[str, Any]:
    """Concrete, copy-pasteable design tokens implied by the reference set."""
    values = {item.feature: item.value for item in report.consensus}
    values.update({item.feature: item.value for item in report.divergence})

    ratio = float(values.get("typography.scale_ratio") or 1.25)
    ratio = min(max(ratio, 1.05), 2.4)
    levels = int(round(float(values.get("typography.hierarchy_depth") or 4)))
    levels = min(max(levels, 2), 6)

    type_scale = {}
    for i in range(levels):
        name = STEP_NAMES[i] if i < len(STEP_NAMES) else f"step{i}"
        type_scale[name] = f"{BASE_FONT_PX * (ratio ** i) / BASE_FONT_PX:.3f}rem"

    columns = int(round(float(values.get("grid.columns") or 12)))
    gutter = float(values.get("grid.gutter_ratio") or 0.02)
    baseline = float(values.get("grid.baseline_unit") or 0.0)
    whitespace = float(values.get("layout.whitespace_ratio") or 0.5)

    # spacing scale derived from the reference baseline rhythm where one exists,
    # otherwise a 4px system nudged by how airy the references are
    base_space = round(max(4.0, min(16.0, baseline * 900.0))) if baseline else round(4 + whitespace * 6)
    spacing = {f"space-{i}": f"{int(base_space * m)}px" for i, m in enumerate([0.5, 1, 2, 3, 5, 8], start=1)}

    color_tokens: dict[str, str] = {}
    for swatch in report.palette:
        key = swatch.role if swatch.role not in color_tokens else f"{swatch.role}-{len(color_tokens)}"
        color_tokens[key] = swatch.hex

    return {
        "color": color_tokens,
        "typography": {
            "scale_ratio": round(ratio, 3),
            "levels": levels,
            "scale": type_scale,
            "alignment": values.get("typography.dominant_alignment", "left"),
            "letterform": values.get("typography.letterform_class", "grotesque"),
            "measure_fraction": round(float(values.get("typography.measure") or 0.6), 3),
        },
        "grid": {
            "columns": columns,
            "gutter": f"{gutter * 100:.1f}%",
            "baseline": f"{baseline * 100:.2f}%" if baseline else None,
            "margin": f"{max(4.0, whitespace * 12):.1f}%",
        },
        "spacing": spacing,
        "targets": {
            "whitespace_ratio": round(whitespace, 3),
            "contrast_ratio": round(float(values.get("color.contrast_ratio") or 7.0), 2),
            "hue_families": int(round(float(values.get("color.hue_count") or 2))),
        },
    }


def to_css(tokens: dict[str, Any]) -> str:
    lines = [":root {"]
    for name, value in tokens["color"].items():
        lines.append(f"  --color-{name}: {value};")
    for name, value in tokens["typography"]["scale"].items():
        lines.append(f"  --text-{name}: {value};")
    for name, value in tokens["spacing"].items():
        lines.append(f"  --{name}: {value};")
    lines.append(f"  --grid-columns: {tokens['grid']['columns']};")
    lines.append(f"  --grid-gutter: {tokens['grid']['gutter']};")
    lines.append(f"  --page-margin: {tokens['grid']['margin']};")
    lines.append("}")
    return "\n".join(lines)


def build_brief(
    report: PatternReport,
    intent: str = "",
    include_tokens: bool = True,
) -> dict[str, Any]:
    """A markdown brief plus machine-readable tokens."""
    tokens = build_tokens(report)
    md: list[str] = []

    title = f"Design brief — {intent}" if intent else "Design brief"
    md.append(f"# {title}")
    md.append(
        f"Derived from {report.sample_size} reference"
        f"{'s' if report.sample_size != 1 else ''} by measured consensus."
    )

    if report.dominant_styles:
        md.append("\n## Direction")
        for style in report.dominant_styles:
            md.append(f"- **{style.label}** — mean fit {style.score:.2f}")

    md.append("\n## Hard constraints")
    for rule in report.rules:
        md.append(f"- {rule}")

    if report.palette:
        md.append("\n## Palette")
        md.append("| swatch | role | share |")
        md.append("| --- | --- | --- |")
        for swatch in report.palette:
            md.append(f"| `{swatch.hex}` | {swatch.role} | {swatch.ratio * 100:.0f}% |")

    if report.divergence:
        md.append("\n## Free choices")
        md.append("The references disagree here — treat these as open, not underspecified:")
        for item in report.divergence[:6]:
            spec = SPEC_INDEX.get(item.feature)
            label = spec.label if spec else item.feature
            md.append(f"- {label} (agreement only {item.agreement * 100:.0f}%)")

    if include_tokens:
        md.append("\n## Tokens")
        md.append("```css")
        md.append(to_css(tokens))
        md.append("```")

    md.append("\n## Checklist before you ship")
    md.append(f"- [ ] Whitespace lands near {tokens['targets']['whitespace_ratio'] * 100:.0f}% of the canvas")
    md.append(f"- [ ] Text/ground contrast ≥ {tokens['targets']['contrast_ratio']:.1f}:1")
    families = tokens["targets"]["hue_families"]
    md.append(f"- [ ] No more than {families} hue famil{'y' if families == 1 else 'ies'}")
    md.append(f"- [ ] Every element aligns to the {tokens['grid']['columns']}-column grid")
    md.append(f"- [ ] Exactly {tokens['typography']['levels']} type sizes in use")

    return {
        "markdown": "\n".join(md),
        "tokens": tokens,
        "rules": report.rules,
        "sources": report.sources,
        "sample_size": report.sample_size,
    }
