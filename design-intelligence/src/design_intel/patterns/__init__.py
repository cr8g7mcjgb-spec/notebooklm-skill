"""Cross-reference reasoning: consensus, briefs, comparison, critique."""

from .brief import build_brief, build_tokens, to_css
from .compare import compare_features, critique
from .consensus import find_common_patterns
from .palette import consensus_palette

__all__ = [
    "find_common_patterns",
    "build_brief",
    "build_tokens",
    "to_css",
    "compare_features",
    "critique",
    "consensus_palette",
]
