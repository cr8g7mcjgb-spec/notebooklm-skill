# Refero Styles Skill

Every time you ask for something with a visual result — a page, a component, an artifact,
a poster, a deck, a document — Claude looks up the matching DESIGN.md on
[styles.refero.design](https://styles.refero.design) and builds the deliverable with
**that document's exact published values**.

Refero Styles is a public registry of design systems extracted from real product sites.
Each entry ships a DESIGN.md: theme, color palette, typography, spacing, radius,
elevation, layout, components, imagery, do/don't rules, and an agent prompt guide.

The contract is fidelity. If the document says `#5E6AD2`, the output contains `#5E6AD2` —
not a nearby default, not "inspired by". Step 6 of the skill greps the built file for the
source values and reports any that went missing.

Because the direction changes from request to request, the lookup runs **fresh every
time**. A style used in the previous answer is never the default for the next one, and a
failed lookup stops the build rather than falling back to an invented palette.

## Install

```bash
cp -r refero-styles ~/.claude/skills/refero-styles     # user-wide
cp -r refero-styles <project>/.claude/skills/          # or per project
```

The HTTP path is stdlib-only. `--render` (browser fallback for JS-hydrated pages and
WAF-blocked requests) needs `patchright` or `playwright`:

```bash
pip install patchright && patchright install chrome
```

The NotebookLM skill in this repo already ships patchright, so `.venv/bin/python` is a
working interpreter for `--render` with nothing else to install.

To make the lookup unmissable rather than merely likely, add one line to your `CLAUDE.md`:

> When building anything visual, use the refero-styles skill to fetch a DESIGN.md and
> apply its exact values.

## Usage

Normally you just ask:

> "랜딩페이지 Linear 느낌으로 만들어줘"
> "미니멀한 에디토리얼 스타일로 발표자료 만들어줘"
> "https://styles.refero.design/style/… 이 스타일로"

Directly:

```bash
python3 scripts/refero_index.py crawl --limit 500      # index the open DESIGN.md corpus
python3 scripts/refero_index.py find "warm editorial serif" --verbose
python3 scripts/refero_fetch.py fetch linear --asset all
python3 scripts/refero_fetch.py fetch linear --asset design-md --save design/refero-linear.DESIGN.md
python3 scripts/refero_tokens.py design/refero-linear.DESIGN.md --format css
python3 scripts/refero_fetch.py probe            # diagnostics
```

A style page publishes the same design several ways — `DESIGN.md`, `CSS Variables`,
`Tailwind v4`, `Design Tokens`. **Take the published block when one fits the stack**
(`--asset css` / `tailwind` / `tokens`): it is already implementation-ready, so there is no
parsing step in which a value can drift. `refero_tokens.py` is the fallback for custom
component systems and non-web deliverables — and the DESIGN.md is still worth pulling
either way, since layout notes, component sizing and do/don't rules live only there.

`refero_tokens.py` emits `css` (`:root` variables), `tailwind` (v4 `@theme`), `json`,
`python` (a dict for python-pptx / python-docx / matplotlib), or `summary` (what was
recognised). Values are copied across untouched — no rounding, no substitution. Whatever
the parser does not recognise is applied by reading the DESIGN.md directly, never guessed.

Documents cache to `~/.claude/skills/refero-styles/cache/` (`REFERO_CACHE_DIR` overrides),
keyed by slug — so the cache speeds up a repeat of the *same* style without ever
short-circuiting the intent lookup for a new one.

## MCP server

For clients without skill support (Claude Desktop, Cursor, Zed):

```bash
pip install "mcp[cli]"
claude mcp add refero-styles -- python /abs/path/refero-styles/mcp/refero_mcp_server.py
```

Tools: `refero_find` (index-ranked search), `refero_search` (live), `refero_design_md`
(verbatim document), `refero_asset` (published
css / tailwind / tokens block), `refero_tokens`
(css/tailwind/json/python), `refero_probe` (reachability).

## Crawling the open corpus

The site publishes thousands of open DESIGN.md documents. Which one fits "warm editorial
serif" is decided by what is inside them, not by slug spelling — so `refero_index.py`
crawls them into a local index and searches that.

```bash
python3 scripts/refero_index.py crawl               # everything
python3 scripts/refero_index.py crawl --limit 500   # or a slice
python3 scripts/refero_index.py find "luxury gold premium elegant" --verbose
python3 scripts/refero_index.py stats
```

The crawler discovers slugs from `sitemap.xml` (following sitemap indexes), `llms.txt`, and
paginated listing pages; **reads and honours `robots.txt`**; identifies itself with a real
User-Agent; runs 4 workers with a 0.5s delay per fetch (`--workers`, `--delay`); and appends
JSONL as it goes, so an interrupted crawl resumes on re-run instead of starting over.
`--refresh` rebuilds from scratch.

`find` ranks on slug, title, headings, font names, color names, hex values and body text,
so mood words, a font name, or a hex code all work as queries — including mixed-language
ones.

**The index selects; it never supplies.** It is a snapshot used for ranking. The chosen
style is always re-fetched live before anything is built, so shipped values are current.

## How it finds things

Refero publishes no documented API, so nothing is hardcoded to one URL shape:

1. **search** — `/?q=`, `/search?q=`, `/api/search?q=`, then `sitemap.xml` / `llms.txt`
   filtered by the query terms, then WebSearch as a last resort. `refero_index.py find` is
   better whenever an index exists, since it matches content rather than slugs.
2. **fetch** — direct file URLs for the requested asset first (`<page>.md`,
   `<page>/variables.css`, `<page>/tokens.json`, …), then the HTML page, extracting from
   `__NEXT_DATA__`, app-router flight payloads, nested token objects, long escaped
   inline-script strings, `<pre>`/`<code>`, and clipboard attributes. Each asset type is
   scored on its own terms, so the CSS block is never mistaken for the markdown and page
   chrome never wins.
3. On `403` or refusal, replays through a real browser.

`probe` tells you which layer broke when the site changes.

## Network blocks

If `probe` returns status `0` on every row, the **host's egress** is blocking the domain —
common in sandboxed or remote environments, where the proxy denies CONNECT. An MCP server
on the same host hits the same proxy, so it is not a workaround. Run from a machine with
normal network access, or open the environment's network policy. A site-side WAF block is
a different problem, and `--render` does solve that one.

## Caveats

- Extraction is heuristic. Skim the saved DESIGN.md before it ships.
- A DESIGN.md describes a marketing/product site, not an app shell — dense product UI
  needs decisions the reference does not make.
- Accessibility beats fidelity in exactly one place: a contrast pair that fails WCAG AA is
  adjusted, minimally, and the change is reported.
- This is design *direction*. Do not reproduce a brand's logo, wordmark, or identity.
