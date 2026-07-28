#!/usr/bin/env python3
"""
Refero Styles MCP server.

Exposes the same lookup the skill uses as MCP tools, so clients without skill
support (Claude Desktop, Cursor, Zed, …) can pull a DESIGN.md too.

Tools:
    refero_search(query, limit)        find style pages for a brand or mood
    refero_design_md(style, render)    the DESIGN.md, verbatim
    refero_tokens(style, format)       parsed tokens as css / tailwind / json

Run:
    pip install "mcp[cli]"
    python refero-styles/mcp/refero_mcp_server.py

Register with Claude Code:
    claude mcp add refero-styles -- python /abs/path/refero-styles/mcp/refero_mcp_server.py

This server talks to the public site over ordinary HTTPS. It does not bypass a
network policy: if the host's egress blocks styles.refero.design, the server is
blocked too, and `refero_probe` will say so.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import argparse  # noqa: E402
import refero_fetch  # noqa: E402
import refero_tokens as tokens_lib  # noqa: E402

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.exit('Missing dependency. Install with:  pip install "mcp[cli]"')

mcp = FastMCP("refero-styles")


def _load_markdown(style, render=False):
    """The DESIGN.md for a slug or URL, verbatim, or None."""
    for url in refero_fetch.slug_to_urls(style):
        status, body, _via = refero_fetch.get(url, render=render)
        if status != 200 or not body.strip():
            continue
        md = refero_fetch.extract_markdown(body, content_is_markdown=url.endswith(".md"))
        if md:
            return md, url
    return None, None


@mcp.tool()
def refero_search(query: str, limit: int = 10) -> str:
    """Find Refero style pages matching a brand name or a mood.

    Args:
        query: brand ("linear", "stripe") or mood ("minimal editorial", "high contrast")
        limit: maximum candidates to return

    Returns one `slug<TAB>url` per line.
    """
    import io
    import contextlib

    args = argparse.Namespace(
        query=[query], limit=limit, render=False, show_browser=False
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = refero_fetch.cmd_search(args)
    return out.getvalue() if code == 0 else f"No results.\n{err.getvalue()}"


@mcp.tool()
def refero_design_md(style: str, render: bool = False) -> str:
    """Fetch one style's DESIGN.md verbatim — theme, colors, typography, spacing,
    radius, elevation, layout, components, imagery, do/don't rules.

    Args:
        style: slug ("linear") or a full styles.refero.design URL
        render: force a real browser (use when a plain fetch is refused)

    Apply the values in the returned document literally. Do not paraphrase them.
    """
    md, url = _load_markdown(style, render=render)
    if not md:
        return (
            f"Could not retrieve a DESIGN.md for {style!r}. "
            "Retry with render=True, or run refero_probe to see what is reachable."
        )
    return f"<!-- source: {url} -->\n\n{md}"


@mcp.tool()
def refero_tokens(style: str, format: str = "css", render: bool = False) -> str:
    """Fetch a style and return its design tokens ready to paste into a build.

    Args:
        style: slug or full styles.refero.design URL
        format: one of css, tailwind, json, python, summary
        render: force a real browser

    Values are carried across exactly as published — no rounding, no substitution.
    """
    if format not in tokens_lib.EMITTERS:
        return f"Unknown format {format!r}. Choose one of: {', '.join(sorted(tokens_lib.EMITTERS))}"
    md, url = _load_markdown(style, render=render)
    if not md:
        return f"Could not retrieve a DESIGN.md for {style!r}."
    parsed = tokens_lib.parse(md)
    if not parsed["colors"] and not parsed["fonts"]:
        return (
            f"Retrieved {url} but recognised no tokens in it. "
            "Call refero_design_md and apply the document by hand."
        )
    return f"/* source: {url} */\n" + tokens_lib.EMITTERS[format](parsed)


@mcp.tool()
def refero_probe() -> str:
    """Report which Refero endpoints this host can actually reach. Use when the
    other tools fail, to tell a site change apart from a network block."""
    lines = []
    for path in ("/", "/sitemap.xml", "/llms.txt", "/?q=minimal"):
        status, body = refero_fetch.http_get(refero_fetch.BASE + path)
        detail = body[:100].replace("\n", " ") if status == 0 else f"{len(body)} chars"
        lines.append(f"{status:>3}  {refero_fetch.BASE + path:<48} {detail}")
    lines.append(
        "\nstatus 0 on every row means this host's egress blocks the domain — "
        "run the server somewhere with normal network access."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
