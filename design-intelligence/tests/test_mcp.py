"""MCP surface tests.

Skipped when the `mcp` extra is not installed. These check the contract Claude
depends on — that every tool is registered, documented, and returns a payload
that leads somewhere — rather than re-testing engine logic covered elsewhere.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp", reason="install with: pip install 'design-intel[mcp]'")

from design_intel.mcp import server as mcp_server  # noqa: E402

EXPECTED_TOOLS = {
    "search_reference",
    "extract_style",
    "compare_design",
    "find_common_patterns",
    "build_design_brief",
    "critique_design",
    "index_references",
    "annotate_reference",
    "record_taste",
    "taste_profile",
    "list_styles",
    "index_stats",
}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, engine):
    """Point the module-level server at the test engine."""
    monkeypatch.setattr(mcp_server, "_engine", engine)
    return engine


def call(name: str, **kwargs):
    result = asyncio.run(mcp_server.mcp.call_tool(name, kwargs))
    content = result[0] if isinstance(result, tuple) else result
    # FastMCP returns (content_blocks, structured_result) on recent versions
    if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], dict):
        return result[1]
    text = content[0].text if isinstance(content, list) else str(content)
    return json.loads(text)


def test_every_tool_is_registered_and_documented():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description and len(tool.description) > 40, f"{tool.name} needs a real docstring"


def test_search_returns_summaries_not_raw_features(wired):
    payload = call("search_reference", style="editorial", limit=2)
    assert payload["count"] == 2
    hit = payload["results"][0]
    assert hit["summary"] and hit["why"]
    assert "features" not in hit  # detail=False must stay compact
    assert payload["next_step"]


def test_search_detail_includes_features(wired):
    hit = call("search_reference", style="editorial", limit=1, detail=True)["results"][0]
    assert hit["features"]["color"]["palette"]


def test_brief_tool_returns_markdown_and_tokens(wired):
    payload = call("build_design_brief", intent="poster", style="minimal", limit=3)
    assert payload["markdown"].startswith("# Design brief")
    assert payload["tokens"]["grid"]["columns"] >= 1
    assert payload["rules"]


def test_patterns_tool_tells_claude_how_to_use_it(wired):
    payload = call("find_common_patterns", style="bauhaus", limit=3)
    assert payload["sample_size"] == 3
    assert payload["how_to_use"]
    assert payload["rules"]


def test_critique_tool_round_trips(wired, sample_files):
    payload = call("critique_design", target=str(sample_files["editorial"][0]), style="editorial")
    assert 0.0 <= payload["score"] <= 1.0
    assert payload["grade"]


def test_list_styles_covers_the_taxonomy(wired):
    keys = {s["key"] for s in call("list_styles")["styles"]}
    assert {"swiss_international", "bauhaus", "brutalism", "editorial", "minimal"} <= keys


def test_stats_tool_reports_plugins(wired):
    payload = call("index_stats")
    assert payload["references"] > 0
    assert "siglip2" in payload["available_embedders"]
    assert "qdrant" in payload["available_stores"]


def test_styles_resource_is_readable():
    text = asyncio.run(mcp_server.mcp.read_resource("design://styles"))
    body = text[0].content if isinstance(text, list) else str(text)
    assert "Swiss" in body and "Bauhaus" in body
