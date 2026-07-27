"""MCP server — the Claude-facing surface.

Tool design follows one rule: **return decisions, not dumps.** A tool that hands
back 40 KB of feature JSON burns context and teaches Claude nothing. Every tool
here returns a short natural-language summary alongside the structured payload,
and the expensive raw analysis is opt-in via `detail=True`.

Run with:  design-intel serve         (stdio, for Claude Desktop / Claude Code)
           design-intel serve --transport http
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..engine import DesignIntelligence, summarize
from ..registry import available_embedders, available_stores

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - depends on optional extra
    raise ImportError("the MCP server needs: pip install 'design-intel[mcp]'") from exc


mcp = FastMCP(
    "design-intelligence",
    instructions=(
        "A design reasoning engine over an indexed reference library. "
        "Typical flow when asked to design something: call find_common_patterns or "
        "build_design_brief FIRST to get measured constraints (grid, type scale, "
        "palette, whitespace targets), design against those constraints, then call "
        "critique_design on the result to check it landed. search_reference is for "
        "finding specific references; it is not a substitute for the brief."
    ),
)

_engine: DesignIntelligence | None = None
_config_path: str | None = None


def engine() -> DesignIntelligence:
    global _engine
    if _engine is None:
        _engine = DesignIntelligence(Config.load(_config_path))
    return _engine


def _hit_payload(hit: Any, detail: bool) -> dict[str, Any]:
    ref = hit.reference
    payload: dict[str, Any] = {
        "id": ref.id,
        "path": ref.path,
        "score": hit.score,
        "rank": hit.rank,
        "style": ref.features.style.primary,
        "summary": summarize(ref.features),
        "tags": ref.tags,
        "why": hit.why,
    }
    if ref.caption:
        payload["caption"] = ref.caption
    if detail:
        payload["features"] = ref.features.to_dict()
        payload["signals"] = hit.signals
    return payload


# -- tools -----------------------------------------------------------------


@mcp.tool()
def search_reference(
    text: str | None = None,
    image_path: str | None = None,
    like_id: str | None = None,
    style: str | None = None,
    tags: list[str] | None = None,
    collection: str | None = None,
    limit: int = 6,
    detail: bool = False,
) -> dict[str, Any]:
    """Find design references by any combination of description, example image,
    similar-to-this-reference, style name, and tags.

    Signals are fused with reciprocal rank fusion, so partial queries work: text
    alone, an image alone, or "like this one but Swiss" all behave sensibly.

    Args:
        text: what you are looking for, in plain language.
        image_path: absolute path to an image to search by visual similarity.
        like_id: an indexed reference id — find more like it.
        style: restrict/steer toward a taxonomy style (see list_styles).
        tags: only references carrying at least one of these tags.
        collection: restrict to one collection.
        limit: how many to return (default 6).
        detail: include the full feature analysis per hit (verbose).
    """
    filters: dict[str, Any] = {}
    if tags:
        filters["tags"] = tags
    if collection:
        filters["collection"] = collection

    hits = engine().search(
        text=text,
        image=image_path,
        like_id=like_id,
        style=style,
        filters=filters or None,
        limit=limit,
    )
    return {
        "count": len(hits),
        "results": [_hit_payload(h, detail) for h in hits],
        "next_step": (
            "call find_common_patterns with these ids to turn them into design rules"
            if hits
            else "index some references first with index_references"
        ),
    }


@mcp.tool()
def extract_style(target: str, detail: bool = True) -> dict[str, Any]:
    """Analyse one design and name its style, with the evidence behind the call.

    Works on any image on disk or on an indexed reference id. Returns the style
    ranking plus the measured colour, layout, grid, typography and composition
    profile that produced it.

    Args:
        target: image path or indexed reference id.
        detail: include the full numeric feature analysis (default true).
    """
    result = engine().extract_style(target)
    if not detail:
        result.pop("features", None)
    return result


@mcp.tool()
def compare_design(a: str, b: str) -> dict[str, Any]:
    """Compare two designs and report exactly where they diverge.

    Use this to check whether a draft matches a reference, or to articulate the
    difference between two directions. Returns similarity, style alignment, the
    largest measured deltas, and concrete suggestions to move A toward B.

    Args:
        a: image path or reference id.
        b: image path or reference id.
    """
    return engine().compare(a, b).to_dict()


@mcp.tool()
def find_common_patterns(
    ids: list[str] | None = None,
    text: str | None = None,
    style: str | None = None,
    tags: list[str] | None = None,
    limit: int = 8,
    min_agreement: float = 0.62,
) -> dict[str, Any]:
    """Extract what a set of references actually agrees on — the core tool.

    Give it reference ids, or a query to select them. Returns hard rules (traits
    the references agree on, with agreement scores) and free choices (traits they
    disagree on, where you can invent). Use this before designing anything.

    Args:
        ids: specific reference ids; omit to select by query instead.
        text: query to pick references, when ids are not given.
        style: style to pick references by.
        tags: tag filter.
        limit: how many references to reason over (default 8).
        min_agreement: 0..1 threshold for calling something consensus.
    """
    filters = {"tags": tags} if tags else None
    report = engine().find_common_patterns(
        ids=ids,
        limit=limit,
        min_agreement=min_agreement,
        **({"text": text} if text else {}),
        **({"style": style} if style else {}),
        **({"filters": filters} if filters else {}),
    )
    data = report.to_dict()
    data["how_to_use"] = (
        "Treat `rules` as hard constraints and `divergence` as free choices. "
        "Call build_design_brief for the same analysis as tokens you can paste into code."
    )
    return data


@mcp.tool()
def build_design_brief(
    intent: str,
    ids: list[str] | None = None,
    text: str | None = None,
    style: str | None = None,
    tags: list[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Turn references into a design brief with concrete tokens.

    This is what to call before generating any visual work. Returns a markdown
    brief (direction, hard constraints, palette, free choices, checklist) plus
    machine-usable tokens: colour variables, a type scale, grid columns, gutters,
    spacing steps and target whitespace/contrast.

    Args:
        intent: what you are designing, e.g. "conference poster for a type foundry".
        ids: specific references to derive from; omit to select by query.
        text: query for selecting references (defaults to `intent`).
        style: style to select references by.
        tags: tag filter.
        limit: how many references to derive from (default 8).
    """
    filters = {"tags": tags} if tags else None
    kwargs: dict[str, Any] = {}
    if text or intent:
        kwargs["text"] = text or intent
    if style:
        kwargs["style"] = style
    if filters:
        kwargs["filters"] = filters
    return engine().design_brief(intent=intent, ids=ids, limit=limit, **kwargs)


@mcp.tool()
def critique_design(
    target: str,
    ids: list[str] | None = None,
    text: str | None = None,
    style: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Score a design against a reference set and list what to fix.

    Call this on your own output after generating something. Returns a 0..1
    score, a grade, which constraints passed, and ranked findings each with the
    measured value, the expected range, and the fix.

    Args:
        target: image path (usually a render of what you just made) or reference id.
        ids: references to judge against; omit to select by query.
        text: query for selecting references.
        style: style to judge against.
        limit: how many references to judge against.
    """
    kwargs: dict[str, Any] = {}
    if text:
        kwargs["text"] = text
    if style:
        kwargs["style"] = style
    return engine().critique(target, ids=ids, limit=limit, **kwargs)


@mcp.tool()
def index_references(
    paths: list[str],
    collection: str | None = None,
    tags: list[str] | None = None,
    force: bool = False,
    prune: bool = False,
) -> dict[str, Any]:
    """Index images into the reference library (incremental — unchanged files are skipped).

    Args:
        paths: files or folders to index.
        collection: name this batch, for later filtering.
        tags: tags applied to everything in this batch.
        force: reindex even if the content hash is unchanged.
        prune: drop indexed records whose files no longer exist.
    """
    report = engine().index(
        paths, collection=collection, tags=tags or [], force=force, prune=prune
    )
    return report.to_dict()


@mcp.tool()
def annotate_reference(
    ref_id: str,
    caption: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a caption, tags or metadata to a reference so keyword search finds it.

    Args:
        ref_id: the reference to annotate.
        caption: a sentence describing it.
        tags: tags to add (merged with existing).
        metadata: arbitrary key/values (merged with existing).
    """
    ref = engine().annotate(ref_id, caption=caption, tags=tags, metadata=metadata)
    return {"id": ref.id, "caption": ref.caption, "tags": ref.tags, "metadata": ref.metadata}


@mcp.tool()
def record_taste(ref_id: str, verdict: str, note: str = "") -> dict[str, Any]:
    """Teach the taste memory which references the user actually likes.

    Positive verdicts pull future search results toward that reference and shape
    generated briefs. Call it when the user praises or rejects a direction.

    Args:
        ref_id: the reference being judged.
        verdict: like | keep | used | dislike | reject | skip.
        note: why, in the user's words — stored for later review.
    """
    return engine().feedback(ref_id, verdict, note=note)


@mcp.tool()
def taste_profile() -> dict[str, Any]:
    """Report the aesthetic learned from recorded feedback.

    Returns the styles the user gravitates to and the traits they are
    consistent about (with a consistency score), or a not-ready message when
    there is not enough feedback yet.
    """
    return engine().taste_profile()


@mcp.tool()
def list_styles() -> dict[str, Any]:
    """List the style taxonomy: keys, labels, descriptions and era."""
    return {"styles": DesignIntelligence.styles()}


@mcp.tool()
def index_stats() -> dict[str, Any]:
    """Report library size, collections, style distribution, model and cache state."""
    stats = engine().stats()
    stats["available_embedders"] = available_embedders()
    stats["available_stores"] = available_stores()
    return stats


# -- resources --------------------------------------------------------------


@mcp.resource("design://styles")
def styles_resource() -> str:
    """The full style taxonomy as readable text."""
    lines = []
    for style in DesignIntelligence.styles():
        lines.append(f"## {style['label']} (`{style['key']}`) — {style['era']}")
        lines.append(style["description"])
        lines.append("descriptors: " + ", ".join(style["descriptors"]) + "\n")
    return "\n".join(lines)


@mcp.resource("design://stats")
def stats_resource() -> str:
    import json

    return json.dumps(engine().stats(), indent=2, default=str)


def main(config_path: str | Path | None = None, transport: str = "stdio") -> None:
    global _config_path
    _config_path = str(config_path) if config_path else None
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
