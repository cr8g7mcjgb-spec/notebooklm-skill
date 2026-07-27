"""Rehydrate stored JSON back into typed dataclasses.

Written by hand rather than pulled from pydantic so the core package stays
dependency-free, and so an index written by an older analyzer version still
loads (unknown keys are dropped, missing keys fall back to defaults).
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, TypeVar

from .types import (
    ColorProfile,
    CompositionProfile,
    DesignFeatures,
    GridProfile,
    LayoutProfile,
    Reference,
    StyleProfile,
    StyleScore,
    Swatch,
    TypographyProfile,
)

T = TypeVar("T")


def _build(cls: type[T], data: Any) -> T:
    if not isinstance(data, dict):
        return cls()  # type: ignore[call-arg]
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in data.items() if k in known})  # type: ignore[arg-type]


def color_from_dict(data: Any) -> ColorProfile:
    profile = _build(ColorProfile, data or {})
    profile.palette = [_build(Swatch, s) for s in (data or {}).get("palette", []) if isinstance(s, dict)]
    for swatch in profile.palette:
        if isinstance(swatch.lab, list):
            swatch.lab = tuple(swatch.lab)  # type: ignore[assignment]
    return profile


def style_from_dict(data: Any) -> StyleProfile:
    profile = _build(StyleProfile, data or {})
    profile.scores = [_build(StyleScore, s) for s in (data or {}).get("scores", []) if isinstance(s, dict)]
    return profile


def features_from_dict(data: Any) -> DesignFeatures:
    data = data or {}
    features = _build(DesignFeatures, data)
    features.color = color_from_dict(data.get("color"))
    features.layout = _build(LayoutProfile, data.get("layout"))
    features.grid = _build(GridProfile, data.get("grid"))
    features.typography = _build(TypographyProfile, data.get("typography"))
    features.composition = _build(CompositionProfile, data.get("composition"))
    features.style = style_from_dict(data.get("style"))
    return features


def reference_from_dict(data: dict[str, Any]) -> Reference:
    ref = _build(Reference, data)
    ref.features = features_from_dict(data.get("features"))
    return ref


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    return value
