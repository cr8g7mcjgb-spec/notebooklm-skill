"""ColPali / ColQwen2 late-interaction embedder.

ColPali is the ViDoRe state of the art for visual document retrieval: it keeps
one vector per image patch and scores with MaxSim instead of pooling everything
into a single embedding. For design work that means a query can latch onto the
headline block or the chart in the corner rather than the average of the page.

Cost: ~1k vectors per image. The strategy here is the standard two-stage one —
store a pooled vector for cheap ANN recall, keep the patch grid on disk, and
rerank the shortlist with MaxSim. `search.hybrid` wires that up automatically
whenever `multi_vector` is True.

Install with: pip install "design-intel[colpali]"
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ..registry import register_embedder
from .base import BaseEmbedder, ImageInput

DEFAULT_MODEL = "vidore/colqwen2-v1.0"
BATCH = 2


@register_embedder("colpali")
class ColPaliEmbedder(BaseEmbedder):
    name = "colpali"
    supports_text = True
    multi_vector = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        device: str | None = None,
        patch_cache: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, device=device, **kwargs)
        try:
            import torch
            from colpali_engine.models import ColQwen2, ColQwen2Processor
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "colpali needs colpali-engine: pip install 'design-intel[colpali]'"
            ) from exc

        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = ColQwen2.from_pretrained(
            model, dtype=torch.bfloat16 if self.device == "cuda" else torch.float32, device_map=self.device
        ).eval()
        self.processor = ColQwen2Processor.from_pretrained(model)
        self.dim = int(self.model.dim) if hasattr(self.model, "dim") else 128
        self.patch_cache = Path(patch_cache) if patch_cache else None
        if self.patch_cache:
            self.patch_cache.mkdir(parents=True, exist_ok=True)

    def _forward_images(self, images: Sequence[ImageInput]) -> list[np.ndarray]:
        grids: list[np.ndarray] = []
        for start in range(0, len(images), BATCH):
            batch = [self._open(i) for i in images[start : start + BATCH]]
            inputs = self.processor.process_images(batch).to(self.device)
            with self._torch.inference_mode():
                out = self.model(**inputs)
            grids.extend(g.float().cpu().numpy().astype(np.float32) for g in out)
        return grids

    def embed_patches(self, images: Sequence[ImageInput]) -> list[np.ndarray]:
        return [self.normalize(g) for g in self._forward_images(images)]

    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:
        """Mean-pooled patch grid: the cheap first-stage recall vector."""
        pooled = [g.mean(axis=0) for g in self._forward_images(images)]
        return self.normalize(np.stack(pooled))

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        return self.normalize(np.stack([g.mean(axis=0) for g in self._forward_text(texts)]))

    def embed_text_patches(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [self.normalize(g) for g in self._forward_text(texts)]

    def _forward_text(self, texts: Sequence[str]) -> list[np.ndarray]:
        grids: list[np.ndarray] = []
        for start in range(0, len(texts), BATCH * 4):
            inputs = self.processor.process_queries(list(texts[start : start + BATCH * 4])).to(self.device)
            with self._torch.inference_mode():
                out = self.model(**inputs)
            grids.extend(g.float().cpu().numpy().astype(np.float32) for g in out)
        return grids
