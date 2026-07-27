"""Shared fixtures. Samples are generated, never checked in — see tools/make_samples.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from make_samples import generate  # noqa: E402

from design_intel import Config, DesignIntelligence  # noqa: E402


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("samples")
    generate(out, per_style=3)
    return out


@pytest.fixture(scope="session")
def sample_files(samples: Path) -> dict[str, list[Path]]:
    by_style: dict[str, list[Path]] = {}
    for path in sorted(samples.glob("*.png")):
        by_style.setdefault(path.stem.split("_")[0], []).append(path)
    return by_style


@pytest.fixture(scope="session")
def shared_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One cache for the whole run — it is content-addressed, so sharing it is
    safe and turns each test's re-index into a cache hit instead of a re-analysis."""
    return tmp_path_factory.mktemp("cache")


@pytest.fixture
def engine(tmp_path: Path, samples: Path, shared_cache: Path) -> DesignIntelligence:
    config = Config(
        index_path=str(tmp_path / "index"),
        cache_dir=str(shared_cache),
        memory_path=str(tmp_path / "memory"),
        max_workers=2,
    )
    di = DesignIntelligence(config)
    di.index([samples], tags=["poster", "sample"])
    return di
