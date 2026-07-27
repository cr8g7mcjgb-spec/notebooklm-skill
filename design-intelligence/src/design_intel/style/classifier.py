"""Style classification: rule membership, optionally ensembled with zero-shot VLM.

Rules alone are deterministic, fast, explainable and need no model download.
When an embedder that supports text ("zero-shot") is available, its prompt
scores are blended in — that catches styles the numeric rules under-serve
(illustration idiom, photographic treatment) while the rules keep it anchored.
"""

from __future__ import annotations

from typing import Any

from ..types import DesignFeatures, StyleProfile, StyleScore
from .taxonomy import STYLES, Range, Style


def resolve(features: DesignFeatures | dict[str, Any], path: str) -> Any:
    """Look up a dotted feature path on a DesignFeatures or plain dict."""
    node: Any = features.to_dict() if hasattr(features, "to_dict") else features
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        else:
            node = getattr(node, part, None)
        if node is None:
            return None
    return node


def _membership(value: float, rule: Range) -> float:
    """Trapezoidal membership: 1.0 inside the band, decaying over the shoulder."""
    span = max(rule.high - rule.low, 1e-6)
    slack = span * rule.shoulder
    if rule.low <= value <= rule.high:
        return 1.0
    distance = rule.low - value if value < rule.low else value - rule.high
    return float(max(0.0, 1.0 - distance / max(slack, 1e-6)))


def score_style(features: DesignFeatures, style: Style) -> tuple[float, list[str]]:
    """Weighted mean membership across a style's constraints, plus evidence."""
    total_weight = 0.0
    accumulated = 0.0
    evidence: list[str] = []

    for rule in style.ranges:
        value = resolve(features, rule.path)
        if value is None or isinstance(value, (list, dict)):
            continue
        member = _membership(float(value), rule)
        accumulated += member * rule.weight
        total_weight += rule.weight
        if member >= 0.85 and rule.note:
            evidence.append(f"{rule.note} ({rule.path}={_fmt(value)})")

    for rule in style.categories:
        value = resolve(features, rule.path)
        if value is None:
            continue
        hit = str(value) in rule.accepted
        accumulated += (1.0 if hit else 0.0) * rule.weight
        total_weight += rule.weight
        if hit:
            evidence.append(f"{rule.note or rule.path} = {value}")

    if total_weight <= 0:
        return 0.0, evidence
    return accumulated / total_weight, evidence[:6]


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def classify(
    features: DesignFeatures,
    zero_shot: dict[str, float] | None = None,
    zero_shot_weight: float = 0.35,
    top_k: int = 5,
) -> StyleProfile:
    """Rank styles for one design.

    `zero_shot` maps style key -> 0..1 similarity from a text-capable embedder.
    """
    scored: list[StyleScore] = []
    for style in STYLES:
        rule_score, evidence = score_style(features, style)
        score = rule_score
        method_note = None
        if zero_shot and style.key in zero_shot:
            zs = float(zero_shot[style.key])
            score = (1.0 - zero_shot_weight) * rule_score + zero_shot_weight * zs
            method_note = f"zero-shot {zs:.2f} / rules {rule_score:.2f}"
        scored.append(
            StyleScore(
                style=style.key,
                score=round(float(score), 4),
                label=style.label,
                evidence=evidence + ([method_note] if method_note else []),
            )
        )

    scored.sort(key=lambda s: -s.score)
    top = scored[:top_k]
    primary = top[0]

    # confidence = how far the winner is clear of the runner-up, tempered by
    # its absolute score. Two styles at 0.8 each is genuinely ambiguous.
    margin = primary.score - (top[1].score if len(top) > 1 else 0.0)
    confidence = float(min(1.0, primary.score * (0.55 + 2.0 * margin)))

    style_obj = next((s for s in STYLES if s.key == primary.style), None)
    return StyleProfile(
        primary=primary.style if primary.score >= 0.45 else "unclassified",
        confidence=round(confidence, 3),
        scores=top,
        descriptors=list(style_obj.descriptors) if style_obj else [],
        method="ensemble" if zero_shot else "rules",
    )


def zero_shot_scores(embedder: Any, image_vector: Any) -> dict[str, float]:
    """Score every style's prompts against an image vector, if the embedder can.

    Returns {} for embedders without a text tower, which is the normal case for
    the dependency-free default.
    """
    if not getattr(embedder, "supports_text", False):
        return {}
    import numpy as np

    prompts: list[str] = []
    owners: list[str] = []
    for style in STYLES:
        for prompt in style.prompts:
            prompts.append(prompt)
            owners.append(style.key)
    if not prompts:
        return {}

    text_vectors = embedder.embed_text(prompts)
    image_vector = np.asarray(image_vector, dtype=np.float32).ravel()
    sims = text_vectors @ image_vector

    # softmax over prompts, then max-pool per style: CLIP-family similarities
    # live in a narrow band, so raw cosines would all look identical.
    exp = np.exp((sims - sims.max()) / 0.07)
    probs = exp / max(float(exp.sum()), 1e-9)
    out: dict[str, float] = {}
    for key, prob in zip(owners, probs, strict=False):
        out[key] = max(out.get(key, 0.0), float(prob))
    peak = max(out.values()) if out else 0.0
    if peak > 0:
        out = {k: v / peak for k, v in out.items()}
    return out
