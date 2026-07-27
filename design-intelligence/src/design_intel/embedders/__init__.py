"""Pluggable embedders. Only `design-features` imports at package load."""

from .base import BaseEmbedder, maxsim
from .design_features import DesignFeatureEmbedder

__all__ = ["BaseEmbedder", "DesignFeatureEmbedder", "maxsim"]
