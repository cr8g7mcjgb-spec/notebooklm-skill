"""Pluggable vector stores. Only `numpy` imports at package load."""

from .base import BaseStore, matches
from .numpy_store import NumpyStore

__all__ = ["BaseStore", "NumpyStore", "matches"]
