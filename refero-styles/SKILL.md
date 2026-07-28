---
name: refero-styles
description: Fetch a DESIGN.md from Refero Styles (styles.refero.design) and build the deliverable with its exact published values. Use whenever creating or restyling anything with a visual result — a web page, landing page, UI component, dashboard, artifact, poster, chart, slide deck (pptx), document (docx), PDF, or README — and especially when the request names a style direction, whether a brand ("Linear 느낌으로", "like Stripe"), a mood ("minimal", "editorial", "high contrast", "미니멀하게", "감성적으로", "고급스럽게"), or a styles.refero.design URL. Also use for "디자인 md 가져와", "레퍼런스대로 만들어", "저 사이트에서 찾아서". Look the style up fresh on every such request — the wanted direction changes each time and must never be carried over from a previous answer.
---

# Refero Styles → deliverable, verbatim

Refero Styles publishes a DESIGN.md per site: theme, color palette, typography, spacing,
radius, elevation, layout, components, imagery, do/don't rules, agent prompt guide.

This skill's contract: **the values that ship in the deliverable are the values published
in the DESIGN.md.** Not "inspired by". Not rounded to a nearby scale. Not substituted for
a default palette. If the document says `#5E6AD2`, the output contains `#5E6AD2`.

## Trigger

Run this whenever the request produces something with a visual result — HTML, a React or
Vue component, a Tailwind page, an artifact, a poster, a chart, a `.pptx`, a `.docx`, a
PDF — **and** the request carries any style direction at all.

Skip it only when the user explicitly says not to use a reference, or when the project's
own design system is the subject of the edit.

## The loop — run it every time

The wanted direction changes from request to request. Treat each request as a fresh
lookup. Three rules, in priority order:

1. **Never reuse the previous request's style.** Re-derive the intent from *this*
   message. A style used ten minutes ago is not the default for the next deliverable.
2. **Never invent a palette.** If the lookup fails, say so and stop — do not fall back to
   a made-up "minimal" palette and present it as the reference.
3. **Never paraphrase values.** Copy them.

### Step 1 — Derive the style intent from this request

Pull out whichever the user gave:

| They said | Search with |
|---|---|
| A brand — "Linear 느낌", "like Stripe" | the brand name |
| A mood — "미니멀", "editorial", "고급스럽게", "high contrast" | the mood words, in English |
| A refero URL | skip to Step 2 with that URL |
| Nothing explicit, but a visual deliverable | infer 2–3 mood words from the content and purpose |

### Step 2 — Find the style page

The site publishes thousands of open DESIGN.md documents. Which one fits "warm editorial
serif" is decided by what is *inside* those documents, not by slug spelling — so search the
crawled index first.

```bash
# content search over the local index — handles moods, font names, hex colors
python3 refero-styles/scripts/refero_index.py find "warm editorial serif magazine"
python3 refero-styles/scripts/refero_index.py find "Playfair Display"
python3 refero-styles/scripts/refero_index.py find "#C8A96A"

# no index yet? build one (resumable, robots-aware), then search it
python3 refero-styles/scripts/refero_index.py crawl --limit 500
python3 refero-styles/scripts/refero_index.py stats
```

If no index exists and building one is not worth it for this request, fall back to the live
lookup — which matches on slug, so it only works when the user named a brand:

```bash
python3 refero-styles/scripts/refero_fetch.py search linear
```

Last resort, WebSearch: `site:styles.refero.design/style/ <query>`.

Output is `slug<TAB>url`. Take the top match and keep going — state which slug you used so
the user can redirect. Only ask when the top candidates are genuinely different directions
and picking wrong would waste the whole build.

**The index selects; it never supplies.** It holds a snapshot for ranking. The chosen style
is always fetched live in Step 3, so the values that ship are the current ones.

### Step 3 — Pull what the page publishes, verbatim

A style page publishes the same design several ways: `Preview`, `DESIGN.md`,
`Tailwind v4`, `CSS Variables`, `Design Tokens`. **Take the most
implementation-ready one for the stack** — a published block needs no parsing, so there is
no step in which a value can drift.

```bash
python3 refero-styles/scripts/refero_fetch.py fetch <slug> --asset all
```

| Project | Asset | Lands as |
|---|---|---|
| Tailwind v4 | `--asset tailwind` | the `@theme` block, used as-is |
| Plain CSS / CSS modules | `--asset css` | the `:root` variables, used as-is |
| CSS-in-JS / theme object | `--asset tokens` | the JSON, imported directly |
| Custom component system, or a non-web deliverable (pptx, docx, poster) | `--asset design-md` | via Step 4 |

Always pull `design-md` as well, even when a published block covers the tokens: the layout
notes, component sizing, imagery direction and do/don't rules exist only there.

```bash
python3 refero-styles/scripts/refero_fetch.py fetch <slug> \
  --asset design-md --save design/refero-<slug>.DESIGN.md --full
```

Read the saved file. It — not your memory of it — is the source of truth.

See `references/public-extraction.md` when a page comes back partial.

If plain HTTP is refused the script retries through a real browser; force with `--render`,
and use this repo's venv interpreter when patchright is needed:

```bash
.venv/bin/python refero-styles/scripts/refero_fetch.py fetch <slug> --render
```

**Keep it out of context.** `fetch` writes to disk and prints only the path — that is
deliberate. A DESIGN.md is several thousand characters of prose that mostly restates its own
tokens. Do not `--full` it. Work from the brief in Step 4, and open the saved file only when
the build actually needs the layout, component or do/don't detail.

### Step 4 — Turn it into tokens (only if no published block fits)

Skip this step when Step 3 already produced a usable `css`, `tailwind`, or `tokens` block —
that output is better than anything derived here.

```bash
# start here — the whole design in ~15 lines, ~80% cheaper than the document
python3 refero-styles/scripts/refero_tokens.py design/refero-<slug>.DESIGN.md --format brief

python3 refero-styles/scripts/refero_tokens.py design/refero-<slug>.DESIGN.md --format css
#   --format tailwind   Tailwind v4 @theme block
#   --format json       structured tokens
#   --format python     dict for python-pptx / python-docx / matplotlib
#   --format summary    what was recognised
```

The emitter copies values across untouched. It is an accelerator, not an authority —
anything it did not recognise (`summary` shows the counts) you apply by reading the
DESIGN.md yourself. Never let a parser gap become a guessed value.

### Step 5 — Build with those tokens

| Deliverable | How the tokens land |
|---|---|
| HTML page / artifact | `--format css` into a `:root` block; every rule references `var(--…)` |
| React / Tailwind v4 | `--format tailwind` into the CSS entry; use the generated utilities |
| React / CSS-in-JS | `--format json`, imported as the theme object |
| `.pptx` (python-pptx) | `--format python`; hexes → `RGBColor.from_string`, sizes → `Pt` |
| `.docx` (python-docx) | `--format python`; styles set from the same dict |
| Poster / chart / canvas | `--format json`; palette drives the fills and the type scale the labels |
| Existing codebase | Put the **exact** values into the project's existing token names — the naming is local, the values are the reference's |

Also carry across what is not a token: the DESIGN.md's layout notes, component sizing,
imagery direction, and do/don't rules are part of the reference.

### Step 6 — Verify the values actually shipped

This is the step that makes "그대로" true rather than claimed. Before reporting done:

```bash
# every source color must appear in the output
grep -oiE '#[0-9a-f]{6}' design/refero-<slug>.DESIGN.md | sort -u > /tmp/want.txt
grep -oiE '#[0-9a-f]{6}' <built-file> | sort -u > /tmp/got.txt
comm -23 /tmp/want.txt /tmp/got.txt      # in the reference, missing from the build
```

For binary outputs (`.pptx`, `.docx`, `.pdf`) run the check against the generation script.
Some misses are legitimate — a color the layout had no use for. An unexplained miss is a
bug: fix it rather than reporting success.

### Step 7 — Report

Name the slug, the source URL, and the saved DESIGN.md path. Then state anything you did
**not** apply verbatim and why — the only two acceptable reasons being a contrast pair
that fails accessibility, or a value the document simply does not specify.

Work through `references/application-checklist.md` before reporting done.

## Guardrails

**Tokens transfer everywhere; layout transfers only where the context matches.** A
DESIGN.md is extracted from a marketing or product site. Its palette, type and spacing
scale apply to anything. Its hero rhythm and decorative patterns do not belong on
dashboards, tools, tables or forms — operational UI stays dense, scannable and
task-focused, and keeps the product's own information architecture.

**Accessibility is the one place fidelity yields.** Copy values exactly, except where a
foreground/background pair fails WCAG AA at its used size. Adjust the minimum necessary,
leave everything else untouched, and report the change. Focus states, hit targets, and
reduced-motion handling survive regardless.

**Fonts that cannot be loaded.** If the reference's family is unavailable to the app,
choose the closest local or web-safe fallback and keep the reference's weights, sizes,
letter-spacing and hierarchy. Name the substitution. Never silently drop to a system
default.

**Ownership.** Style principles and token values cross over. Logos, wordmarks, brand
illustrations, and copy do not — unless the user owns or supplied them. Follow the imagery
*direction*; do not lift imagery assets.

**Claims.** These are public pages. Do not describe the result as using Refero MCP or any
private Refero access unless an MCP server is actually configured in the session.

## Command reference

| Command | Purpose |
|---|---|
| `refero_index.py crawl [--limit N] [--refresh]` | Discover and index every public style |
| `refero_index.py find <query> [--verbose]` | Rank the index by content — moods, fonts, hexes |
| `refero_index.py stats` | What the index holds |
| `refero_fetch.py search <query> [--limit N]` | Live slug lookup when no index exists |
| `refero_fetch.py fetch <slug\|url> --asset <a>` | Published output: `design-md`, `css`, `tailwind`, `tokens`, `all` |
| `refero_fetch.py probe` | Endpoint reachability (diagnostics) |
| `refero_tokens.py <file> --format <fmt>` | css / tailwind / json / python / summary |
| `--render` / `--show-browser` | Route through a real browser |

## Cost discipline

The expensive mistake is pulling documents into context that the build never reads.

| Step | Cheap way | Costly way |
|---|---|---|
| Choose a style | `refero_index.py find` → one `slug<TAB>url` line | Fetching several candidates to compare |
| Get the design | `fetch` (writes to disk, prints a path) | `fetch --full` |
| Build from it | `--format brief`, plus the emitted `css` file | Reading the whole DESIGN.md |
| Need a specific rule | Read just that section of the saved file | Re-reading the document |

Crawling is a one-time cost paid outside the conversation; `find` afterwards is a single
line of output. There are no API keys, no paid services, and no third-party endpoints —
plain HTTPS to a public site, stdlib only, with a browser used only when a fetch is refused.

## References

- `references/public-extraction.md` — asset priority, and what to do when a page is partial
- `references/application-checklist.md` — pre-ship verification of fidelity, fit and access

Fetched documents cache to `~/.claude/skills/refero-styles/cache/` (`REFERO_CACHE_DIR`
overrides). The cache is keyed by slug, so it never short-circuits Step 1 — a new request
still derives its own intent and may resolve to a different slug.

## MCP alternative

`mcp/refero_mcp_server.py` exposes `refero_search`, `refero_design_md`, `refero_tokens`,
and `refero_probe` for clients without skill support. See the README.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `probe` shows `0` on every row | The host's egress blocks the domain (sandboxed/remote envs deny CONNECT) | Run where the network is open. An MCP server on the same host is blocked identically — it is not a way around this |
| `403` everywhere | WAF rejecting the request | `--render` |
| `200` but no DESIGN.md extracted | Markdown only exists post-hydration, or the markup changed | `--render`; then update `_candidates_from_html` |
| `search` empty | The `?q=` endpoint changed | Auto-falls back to `sitemap.xml`, then WebSearch |
| Few tokens in `summary` | Document uses a shape the parser misses | Apply the DESIGN.md by hand — do not guess |
| `--render` says patchright missing | No browser lib | `.venv/bin/python`, or `pip install patchright && patchright install chrome` |

## Limits

- Refero publishes no documented API; `search`/`fetch` probe URL shapes and parse pages.
  A redesign can break extraction — `probe` isolates which layer.
- A DESIGN.md describes a marketing/product site. Dense product UI (tables, dashboards)
  needs decisions the reference does not make; make them, and say which were yours.
- This is design direction. Do not reproduce a brand's logo, wordmark, or identity.
