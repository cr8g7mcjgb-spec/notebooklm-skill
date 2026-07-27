# Refero Styles Skill

A Claude Code skill that finds the right DESIGN.md on
[styles.refero.design](https://styles.refero.design) by itself, extracts it, and maps it
onto the project's existing token layer — instead of the user browsing the site and
pasting markdown into the chat.

Refero Styles is a public registry of design systems extracted from real product sites.
Each entry ships a DESIGN.md: theme, color palette, typography, spacing, radius,
elevation, layout, components, imagery, do/don't rules, and an agent prompt guide.

## Install

```bash
cp -r refero-styles ~/.claude/skills/refero-styles     # user-wide
# or, per project:
cp -r refero-styles <project>/.claude/skills/refero-styles
```

The HTTP path is stdlib-only — no install step. `--render` (browser fallback for
JS-hydrated pages and WAF-blocked requests) needs `patchright` or `playwright`:

```bash
pip install patchright && patchright install chrome
```

The NotebookLM skill in this repo already ships patchright, so `.venv/bin/python` works
as the interpreter for `--render` without installing anything else.

## Usage

Normally you just ask, and the skill triggers:

> "make the landing page feel like Linear"
> "미니멀한 에디토리얼 스타일로 바꿔줘"
> "https://styles.refero.design/style/… 이 스타일 적용해줘"

Directly:

```bash
python3 scripts/refero_fetch.py search linear
python3 scripts/refero_fetch.py fetch linear --save design/refero-linear.DESIGN.md
python3 scripts/refero_fetch.py probe          # diagnostics
```

Fetched documents cache to `~/.claude/skills/refero-styles/cache/`
(`REFERO_CACHE_DIR` overrides).

## How it finds things

Refero publishes no documented API, so nothing is hardcoded to one URL shape:

1. **search** — tries `/?q=`, `/search?q=`, `/api/search?q=`, then falls back to
   `sitemap.xml` / `llms.txt` and filters slugs by the query terms.
2. **fetch** — tries `<page>.md`, `<page>/design.md`, `<page>/DESIGN.md`, then the HTML
   page, extracting markdown from `__NEXT_DATA__`, app-router flight payloads, long
   escaped inline-script strings, `<pre>`/`<code>`, and clipboard data attributes —
   scoring candidates so page chrome never wins.
3. On `403`/network refusal, replays the request through a real browser.

When the site redesigns, `probe` tells you which layer broke.

## Alternatives

- **MCP server** — a community `refero-styles-mcp-server` exists; it puts the same
  lookup behind MCP tools. Heavier setup (a server per session), but no scraping code to
  maintain if the maintainer keeps it current.
- **WebSearch + WebFetch** — zero setup, works today: search
  `site:styles.refero.design/style/ <brand>` and fetch the page. You get a model summary
  of the document rather than the document, which is why this skill exists.

## Caveats

- Extraction is heuristic. Skim the saved file before applying it.
- A DESIGN.md describes a marketing/product site, not an app shell — dense product UI
  needs adaptation.
- Accessibility beats fidelity: contrast, focus states, and reduced-motion handling are
  preserved even when the reference would break them.
- This is design *direction*. Do not use it to clone a brand's identity.
