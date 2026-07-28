#!/usr/bin/env python3
"""
Refero Styles fetcher.

Finds and extracts DESIGN.md documents from https://styles.refero.design
so an agent can pull a design direction without the user copy-pasting.

The site is a JS-rendered app and its internal endpoints are not a published
API, so nothing here is hardcoded to a single URL shape: every command probes
a list of candidates and reports which one actually worked. When plain HTTP is
blocked or the markdown only exists after hydration, `--render` replays the
same request through a real browser (patchright/playwright).

Commands:
    search <query>         Find candidate style pages
    fetch <url-or-slug>    Extract the DESIGN.md for one style
    probe                  Report which site endpoints are reachable

Stdlib only for the HTTP path. `--render` needs patchright or playwright.
"""

import argparse
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://styles.refero.design"

# A real browser UA: the site sits behind a WAF that rejects default UAs.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

CACHE_DIR = Path(
    os.environ.get("REFERO_CACHE_DIR", Path.home() / ".claude" / "skills" / "refero-styles" / "cache")
)

SLUG_RE = re.compile(r"/style/([a-z0-9][a-z0-9._-]{0,80})", re.IGNORECASE)
TIMEOUT = 30


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get(url, timeout=TIMEOUT):
    """GET a URL. Returns (status, text). Never raises on HTTP errors."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,text/markdown,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # DNS, TLS, proxy denial, timeout
        return 0, f"{type(e).__name__}: {e}"


def render_get(url, timeout=TIMEOUT, show_browser=False):
    """GET a URL through a real browser, returning the hydrated HTML."""
    launcher = None
    for module in ("patchright.sync_api", "playwright.sync_api"):
        try:
            launcher = __import__(module, fromlist=["sync_playwright"]).sync_playwright
            break
        except ImportError:
            continue
    if launcher is None:
        return 0, (
            "patchright/playwright not importable. Install one of them, or run this "
            "script with the NotebookLM skill venv:\n"
            "  <repo>/.venv/bin/python refero-styles/scripts/refero_fetch.py ..."
        )

    with launcher() as p:
        browser = p.chromium.launch(headless=not show_browser)
        try:
            page = browser.new_context(user_agent=USER_AGENT).new_page()
            resp = page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            html = page.content()
            return (resp.status if resp else 200), html
        finally:
            browser.close()


def get(url, render=False, show_browser=False):
    """HTTP first, browser only if HTTP came back empty or refused."""
    if not render:
        status, body = http_get(url)
        if status == 200 and body.strip():
            return status, body, "http"
        if status in (401, 403, 429) or status == 0:
            # Blocked or unreachable -> a browser is the only remaining option.
            r_status, r_body = render_get(url, show_browser=show_browser)
            if r_status == 200 and r_body.strip():
                return r_status, r_body, "render"
            return status, body, "http"
        return status, body, "http"
    status, body = render_get(url, show_browser=show_browser)
    return status, body, "render"


# --------------------------------------------------------------------------
# Markdown extraction
# --------------------------------------------------------------------------

def _unescape_js_string(s):
    try:
        return json.loads('"' + s.replace('"', '\\"') + '"')
    except Exception:
        return s.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")


def _markdown_score(text):
    """How much does this string look like a DESIGN.md rather than page chrome?"""
    if not text or len(text) < 100:
        return 0
    score = 0
    score += 30 * len(re.findall(r"^#{1,3} \S", text, re.MULTILINE))
    score += 10 * len(re.findall(r"^\s*[-*] \S", text, re.MULTILINE))
    score += 8 * len(re.findall(r"#[0-9a-fA-F]{3,8}\b", text))
    for kw in ("color", "typography", "spacing", "radius", "elevation",
               "component", "palette", "font", "token", "theme"):
        if kw in text.lower():
            score += 25
    score += min(len(text) // 100, 200)
    if "<div" in text or "<script" in text:
        score -= 500
    return score


def _css_score(text):
    """A published `CSS Variables` block — already implementation-ready."""
    if not text or len(text) < 80:
        return 0
    decls = len(re.findall(r"--[a-z0-9-]+\s*:\s*[^;\n]+;", text, re.IGNORECASE))
    if decls < 4:
        return 0
    score = 40 * decls
    if re.search(r":root\s*\{", text):
        score += 300
    if "<div" in text or "<script" in text:
        score -= 1000
    return score


def _tailwind_score(text):
    """A published `Tailwind v4` block: @theme / @import "tailwindcss"."""
    if not text or len(text) < 80:
        return 0
    score = 0
    if re.search(r"@theme\b", text):
        score += 600
    if re.search(r'@import\s+["\']tailwindcss', text):
        score += 400
    score += 30 * len(re.findall(r"--(?:color|font|text|spacing|radius|shadow)-[a-z0-9-]+\s*:",
                                 text, re.IGNORECASE))
    if score and ("<div" in text or "<script" in text):
        score -= 1000
    return score


def _tokens_json_score(text):
    """A published `Design Tokens` JSON blob."""
    stripped = text.strip()
    if not stripped.startswith("{") or len(stripped) < 80:
        return 0
    try:
        data = json.loads(stripped)
    except Exception:
        return 0
    flat = json.dumps(data).lower()
    if not any(k in flat for k in ("color", "font", "spacing", "radius", "shadow")):
        return 0
    return 500 + min(len(stripped) // 10, 500)


# The page publishes these ready-made; prefer them over re-parsing the markdown.
ASSET_SCORERS = {
    "design-md": _markdown_score,
    "css": _css_score,
    "tailwind": _tailwind_score,
    "tokens": _tokens_json_score,
}

ASSET_EXT = {"design-md": "DESIGN.md", "css": "vars.css", "tailwind": "theme.css", "tokens": "tokens.json"}


def _candidates_from_html(html):
    """Every plausible embedded blob — markdown, CSS, or JSON."""
    out = []

    # 1. Next.js pages router payload.
    for m in re.finditer(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    ):
        try:
            data = json.loads(m.group(1))
            out.extend(_walk_json(data))
            # Design Tokens may be a nested object rather than a string blob.
            out.extend(_json_subtrees(data))
        except Exception:
            pass

    # 2. Next.js app-router streamed flight data / other inline JSON payloads.
    for m in re.finditer(r'self\.__next_f\.push\(\[.*?"(.*?)"\]\)', html, re.DOTALL):
        out.append(_unescape_js_string(m.group(1)))

    # 3. Long escaped strings anywhere in inline scripts (covers other frameworks).
    for m in re.finditer(r'"((?:[^"\\]|\\.){400,})"', html):
        out.append(_unescape_js_string(m.group(1)))

    # 4. Rendered <pre>/<code> blocks, and elements that carry the raw text.
    for m in re.finditer(r"<(pre|code|textarea)[^>]*>(.*?)</\1>", html, re.DOTALL | re.IGNORECASE):
        out.append(_strip_tags(m.group(2)))
    for m in re.finditer(r'data-(?:clipboard-text|markdown|content)="([^"]{200,})"', html):
        out.append(_unescape_js_string(m.group(1)))

    return out


def _walk_json(node, acc=None):
    acc = [] if acc is None else acc
    if isinstance(node, str):
        # 80 is the shortest asset worth scoring (a small CSS variables block);
        # scoring, not this threshold, is what rejects noise.
        if len(node) > 80:
            acc.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _walk_json(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk_json(v, acc)
    return acc


def _json_subtrees(node, depth=0, acc=None):
    """Serialized dict nodes that look like a design-token tree."""
    acc = [] if acc is None else acc
    if depth > 6 or len(acc) > 40:
        return acc
    if isinstance(node, dict):
        keys = " ".join(str(k).lower() for k in node)
        if any(k in keys for k in ("color", "font", "spacing", "radius", "shadow", "typography")):
            acc.append(json.dumps(node, indent=2, ensure_ascii=False))
        for v in node.values():
            _json_subtrees(v, depth + 1, acc)
    elif isinstance(node, list):
        for v in node[:20]:
            _json_subtrees(v, depth + 1, acc)
    return acc


def _strip_tags(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return text


def extract_asset(body, asset="design-md", content_is_raw=False):
    """Pick the best candidate of one asset type out of a response body.

    A Refero style page publishes DESIGN.md, CSS Variables, Tailwind v4 and
    Design Tokens side by side; each is scored on its own terms so the CSS block
    is never mistaken for the markdown or vice versa.
    """
    scorer = ASSET_SCORERS[asset]
    # A body that is not a web page is the asset itself — accept it on any positive
    # signal rather than the threshold tuned for picking blobs out of page chrome.
    if content_is_raw and scorer(body) > 0:
        return body.strip()
    if "<html" not in body[:2000].lower() and scorer(body) > 300:
        return body.strip()
    best, best_score = None, 0
    for cand in _candidates_from_html(body):
        score = scorer(cand)
        if score > best_score:
            best, best_score = cand, score
    return best.strip() if best and best_score >= 300 else None


def extract_markdown(body, content_is_markdown=False):
    """Back-compat wrapper: the DESIGN.md specifically."""
    return extract_asset(body, "design-md", content_is_raw=content_is_markdown)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def page_url(target):
    if target.startswith("http://") or target.startswith("https://"):
        return target.rstrip("/")
    return f"{BASE}/style/{target.strip().strip('/')}"


def slug_to_urls(target, asset="design-md"):
    """Candidate URLs for one asset, cheapest/most-likely first.

    Direct file URLs are tried before the page itself: if the site serves the
    asset as a file, that copy is authoritative and needs no extraction.
    """
    page = page_url(target)
    direct = {
        "design-md": [f"{page}.md", f"{page}/design.md", f"{page}/DESIGN.md"],
        "css": [f"{page}/variables.css", f"{page}/vars.css", f"{page}.css"],
        "tailwind": [f"{page}/tailwind.css", f"{page}/theme.css"],
        "tokens": [f"{page}/tokens.json", f"{page}.json"],
    }[asset]
    return direct + [page]


def comment_for(asset, url):
    if asset == "design-md":
        return f"<!-- source: {url} -->\n\n"
    if asset == "tokens":
        return ""  # JSON takes no comment
    return f"/* source: {url} */\n"


def fetch_asset(target, asset, render=False, show_browser=False):
    """(content, url, via) for one asset, or (None, None, attempts-log)."""
    tried = []
    for url in slug_to_urls(target, asset):
        status, body, via = get(url, render=render, show_browser=show_browser)
        tried.append(f"  {status:>3} {via:<6} {url}")
        if status != 200 or not body.strip():
            continue
        # Raw-vs-page is decided by the body, not the URL: the page URL itself can
        # serve the bare document under content negotiation.
        content = extract_asset(body, asset, content_is_raw=_looks_raw(body))
        if content:
            return content, url, via
    return None, None, "\n".join(tried)


def _looks_raw(body):
    return "<html" not in body[:2000].lower()


def cmd_fetch(args):
    assets = list(ASSET_SCORERS) if args.asset == "all" else [args.asset]
    slug = re.sub(r"[^a-z0-9._-]+", "-", args.target.rstrip("/").split("/")[-1].lower()) or "style"
    ok = False

    for asset in assets:
        content, url, log = fetch_asset(
            args.target, asset, render=args.render, show_browser=args.show_browser
        )
        if not content:
            if args.asset != "all":
                print(f"Could not extract '{asset}'. Attempts:", file=sys.stderr)
                print(log, file=sys.stderr)
                if not args.render:
                    print("\nRetry with --render to let a real browser hydrate the page.",
                          file=sys.stderr)
                return 1
            print(f"[{asset}] not found", file=sys.stderr)
            continue

        ok = True
        if args.save and args.asset != "all":
            out = Path(args.save)
        else:
            out = CACHE_DIR / f"{slug}.{ASSET_EXT[asset]}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(comment_for(asset, url) + content + "\n", encoding="utf-8")

        print(f"[{asset}] source: {url}")
        print(f"[{asset}] saved:  {out}  ({len(content)} chars)")
        if args.asset != "all":
            print("---")
            print(content if args.full
                  else content[:4000] + ("\n… (truncated, use --full)" if len(content) > 4000 else ""))

    return 0 if ok else 1


def cmd_search(args):
    query = " ".join(args.query).strip()
    encoded = urllib.parse.quote_plus(query)
    found, notes = [], []

    for url in (
        f"{BASE}/?q={encoded}",
        f"{BASE}/search?q={encoded}",
        f"{BASE}/api/search?q={encoded}",
    ):
        status, body, via = get(url, render=args.render, show_browser=args.show_browser)
        notes.append(f"  {status:>3} {via:<6} {url}")
        if status == 200 and body.strip():
            for slug in SLUG_RE.findall(body):
                if slug.lower() not in [f.lower() for f in found]:
                    found.append(slug)
        if len(found) >= args.limit:
            break

    # The sitemap is the reliable fallback: it lists every style page.
    if len(found) < args.limit:
        for url in (f"{BASE}/sitemap.xml", f"{BASE}/sitemap-0.xml", f"{BASE}/llms.txt"):
            status, body = http_get(url)
            notes.append(f"  {status:>3} http   {url}")
            if status != 200 or not body.strip():
                continue
            terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]
            for slug in SLUG_RE.findall(body):
                low = slug.lower()
                if terms and not any(t in low for t in terms):
                    continue
                if low not in [f.lower() for f in found]:
                    found.append(slug)
            if found:
                break

    if not found:
        print("No style pages found. Endpoints tried:", file=sys.stderr)
        print("\n".join(notes), file=sys.stderr)
        print(
            "\nFall back to a web search for:  site:styles.refero.design/style/ " + query,
            file=sys.stderr,
        )
        return 1

    for slug in found[: args.limit]:
        print(f"{slug}\t{BASE}/style/{slug}")
    return 0


def cmd_probe(args):
    """Report reachability so failures are diagnosable rather than mysterious."""
    for path in ("/", "/sitemap.xml", "/llms.txt", "/design-md/design-md-template", "/?q=minimal"):
        status, body = http_get(BASE + path)
        detail = body[:80].replace("\n", " ") if status == 0 else f"{len(body)} chars"
        print(f"{status:>3}  {BASE + path:<55} {detail}")
    if args.render:
        status, body = render_get(BASE + "/")
        print(f"{status:>3}  rendered {BASE}/  {len(body)} chars")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--render", action="store_true", help="force a real browser (JS-rendered pages)")
    common.add_argument("--show-browser", action="store_true", help="show the browser window while rendering")

    p = sub.add_parser("search", parents=[common], help="find candidate style pages")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("fetch", parents=[common], help="extract a style's published assets")
    p.add_argument("target", help="slug (linear) or full URL")
    p.add_argument(
        "--asset",
        choices=sorted(ASSET_SCORERS) + ["all"],
        default="design-md",
        help="which published output to take: design-md, css, tailwind, tokens, or all",
    )
    p.add_argument("--save", help="write to this path instead of the cache (single asset only)")
    p.add_argument("--full", action="store_true", help="print the whole document")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("probe", parents=[common], help="report which endpoints are reachable")
    p.set_defaults(func=cmd_probe)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
