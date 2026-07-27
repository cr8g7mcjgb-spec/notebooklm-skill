"""OpenCLIP embedder — the swap-in alternative to SigLIP2.

Kept because OpenCLIP ships a far wider checkpoint zoo (DFN, LAION-2B, DataComp)
and some of those are better tuned for illustration and poster art than SigLIP.
Install with: pip install "design-intel[openclip]"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from ..registry import register_embedder
from .base import BaseEmbedder, ImageInput

DEFAULT_MODEL = "ViT-L-14-quickgelu"
DEFAULT_PRETRAINED = "dfn2b"
BATCH = 8


@register_embedder("openclip")
class OpenCLIPEmbedder(BaseEmbedder):
    name = "openclip"
    supports_text = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        pretrained: str = DEFAULT_PRETRAINED,
        device: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, pretrained=pretrained, device=device, **kwargs)
        try:
            import open_clip
            import torch
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "openclip needs open_clip_torch: pip install 'design-intel[openclip]'"
            ) from exc

        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model, pretrained=pretrained, device=self.device
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(model)
        with torch.inference_mode():
            probe = self.model.encode_text(self.tokenizer(["probe"]).to(self.device))
        self.dim = int(probe.shape[-1])

    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:
        out: list[np.ndarray] = []
        for start in range(0, len(images), BATCH):
            batch = self._torch.stack(
                [self.preprocess(self._open(i)) for i in images[start : start + BATCH]]
            ).to(self.device)
            with self._torch.inference_mode():
                feats = self.model.encode_image(batch)
            out.append(feats.float().cpu().numpy())
        return self.normalize(np.concatenate(out, axis=0))

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        tokens = self.tokenizer(list(texts)).to(self.device)
        with self._torch.inference_mode():
            feats = self.model.encode_text(tokens)
        return self.normalize(feats.float().cpu().numpy())
