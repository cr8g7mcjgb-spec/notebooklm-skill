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

```bash
python3 refero-styles/scripts/refero_fetch.py search linear
python3 refero-styles/scripts/refero_fetch.py search "minimal editorial warm"
```

Output is `slug<TAB>url`. Take the top match and keep going — state which slug you used so
the user can redirect. Only ask when the top candidates are genuinely different
directions and picking wrong would waste the whole build.

If `search` returns nothing, fall back to WebSearch: `site:styles.refero.design/style/ <query>`.

### Step 3 — Pull the DESIGN.md, verbatim

```bash
python3 refero-styles/scripts/refero_fetch.py fetch <slug> \
  --save design/refero-<slug>.DESIGN.md --full
```

Read the saved file. It — not your memory of it — is the source of truth.

If plain HTTP is refused the script retries through a real browser; force with `--render`,
and use this repo's venv interpreter when patchright is needed:

```bash
.venv/bin/python refero-styles/scripts/refero_fetch.py fetch <slug> --render
```

### Step 4 — Turn it into tokens

```bash
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

## Accessibility — the one place fidelity yields

Copy the values exactly, except where a foreground/background pair from the reference
fails WCAG AA at its used size. Adjust the minimum necessary, keep every other value
untouched, and say which pair you changed and to what. Focus states, hit targets, and
reduced-motion handling survive regardless.

## Command reference

| Command | Purpose |
|---|---|
| `refero_fetch.py search <query> [--limit N]` | Candidate style pages |
| `refero_fetch.py fetch <slug\|url> [--save P] [--full]` | Extract one DESIGN.md |
| `refero_fetch.py probe` | Endpoint reachability (diagnostics) |
| `refero_tokens.py <file> --format <fmt>` | css / tailwind / json / python / summary |
| `--render` / `--show-browser` | Route through a real browser |

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
