"""Command line interface.

    design-intel index ~/refs --tags poster,swiss
    design-intel search --text "editorial grid, restrained palette" --limit 5
    design-intel brief  --text "conference poster" --out brief.md
    design-intel critique my-draft.png --style swiss_international
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import Config
from .engine import DesignIntelligence, summarize
from .registry import available_embedders, available_stores


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _engine(args: argparse.Namespace) -> DesignIntelligence:
    overrides: dict[str, Any] = {}
    for key in ("store", "embedder", "collection", "index_path"):
        value = getattr(args, key, None)
        if value:
            overrides[key] = value
    return DesignIntelligence(Config.load(getattr(args, "config", None), **overrides))


def _search_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if getattr(args, "tags", None):
        filters["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if getattr(args, "collection", None):
        filters["collection"] = args.collection
    kwargs: dict[str, Any] = {}
    if getattr(args, "text", None):
        kwargs["text"] = args.text
    if getattr(args, "image", None):
        kwargs["image"] = args.image
    if getattr(args, "like", None):
        kwargs["like_id"] = args.like
    if getattr(args, "style", None):
        kwargs["style"] = args.style
    if filters:
        kwargs["filters"] = filters
    return kwargs


# -- commands -------------------------------------------------------------


def cmd_index(args: argparse.Namespace) -> int:
    di = _engine(args)

    def progress(_: str, done: int, total: int) -> None:
        if not args.quiet:
            print(f"\r  indexing {done}/{total}", end="", file=sys.stderr, flush=True)

    report = di.index(
        args.paths,
        tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
        recursive=not args.no_recursive,
        force=args.force,
        prune=args.prune,
        progress=progress,
    )
    if not args.quiet:
        print(file=sys.stderr)
    _emit(report.to_dict(), args.json)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    di = _engine(args)
    hits = di.search(limit=args.limit, **_search_kwargs(args))
    if args.json:
        _emit([h.to_dict() for h in hits], True)
        return 0
    if not hits:
        print("no matches")
        return 0
    for hit in hits:
        ref = hit.reference
        print(f"{hit.rank:2d}. {ref.path}  [{hit.score:.3f}]  id={ref.id}")
        print(f"    {summarize(ref.features)}")
        if hit.why:
            print(f"    why: {'; '.join(hit.why[:3])}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    di = _engine(args)
    result = di.extract_style(args.target)
    if args.json:
        _emit(result, True)
    else:
        print(f"{args.target}\n  {result['summary']}")
        for score in result["style"]["scores"][:4]:
            print(f"  {score['score']:.2f}  {score['label']}")
            for line in score["evidence"][:3]:
                print(f"          - {line}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    di = _engine(args)
    result = di.compare(args.a, args.b)
    if args.json:
        _emit(result.to_dict(), True)
    else:
        print(f"similarity {result.similarity:.3f} | style alignment {result.style_alignment:.3f}")
        print(f"verdict: {result.verdict}\n")
        for delta in result.deltas:
            print(f"  {delta['label']}: A={delta['a']} B={delta['b']} ({delta['direction']})")
        if result.suggestions:
            print("\nsuggestions:")
            for s in result.suggestions:
                print(f"  - {s}")
    return 0


def cmd_patterns(args: argparse.Namespace) -> int:
    di = _engine(args)
    report = di.find_common_patterns(
        ids=args.ids or None, limit=args.limit, min_agreement=args.min_agreement, **_search_kwargs(args)
    )
    if args.json:
        _emit(report.to_dict(), True)
    else:
        print(f"consensus across {report.sample_size} references:\n")
        for rule in report.rules:
            print(f"  - {rule}")
        if report.divergence:
            print("\nfree choices (references disagree):")
            for item in report.divergence[:6]:
                print(f"  - {item.feature} (agreement {item.agreement:.2f})")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    di = _engine(args)
    brief = di.design_brief(
        intent=args.text or args.intent or "",
        ids=args.ids or None,
        limit=args.limit,
        **_search_kwargs(args),
    )
    if args.out:
        Path(args.out).write_text(brief["markdown"], encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    if args.json:
        _emit(brief, True)
    else:
        print(brief["markdown"])
    return 0


def cmd_critique(args: argparse.Namespace) -> int:
    di = _engine(args)
    result = di.critique(args.target, ids=args.ids or None, limit=args.limit, **_search_kwargs(args))
    if args.json:
        _emit(result, True)
    else:
        print(f"score {result['score']:.2f} — {result['grade']}")
        print(f"compared against {result['reference_count']} references\n")
        for finding in result["findings"]:
            print(f"  ! {finding['label']}: {finding['actual']} (expected {finding['expected']})")
            print(f"      → {finding['fix']}")
        if result["passed"]:
            print(f"\n  on-target: {', '.join(result['passed'][:8])}")
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    di = _engine(args)
    _emit(di.feedback(args.ref_id, args.verdict, note=args.note or ""), args.json)
    return 0


def cmd_taste(args: argparse.Namespace) -> int:
    _emit(_engine(args).taste_profile(), args.json)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _emit(_engine(args).stats(), args.json)
    return 0


def cmd_styles(args: argparse.Namespace) -> int:
    styles = DesignIntelligence.styles()
    if args.json:
        _emit(styles, True)
    else:
        for style in styles:
            print(f"{style['key']:22s} {style['label']}")
            print(f"{'':22s} {style['description']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .mcp.server import main as serve_main

    serve_main(config_path=getattr(args, "config", None), transport=args.transport)
    return 0


# -- parser ---------------------------------------------------------------


def _global_flags(suppress: bool) -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand.

    Argparse only accepts parser-level options *before* the subcommand, but
    `design-intel index refs --json` is what everyone actually types, so the same
    flags are attached to every subparser too.

    The subparser copy uses SUPPRESS defaults: without it, a subparser would
    write its own default back over a value the user passed *before* the
    subcommand, silently discarding `design-intel --json index refs`.
    """
    default = argparse.SUPPRESS if suppress else None
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=default, help="path to design-intel.yaml/json")
    common.add_argument("--store", choices=available_stores(), default=default)
    common.add_argument("--embedder", choices=available_embedders(), default=default)
    common.add_argument("--index-path", default=default)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
        help="machine-readable output",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _global_flags(suppress=True)
    parser = argparse.ArgumentParser(
        prog="design-intel", description=__doc__, parents=[_global_flags(suppress=False)]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="index or re-index a folder of references", parents=[common])
    p.add_argument("paths", nargs="+")
    p.add_argument("--collection")
    p.add_argument("--tags")
    p.add_argument("--force", action="store_true", help="ignore the cache and reindex everything")
    p.add_argument("--prune", action="store_true", help="drop records whose files are gone")
    p.add_argument("--no-recursive", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="hybrid reference search", parents=[common])
    p.add_argument("--text")
    p.add_argument("--image")
    p.add_argument("--like", help="find references similar to this indexed id")
    p.add_argument("--style")
    p.add_argument("--tags")
    p.add_argument("--collection")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("analyze", help="extract style + full feature analysis", parents=[common])
    p.add_argument("target", help="image path or indexed reference id")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("compare", help="compare two designs", parents=[common])
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("patterns", help="extract what the top references agree on", parents=[common])
    p.add_argument("--ids", nargs="*")
    p.add_argument("--text")
    p.add_argument("--image")
    p.add_argument("--style")
    p.add_argument("--tags")
    p.add_argument("--collection")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--min-agreement", type=float, default=0.62)
    p.set_defaults(func=cmd_patterns)

    p = sub.add_parser("brief", help="generate a design brief from references", parents=[common])
    p.add_argument("intent", nargs="?", help="what you are designing")
    p.add_argument("--ids", nargs="*")
    p.add_argument("--text")
    p.add_argument("--image")
    p.add_argument("--style")
    p.add_argument("--tags")
    p.add_argument("--collection")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--out")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("critique", help="score a candidate design against references", parents=[common])
    p.add_argument("target")
    p.add_argument("--ids", nargs="*")
    p.add_argument("--text")
    p.add_argument("--style")
    p.add_argument("--tags")
    p.add_argument("--collection")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_critique)

    p = sub.add_parser("feedback", help="teach the taste memory", parents=[common])
    p.add_argument("ref_id")
    p.add_argument("verdict", choices=["like", "dislike", "keep", "reject", "used", "skip"])
    p.add_argument("--note")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("taste", help="show the learned taste profile", parents=[common])
    p.set_defaults(func=cmd_taste)

    p = sub.add_parser("stats", help="index statistics", parents=[common])
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("styles", help="list the style taxonomy", parents=[common])
    p.set_defaults(func=cmd_styles)

    p = sub.add_parser("serve", help="run the MCP server", parents=[common])
    p.add_argument("--transport", default="stdio", choices=["stdio", "http", "sse"])
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (KeyError, ValueError, FileNotFoundError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
