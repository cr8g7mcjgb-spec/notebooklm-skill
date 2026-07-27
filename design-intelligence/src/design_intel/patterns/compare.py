"""Design comparison and critique.

`compare` is symmetric: how alike are two designs, and where exactly do they
diverge. `critique` is directional: score a candidate against a reference set
and say what to change, which is what closes the loop when Claude generates
something and wants to know whether it actually hit the brief.
"""

from __future__ import annotations

import numpy as np

from ..features import CATEGORICAL_SPECS, NUMERIC_SPECS, SPEC_INDEX
from ..style.classifier import resolve
from ..types import ComparisonResult, DesignFeatures, PatternReport

MATCH_TOLERANCE = 0.12  # normalised distance below which two values "match"


def compare_features(
    a: DesignFeatures,
    b: DesignFeatures,
    embedding_similarity: float | None = None,
    top_deltas: int = 8,
) -> ComparisonResult:
    deltas: list[dict[str, object]] = []
    shared: list[str] = []
    weighted_distance = 0.0
    total_weight = 0.0

    for spec in NUMERIC_SPECS:
        va, vb = resolve(a, spec.path), resolve(b, spec.path)
        if va is None or vb is None:
            continue
        na, nb = spec.normalize(float(va)), spec.normalize(float(vb))
        distance = abs(na - nb)
        weighted_distance += distance * spec.importance
        total_weight += spec.importance
        if distance <= MATCH_TOLERANCE:
            shared.append(f"{spec.label} (both ≈ {float(va):.3g})")
        else:
            deltas.append(
                {
                    "feature": spec.path,
                    "label": spec.label,
                    "a": round(float(va), 4),
                    "b": round(float(vb), 4),
                    "distance": round(distance, 3),
                    "direction": "higher in B" if nb > na else "higher in A",
                }
            )

    for spec in CATEGORICAL_SPECS:
        va, vb = resolve(a, spec.path), resolve(b, spec.path)
        if va is None or vb is None:
            continue
        total_weight += spec.importance
        if str(va) == str(vb):
            shared.append(f"{spec.label} (both {va})")
        else:
            weighted_distance += spec.importance
            deltas.append(
                {
                    "feature": spec.path,
                    "label": spec.label,
                    "a": str(va),
                    "b": str(vb),
                    "distance": 1.0,
                    "direction": "differs",
                }
            )

    feature_similarity = 1.0 - (weighted_distance / max(total_weight, 1e-6))
    if embedding_similarity is None:
        similarity = feature_similarity
    else:
        similarity = 0.55 * feature_similarity + 0.45 * float(embedding_similarity)

    style_alignment = _style_alignment(a, b)
    deltas.sort(key=lambda d: -float(d["distance"]) * SPEC_INDEX[str(d["feature"])].importance)
    top = deltas[:top_deltas]

    return ComparisonResult(
        similarity=round(float(np.clip(similarity, 0.0, 1.0)), 3),
        style_alignment=round(style_alignment, 3),
        deltas=top,
        shared=shared[:10],
        suggestions=[_suggest(d) for d in top[:6]],
        verdict=_verdict(similarity, style_alignment),
    )


def _style_alignment(a: DesignFeatures, b: DesignFeatures) -> float:
    """Cosine over the two style-score vectors, on their shared keys."""
    sa = {s.style: s.score for s in a.style.scores}
    sb = {s.style: s.score for s in b.style.scores}
    keys = set(sa) | set(sb)
    if not keys:
        return 0.0
    va = np.array([sa.get(k, 0.0) for k in keys], dtype=np.float32)
    vb = np.array([sb.get(k, 0.0) for k in keys], dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denom) if denom > 1e-9 else 0.0


def _suggest(delta: dict[str, object]) -> str:
    label, direction = delta["label"], delta["direction"]
    if direction == "differs":
        return f"{label}: A is {delta['a']}, B is {delta['b']} — pick one and apply it consistently."
    verb = "increase" if direction == "higher in B" else "reduce"
    return f"To move A toward B, {verb} {label} (currently {delta['a']} vs {delta['b']})."


def _verdict(similarity: float, style_alignment: float) -> str:
    if similarity > 0.85 and style_alignment > 0.85:
        return "Same design language — differences are incidental."
    if similarity > 0.7:
        return "Closely related: same family, different execution."
    if style_alignment > 0.7:
        return "Same stylistic intent, but the execution diverges measurably."
    if similarity > 0.5:
        return "Loosely related — shared conventions, different design language."
    return "Different design languages."


def critique(candidate: DesignFeatures, report: PatternReport, tolerance: float = 1.0) -> dict[str, object]:
    """Score a candidate against a reference consensus and say what to fix."""
    findings: list[dict[str, object]] = []
    passed: list[str] = []
    score_total, weight_total = 0.0, 0.0

    for item in report.consensus:
        spec = SPEC_INDEX.get(item.feature)
        if spec is None:
            continue
        value = resolve(candidate, item.feature)
        if value is None:
            continue
        weight = spec.importance * item.agreement
        weight_total += weight

        if item.kind == "numeric":
            lo, hi = (item.spread or [item.value, item.value])[:2]
            pad = max((float(hi) - float(lo)) * tolerance, (spec.hi - spec.lo) * 0.04)
            lo, hi = float(lo) - pad, float(hi) + pad
            ok = lo <= float(value) <= hi
            if ok:
                passed.append(spec.label)
                score_total += weight
            else:
                findings.append(
                    {
                        "feature": item.feature,
                        "label": spec.label,
                        "expected": f"{lo:.3g}–{hi:.3g}",
                        "actual": round(float(value), 4),
                        "fix": spec.say(item.value),
                        "severity": round(
                            min(
                                1.0,
                                abs(float(value) - float(item.value))
                                / max(spec.hi - spec.lo, 1e-6)
                                * 4,
                            ),
                            2,
                        ),
                    }
                )
        else:
            ok = str(value) == str(item.value)
            if ok:
                passed.append(spec.label)
                score_total += weight
            else:
                findings.append(
                    {
                        "feature": item.feature,
                        "label": spec.label,
                        "expected": item.value,
                        "actual": str(value),
                        "fix": spec.say(item.value),
                        "severity": round(item.agreement, 2),
                    }
                )

    findings.sort(key=lambda f: -float(f["severity"]))
    score = score_total / max(weight_total, 1e-6)
    return {
        "score": round(float(score), 3),
        "grade": _grade(score),
        "passed": passed,
        "findings": findings[:10],
        "reference_count": report.sample_size,
    }


def _grade(score: float) -> str:
    if score >= 0.85:
        return "on-brief"
    if score >= 0.7:
        return "close — minor corrections"
    if score >= 0.5:
        return "off-brief in several dimensions"
    return "does not follow the references"
