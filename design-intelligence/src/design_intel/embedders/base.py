"""Embedder interface.

Contract: `embed_images` returns an L2-normalised float32 array of shape
(n, dim), so cosine similarity is a plain dot product everywhere downstream.
Text-capable embedders additionally set `supports_text = True` and implement
`embed_text`, which unlocks natural-language search and zero-shot style scoring.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ImageInput = str | Path | Image.Image


class BaseEmbedder(ABC):
    name: str = "base"
    dim: int = 0
    supports_text: bool = False
    # multi-vector (late interaction) embedders expose patch grids for MaxSim
    multi_vector: bool = False

    def __init__(self, **kwargs: Any) -> None:
        self.options = kwargs

    @abstractmethod
    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:
        """(n, dim) L2-normalised float32."""

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError(f"{self.name} has no text tower")

    def embed_patches(self, images: Sequence[ImageInput]) -> list[np.ndarray]:
        """Per-image (patches, dim) arrays. Only for multi_vector embedders."""
        raise NotImplementedError(f"{self.name} is not a multi-vector embedder")

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _open(image: ImageInput) -> Image.Image:
        if isinstance(image, Image.Image):
            return image.convert("RGB")
        return Image.open(image).convert("RGB")

    @staticmethod
    def normalize(vectors: np.ndarray) -> np.ndarray:
        arr = np.asarray(vectors, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr[None, :]
        norms = np.linalg.norm(arr, axis=-1, keepdims=True)
        return (arr / np.maximum(norms, 1e-8)).astype(np.float32)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dim": self.dim,
            "supports_text": self.supports_text,
            "multi_vector": self.multi_vector,
            "options": {k: str(v) for k, v in self.options.items()},
        }


def maxsim(query_patches: np.ndarray, doc_patches: np.ndarray) -> float:
    """ColBERT/ColPali late-interaction score: sum over query of max over doc."""
    sims = query_patches @ doc_patches.T
    return float(sims.max(axis=1).sum() / max(1, query_patches.shape[0]))
