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
    if not text or len(text) < 200:
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


def _candidates_from_html(html):
    """Every plausible markdown blob embedded in the page."""
    out = []

    # 1. Next.js pages router payload.
    for m in re.finditer(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    ):
        try:
            out.extend(_walk_json(json.loads(m.group(1))))
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
        if len(node) > 200:
            acc.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _walk_json(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk_json(v, acc)
    return acc


def _strip_tags(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return text


def extract_markdown(body, content_is_markdown=False):
    """Pick the best DESIGN.md candidate out of a response body."""
    if content_is_markdown or ("<html" not in body[:2000].lower() and _markdown_score(body) > 300):
        return body.strip()
    best, best_score = None, 0
    for cand in _candidates_from_html(body):
        score = _markdown_score(cand)
        if score > best_score:
            best, best_score = cand, score
    return best.strip() if best and best_score >= 300 else None


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def slug_to_urls(target):
    """Candidate URLs for one style, cheapest/most-likely first."""
    if target.startswith("http://") or target.startswith("https://"):
        page = target.rstrip("/")
    else:
        page = f"{BASE}/style/{target.strip().strip('/')}"
    return [f"{page}.md", f"{page}/design.md", f"{page}/DESIGN.md", page]


def cmd_fetch(args):
    tried = []
    for url in slug_to_urls(args.target):
        status, body, via = get(url, render=args.render, show_browser=args.show_browser)
        tried.append(f"  {status:>3} {via:<6} {url}")
        if status != 200 or not body.strip():
            continue
        md = extract_markdown(body, content_is_markdown=url.endswith(".md"))
        if not md:
            continue

        slug = re.sub(r"[^a-z0-9._-]+", "-", args.target.rstrip("/").split("/")[-1].lower())
        if args.save:
            out = Path(args.save)
        else:
            out = CACHE_DIR / f"{slug or 'style'}.DESIGN.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"<!-- source: {url} -->\n\n{md}\n", encoding="utf-8")

        print(f"source: {url}  (via {via})")
        print(f"saved:  {out}")
        print(f"length: {len(md)} chars")
        print("---")
        print(md if args.full else md[:4000] + ("\n… (truncated, use --full)" if len(md) > 4000 else ""))
        return 0

    print("Could not extract a DESIGN.md. Attempts:", file=sys.stderr)
    print("\n".join(tried), file=sys.stderr)
    if not args.render:
        print("\nRetry with --render to let a real browser hydrate the page.", file=sys.stderr)
    return 1


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

    p = sub.add_parser("fetch", parents=[common], help="extract one style's DESIGN.md")
    p.add_argument("target", help="slug (linear) or full URL")
    p.add_argument("--save", help="write to this path instead of the cache")
    p.add_argument("--full", action="store_true", help="print the whole document")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("probe", parents=[common], help="report which endpoints are reachable")
    p.set_defaults(func=cmd_probe)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
