"""A tiny plugin registry.

Embedders and vector stores are looked up by name so swapping SigLIP2 for
OpenCLIP, or LanceDB for Qdrant, is a one-line config change. Third parties can
register their own without touching this package:

    from design_intel.registry import register_embedder

    @register_embedder("my-model")
    class MyEmbedder(BaseEmbedder): ...

Entry points named `design_intel.embedders` / `design_intel.stores` are also
loaded on first lookup.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_EMBEDDERS: dict[str, Any] = {}
_STORES: dict[str, Any] = {}

# name -> module path, imported lazily so that a missing torch install only
# matters if you actually ask for a torch-backed embedder.
_LAZY_EMBEDDERS = {
    "design-features": "design_intel.embedders.design_features",
    "siglip2": "design_intel.embedders.siglip2",
    "openclip": "design_intel.embedders.openclip",
    "colpali": "design_intel.embedders.colpali",
}
_LAZY_STORES = {
    "numpy": "design_intel.stores.numpy_store",
    "lancedb": "design_intel.stores.lancedb_store",
    "qdrant": "design_intel.stores.qdrant_store",
}


def register_embedder(name: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        _EMBEDDERS[name] = cls
        return cls

    return decorator


def register_store(name: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        _STORES[name] = cls
        return cls

    return decorator


def _load_entry_points(group: str) -> None:
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group=group):
            try:
                ep.load()
            except Exception:
                continue
    except Exception:
        return


def _resolve(name: str, table: dict[str, Any], lazy: dict[str, str], group: str, kind: str) -> Any:
    key = name.lower().strip()
    if key in table:
        return table[key]
    if key in lazy:
        importlib.import_module(lazy[key])
        if key in table:
            return table[key]
    _load_entry_points(group)
    if key in table:
        return table[key]
    known = sorted(set(table) | set(lazy))
    raise KeyError(f"unknown {kind} '{name}'. available: {', '.join(known)}")


def get_embedder_class(name: str) -> Any:
    return _resolve(name, _EMBEDDERS, _LAZY_EMBEDDERS, "design_intel.embedders", "embedder")


def get_store_class(name: str) -> Any:
    return _resolve(name, _STORES, _LAZY_STORES, "design_intel.stores", "store")


def available_embedders() -> list[str]:
    return sorted(set(_EMBEDDERS) | set(_LAZY_EMBEDDERS))


def available_stores() -> list[str]:
    return sorted(set(_STORES) | set(_LAZY_STORES))
