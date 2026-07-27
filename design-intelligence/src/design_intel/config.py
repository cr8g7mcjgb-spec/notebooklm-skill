"""Configuration: file + environment, one object passed to the engine.

Resolution order (later wins): defaults -> config file -> DI_* env vars ->
explicit keyword arguments. YAML is supported when PyYAML is installed; JSON
always is, so a container can be configured without extra dependencies.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

CONFIG_NAMES = ("design-intel.yaml", "design-intel.yml", "design-intel.json", ".design-intel.json")
ENV_PREFIX = "DI_"


@dataclass
class Config:
    # storage
    store: str = "numpy"
    store_options: dict[str, Any] = field(default_factory=dict)
    index_path: str = ".design-index"

    # models
    embedder: str = "design-features"
    embedder_options: dict[str, Any] = field(default_factory=dict)
    text_embedder: str | None = None  # defaults to the image embedder if it has a text tower

    # behaviour
    collection: str = "default"
    cache_dir: str = ".design-cache"
    cache_enabled: bool = True
    max_workers: int = 4
    analyzer_max_edge: int = 768
    zero_shot_weight: float = 0.35

    # search fusion weights (reciprocal rank fusion)
    weight_image: float = 1.0
    weight_text: float = 0.7
    weight_style: float = 0.5
    weight_taste: float = 0.3
    rrf_k: int = 60

    # taste memory
    memory_path: str = ".design-memory"
    memory_enabled: bool = True

    def __post_init__(self) -> None:
        self.store_options.setdefault("path", self.index_path)

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> Config:
        data: dict[str, Any] = {}
        found = _find_config(path)
        if found:
            data.update(_read_config(found))
        data.update(_env_overrides())
        data.update({k: v for k, v in overrides.items() if v is not None})

        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
        config = cls(**data)
        if found:
            config.store_options.setdefault("path", config.index_path)
        return config

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolved_store_options(self) -> dict[str, Any]:
        options = dict(self.store_options)
        options.setdefault("path", self.index_path)
        if self.store == "qdrant":
            options.pop("path", None)  # qdrant is addressed by url, not a directory
        return options


def _find_config(path: str | Path | None) -> Path | None:
    if path:
        candidate = Path(path)
        if not candidate.exists():
            raise FileNotFoundError(f"config file not found: {candidate}")
        return candidate
    env_path = os.environ.get("DESIGN_INTEL_CONFIG")
    if env_path:
        return Path(env_path)
    for directory in (Path.cwd(), *Path.cwd().parents):
        for name in CONFIG_NAMES:
            candidate = directory / name
            if candidate.exists():
                return candidate
        if (directory / ".git").exists():
            break
    return None


def _read_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(f"{path.name} needs PyYAML — or use a .json config instead") from exc
        return yaml.safe_load(text) or {}
    return json.loads(text or "{}")


def _coerce(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.strip().startswith(("{", "[")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _env_overrides() -> dict[str, Any]:
    known = {f.name for f in fields(Config)}
    out: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        name = key[len(ENV_PREFIX) :].lower()
        if name in known:
            out[name] = _coerce(value)
    return out
