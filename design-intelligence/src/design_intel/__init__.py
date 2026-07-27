"""Design Intelligence System — a design reasoning engine, not an image RAG.

    from design_intel import DesignIntelligence

    di = DesignIntelligence()
    di.index(["~/design-refs"])
    brief = di.design_brief("swiss editorial poster, restrained palette")
    print(brief["markdown"])
"""

from .config import Config
from .engine import DesignIntelligence, summarize
from .registry import (
    available_embedders,
    available_stores,
    register_embedder,
    register_store,
)
from .types import (
    ComparisonResult,
    DesignFeatures,
    PatternReport,
    Reference,
    SearchHit,
    StyleProfile,
)

__version__ = "1.0.0"

__all__ = [
    "Config",
    "DesignIntelligence",
    "summarize",
    "register_embedder",
    "register_store",
    "available_embedders",
    "available_stores",
    "DesignFeatures",
    "Reference",
    "SearchHit",
    "StyleProfile",
    "PatternReport",
    "ComparisonResult",
    "__version__",
]
