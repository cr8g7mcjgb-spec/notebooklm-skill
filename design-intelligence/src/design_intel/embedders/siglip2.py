"""SigLIP2 embedder (recommended default when a GPU/CPU torch stack is present).

SigLIP2-so400m is the strongest open image/text tower for mixed
document-and-natural-image retrieval, and its native aspect-ratio variants
matter for design work where a 3:4 poster must not be centre-cropped into a
square. Install with: pip install "design-intel[torch]"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from ..registry import register_embedder
from .base import BaseEmbedder, ImageInput

DEFAULT_MODEL = "google/siglip2-so400m-patch14-384"
BATCH = 8


@register_embedder("siglip2")
class SigLIP2Embedder(BaseEmbedder):
    name = "siglip2"
    supports_text = True

    def __init__(self, model: str = DEFAULT_MODEL, device: str | None = None, **kwargs: Any) -> None:
        super().__init__(model=model, device=device, **kwargs)
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "siglip2 needs torch + transformers: pip install 'design-intel[torch]'"
            ) from exc

        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model_id = model
        self.processor = AutoProcessor.from_pretrained(model)
        self.model = AutoModel.from_pretrained(
            model, dtype=torch.float16 if self.device == "cuda" else torch.float32
        ).to(self.device)
        self.model.eval()
        self.dim = int(self.model.config.text_config.hidden_size)

    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:
        out: list[np.ndarray] = []
        for start in range(0, len(images), BATCH):
            batch = [self._open(i) for i in images[start : start + BATCH]]
            inputs = self.processor(images=batch, return_tensors="pt").to(self.device)
            with self._torch.inference_mode():
                feats = self.model.get_image_features(**inputs)
            out.append(feats.float().cpu().numpy())
        return self.normalize(np.concatenate(out, axis=0))

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        out: list[np.ndarray] = []
        for start in range(0, len(texts), BATCH * 4):
            batch = list(texts[start : start + BATCH * 4])
            inputs = self.processor(
                text=batch, padding="max_length", truncation=True, return_tensors="pt"
            ).to(self.device)
            with self._torch.inference_mode():
                feats = self.model.get_text_features(**inputs)
            out.append(feats.float().cpu().numpy())
        return self.normalize(np.concatenate(out, axis=0))
