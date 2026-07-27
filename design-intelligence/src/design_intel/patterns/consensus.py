"""Common-pattern extraction — the part that makes this a reasoning engine.

Given the top-N references for a query, work out what they actually agree on.
Agreement is measured per feature: tight numeric spread (robust IQR against the
feature's plausible range) or a dominant category. High-agreement traits become
rules; low-agreement traits are reported as *free choices*, which is equally
useful — knowing where the references disagree is knowing where you can invent.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from ..features import CATEGORICAL_SPECS, NUMERIC_SPECS, FeatureSpec
from ..style.classifier import resolve
from ..types import ConsensusItem, PatternReport, Reference, StyleScore, Swatch
from .palette import consensus_palette

CONSENSUS_THRESHOLD = 0.62
DIVERGENCE_THRESHOLD = 0.38
MIN_SAMPLES = 2


def find_common_patterns(
    references: list[Reference],
    min_agreement: float = CONSENSUS_THRESHOLD,
    max_rules: int = 14,
) -> PatternReport:
    if len(references) < MIN_SAMPLES:
        return PatternReport(
            sample_size=len(references),
            sources=[r.path for r in references],
            rules=["not enough references to establish a pattern — index or select at least 2"],
        )

    consensus: list[ConsensusItem] = []
    divergence: list[ConsensusItem] = []

    for spec in NUMERIC_SPECS:
        item = _numeric_consensus(references, spec)
        if item is None:
            continue
        (consensus if item.agreement >= min_agreement else divergence).append(item)

    for spec in CATEGORICAL_SPECS:
        item = _categorical_consensus(references, spec)
        if item is None:
            continue
        (consensus if item.agreement >= min_agreement else divergence).append(item)

    consensus.sort(key=lambda i: -i.agreement)
    divergence.sort(key=lambda i: i.agreement)

    palette = consensus_palette(references)
    styles = _dominant_styles(references)

    return PatternReport(
        sample_size=len(references),
        consensus=consensus,
        divergence=[d for d in divergence if d.agreement <= DIVERGENCE_THRESHOLD][:8],
        palette=palette,
        dominant_styles=styles,
        rules=_to_rules(consensus, palette, styles, max_rules),
        sources=[r.path for r in references],
    )


def _values(references: list[Reference], path: str) -> list[float]:
    out: list[float] = []
    for ref in references:
        value = resolve(ref.features, path)
        if value is None or isinstance(value, (list, dict, str, bool)):
            continue
        out.append(float(value))
    return out


def _numeric_consensus(references: list[Reference], spec: FeatureSpec) -> ConsensusItem | None:
    values = _values(references, spec.path)
    if len(values) < MIN_SAMPLES:
        return None
    arr = np.array(values, dtype=np.float64)
    median = float(np.median(arr))
    q1, q3 = (float(x) for x in np.percentile(arr, [25, 75]))
    span = max(spec.hi - spec.lo, 1e-6)

    # agreement falls off with the interquartile spread relative to how much of
    # the feature's plausible range the references could have used
    agreement = float(np.clip(1.0 - (q3 - q1) / (span * 0.35), 0.0, 1.0))

    return ConsensusItem(
        feature=spec.path,
        value=round(median, 4),
        agreement=round(agreement, 3),
        spread=[round(q1, 4), round(q3, 4)],
        samples=len(values),
        kind="numeric",
    )


def _categorical_consensus(references: list[Reference], spec: FeatureSpec) -> ConsensusItem | None:
    values = [
        str(resolve(ref.features, spec.path))
        for ref in references
        if resolve(ref.features, spec.path) not in (None, "unknown", "unclassified")
    ]
    if len(values) < MIN_SAMPLES:
        return None
    counts = Counter(values)
    top, hits = counts.most_common(1)[0]
    return ConsensusItem(
        feature=spec.path,
        value=top,
        agreement=round(hits / len(values), 3),
        spread=[f"{k}:{v}" for k, v in counts.most_common(4)],
        samples=len(values),
        kind="categorical",
    )


def _dominant_styles(references: list[Reference], top_k: int = 3) -> list[StyleScore]:
    """Average each style's score across the set, not just count the winners."""
    totals: dict[str, list[float]] = {}
    labels: dict[str, str] = {}
    for ref in references:
        for score in ref.features.style.scores:
            totals.setdefault(score.style, []).append(score.score)
            labels[score.style] = score.label or score.style
    ranked = sorted(
        (
            StyleScore(
                style=key,
                score=round(float(np.mean(scores)), 4),
                label=labels.get(key, key),
                evidence=[f"present in {len(scores)}/{len(references)} references"],
            )
            for key, scores in totals.items()
        ),
        key=lambda s: -s.score,
    )
    return ranked[:top_k]


def _to_rules(
    consensus: list[ConsensusItem],
    palette: list[Swatch],
    styles: list[StyleScore],
    max_rules: int,
) -> list[str]:
    """Turn agreed traits into imperative instructions Claude can follow."""
    from ..features import SPEC_INDEX

    rules: list[str] = []
    if styles and styles[0].score >= 0.45:
        lead = styles[0]
        second = styles[1] if len(styles) > 1 and styles[1].score >= 0.4 else None
        rules.append(
            f"Work in the {lead.label} idiom"
            + (f", with a {second.label} undertone." if second else ".")
        )

    if palette:
        swatches = ", ".join(f"{s.hex} ({s.role}, {s.ratio * 100:.0f}%)" for s in palette[:5])
        rules.append(f"Use this palette and these proportions: {swatches}.")

    for item in consensus:
        spec = SPEC_INDEX.get(item.feature)
        # a rule is only worth stating if it can be acted on: either the spec
        # knows how to phrase it as an instruction, or it is important enough
        # that the bare number still guides a decision
        if spec is None or (spec.phrase is None and spec.importance < 1.2):
            continue
        rules.append(
            spec.say(item.value).capitalize().rstrip(".")
            + f". (agreement {item.agreement * 100:.0f}%)"
        )
        if len(rules) >= max_rules:
            break
    return rules
