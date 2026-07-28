#!/usr/bin/env python3
"""
Crawl every public style on styles.refero.design into a local index, then search
it by content rather than by slug.

Why an index: the site publishes thousands of open DESIGN.md documents. Matching
a request like "minimal warm editorial" against slug strings fails — the words
live in the documents, not the URLs. Crawling once makes every color, font,
heading and adjective searchable, and the winning slug is still fetched live at
build time so the values applied are current.

Commands:
    crawl [--limit N] [--refresh]   discover slugs and index their DESIGN.md
    find <query> [--limit N]        rank indexed styles against a description
    stats                           what the index currently holds

The crawler identifies itself, honours robots.txt, runs a small worker pool, and
sleeps between requests. It writes JSONL incrementally, so an interrupted crawl
resumes where it stopped.
"""

import argparse
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refero_fetch as rf  # noqa: E402
import refero_tokens as rt  # noqa: E402

INDEX_PATH = rf.CACHE_DIR / "index.jsonl"

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "with", "in", "on", "to", "style",
    "styles", "design", "like", "feel", "vibe", "make", "it", "me", "my", "this",
}


# --------------------------------------------------------------------------
# Politeness
# --------------------------------------------------------------------------

def robots_checker():
    """A callable(url) -> bool. Fails open only if robots.txt is unreachable."""
    parser = urllib.robotparser.RobotFileParser()
    status, body = rf.http_get(f"{rf.BASE}/robots.txt")
    if status == 200 and body.strip():
        parser.parse(body.splitlines())
        return lambda url: parser.can_fetch(rf.USER_AGENT, url)
    if status == 404:
        return lambda url: True
    print(
        f"warning: could not read robots.txt (status {status}); crawling conservatively",
        file=sys.stderr,
    )
    return lambda url: True


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

# Style URLs are /style/<uuid> — the slug carries no brand name, so the listing
# pages are the only place the name and tagline exist. Harvest them together.
ANCHOR_RE = re.compile(
    r'<a[^>]+href="(?:https?://[^"]*)?/style/([0-9a-fA-F][0-9a-fA-F-]{7,})"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def discover_entries(limit=None, verbose=True):
    """[{slug, name, tagline}] for every style the site lists."""
    entries, seen = [], set()

    def add(slug, name="", tagline=""):
        low = slug.lower()
        if low in seen:
            # A later page may supply the name a sitemap entry lacked.
            if name:
                for e in entries:
                    if e["slug"].lower() == low and not e["name"]:
                        e["name"], e["tagline"] = name, tagline
            return
        seen.add(low)
        entries.append({"slug": slug, "name": name, "tagline": tagline})

    def take_anchors(html):
        for slug, inner in ANCHOR_RE.findall(html):
            lines = [ln.strip() for ln in rf._strip_tags(inner).splitlines() if ln.strip()]
            add(slug, lines[0] if lines else "", " ".join(lines[1:3]))

    def take_slugs(text):
        for slug in rf.SLUG_RE.findall(text):
            add(slug)

    queue = [f"{rf.BASE}/sitemap.xml", f"{rf.BASE}/sitemap-0.xml", f"{rf.BASE}/llms.txt"]
    visited = set()

    # Listing pages first — they are the only source of names and taglines.
    page = 1
    while limit is None or len(entries) < limit:
        before = len(entries)
        status, body, _via = rf.get(f"{rf.BASE}/?page={page}")
        if status != 200 or not body.strip():
            break
        take_anchors(body)
        take_slugs(body)
        if len(entries) == before:
            break
        if verbose:
            print(f"  page {page}: {len(entries)} styles so far", file=sys.stderr)
        page += 1
        if page > 100:
            break
        time.sleep(0.25)

    # Sitemaps catch anything the listings paginate past.
    while queue and (limit is None or len(entries) < limit):
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        status, body = rf.http_get(url)
        if status != 200 or not body.strip():
            continue
        if verbose:
            print(f"  discovered from {url}", file=sys.stderr)
        take_slugs(body)
        for child in re.findall(r"<loc>\s*([^<\s]+sitemap[^<\s]*)\s*</loc>", body, re.IGNORECASE):
            if child not in visited:
                queue.append(child)

    named = sum(1 for e in entries if e["name"])
    if verbose:
        print(f"  {len(entries)} styles, {named} with names", file=sys.stderr)
    return entries[:limit] if limit else entries


def discover_slugs(limit=None, verbose=True):
    return [e["slug"] for e in discover_entries(limit=limit, verbose=verbose)]


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------

def summarize(slug, url, md, name="", tagline=""):
    """The searchable record for one style."""
    tokens = rt.parse(md)
    sections = rt.split_sections(md)
    title = next((h for h in sections if h), slug)
    body = re.sub(r"[#*`|>_\[\]()-]+", " ", md)
    body = re.sub(r"\s+", " ", body).strip()
    return {
        "slug": slug,
        "name": name,
        "tagline": tagline,
        "url": url,
        "title": name or title,
        "headings": [h for h in sections if h][:24],
        "colors": list(tokens["colors"].values())[:16],
        "color_names": list(tokens["colors"].keys())[:16],
        "fonts": list(tokens["fonts"].values())[:6],
        "spacing": tokens["spacing"][:12],
        "radius": tokens["radius"][:8],
        "text": body[:1800],
        "chars": len(md),
    }


def load_index():
    if not INDEX_PATH.exists():
        return []
    out = []
    for line in INDEX_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def cmd_crawl(args):
    allowed = robots_checker()

    existing = {e["slug"].lower() for e in load_index()} if not args.refresh else set()
    if args.refresh and INDEX_PATH.exists():
        INDEX_PATH.unlink()
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("discovering slugs…", file=sys.stderr)
    found = discover_entries(limit=args.limit)
    todo = [e for e in found if e["slug"].lower() not in existing]
    print(
        f"{len(found)} styles found, {len(existing)} already indexed, {len(todo)} to fetch",
        file=sys.stderr,
    )
    if not todo:
        return 0

    lock = threading.Lock()
    counts = {"ok": 0, "fail": 0, "blocked": 0}
    handle = INDEX_PATH.open("a", encoding="utf-8")

    def work(entry):
        slug = entry["slug"]
        url = rf.page_url(slug)
        if not allowed(url):
            with lock:
                counts["blocked"] += 1
            return
        time.sleep(args.delay)
        md, source, _log = rf.fetch_asset(slug, "design-md", render=args.render)
        with lock:
            if not md:
                counts["fail"] += 1
                if args.verbose:
                    print(f"  miss {entry.get('name') or slug}", file=sys.stderr)
                return
            handle.write(json.dumps(
                summarize(slug, source, md, entry.get("name", ""), entry.get("tagline", "")),
                ensure_ascii=False) + "\n")
            handle.flush()
            counts["ok"] += 1
            done = counts["ok"] + counts["fail"]
            if done % 25 == 0 or args.verbose:
                print(f"  {done}/{len(todo)}  ok={counts['ok']} fail={counts['fail']}",
                      file=sys.stderr)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(work, todo))
    except KeyboardInterrupt:
        print("\ninterrupted — partial index kept, re-run to resume", file=sys.stderr)
    finally:
        handle.close()

    print(
        f"indexed {counts['ok']}, failed {counts['fail']}, robots-blocked {counts['blocked']}\n"
        f"index: {INDEX_PATH}",
        file=sys.stderr,
    )
    return 0 if counts["ok"] else 1


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def score(entry, terms, hexes):
    """How well one indexed style answers the query."""
    total = 0
    slug = entry["slug"].lower()
    name = entry.get("name", "").lower()
    tagline = entry.get("tagline", "").lower()
    title = entry.get("title", "").lower()
    headings = " ".join(entry.get("headings", [])).lower()
    fonts = " ".join(entry.get("fonts", [])).lower()
    names = " ".join(entry.get("color_names", [])).lower()
    text = entry.get("text", "").lower()

    if terms and " ".join(terms) in (slug, name):
        total += 1200

    for term in terms:
        if term == name:
            total += 900
        elif term in name:
            total += 400
        if term in tagline:
            total += 200
        if term == slug:
            total += 800
        elif term in slug:
            total += 220
        if term in title:
            total += 130
        if term in fonts:
            total += 110
        if term in headings:
            total += 60
        if term in names:
            total += 50
        if term in text:
            total += 25

    entry_hexes = {c.lower() for c in entry.get("colors", [])}
    for h in hexes:
        if h in entry_hexes:
            total += 400

    # A style matching more of the query beats one matching a single term loudly.
    matched = sum(1 for t in terms
                  if t in slug or t in name or t in tagline or t in title or t in text or t in fonts)
    total += 90 * matched
    return total


def cmd_find(args):
    index = load_index()
    if not index:
        print(
            f"No index at {INDEX_PATH}. Build one first:\n"
            f"  python3 {Path(__file__).name} crawl --limit 500",
            file=sys.stderr,
        )
        return 1

    query = " ".join(args.query).lower()
    hexes = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}", query)}
    terms = [t for t in re.split(r"[^a-z0-9#]+", query) if t and t not in STOPWORDS]

    ranked = sorted(
        ((score(e, terms, hexes), e) for e in index), key=lambda p: p[0], reverse=True
    )
    hits = [(s, e) for s, e in ranked if s > 0][: args.limit]
    if not hits:
        print(f"Nothing in the index matches {query!r} ({len(index)} styles indexed).",
              file=sys.stderr)
        return 1

    for s, e in hits:
        if args.verbose:
            print(f"{s:>6}  {e['slug']}\t{e['url']}\t{e['title']}\t"
                  f"{','.join(e.get('colors', [])[:5])}")
        else:
            print(f"{e['slug']}\t{e['url']}")
    return 0


def cmd_stats(args):
    index = load_index()
    if not index:
        print(f"No index at {INDEX_PATH}.", file=sys.stderr)
        return 1
    fonts = {}
    for e in index:
        for f in e.get("fonts", []):
            head = f.split(",")[0].strip().strip("\"'")
            fonts[head] = fonts.get(head, 0) + 1
    print(f"indexed styles : {len(index)}")
    print(f"index file     : {INDEX_PATH}  ({INDEX_PATH.stat().st_size // 1024} KB)")
    print(f"with colors    : {sum(1 for e in index if e.get('colors'))}")
    print(f"with fonts     : {sum(1 for e in index if e.get('fonts'))}")
    print("top fonts      : " + ", ".join(
        f"{k} ({v})" for k, v in sorted(fonts.items(), key=lambda p: -p[1])[:10]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("crawl", help="discover and index style pages")
    p.add_argument("--limit", type=int, help="stop after this many slugs")
    p.add_argument("--workers", type=int, default=4, help="concurrent fetches (default 4)")
    p.add_argument("--delay", type=float, default=0.5, help="seconds before each fetch (default 0.5)")
    p.add_argument("--refresh", action="store_true", help="discard the existing index first")
    p.add_argument("--render", action="store_true", help="use a real browser for each page (slow)")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("find", help="rank indexed styles against a description")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--verbose", action="store_true", help="show scores, titles and palettes")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("stats", help="what the index holds")
    p.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
