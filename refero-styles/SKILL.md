---
name: refero-styles
description: Find and apply a DESIGN.md from Refero Styles (styles.refero.design) — a public registry of 2,000+ design systems extracted from real product sites, each with colors, typography, spacing, radius, elevation, components, and do/don't rules. Use when the user wants a visual direction for a UI and names a reference ("make it look like Linear/Stripe/Vercel", "이 사이트 스타일로", "레퍼런스 디자인 가져와"), describes a mood ("minimal", "editorial", "high contrast", "미니멀하게", "감성적으로"), mentions Refero / DESIGN.md / refero styles, or shares a styles.refero.design URL. Not for editing an existing design system the project already owns.
---

# Refero Styles → DESIGN.md

Turn "make it look like X" into a concrete token set the codebase can actually use.
Refero Styles publishes a DESIGN.md per site — theme, color palette, typography, spacing,
radius, elevation, layout, components, imagery, do/don't rules, and an agent prompt guide.
This skill finds the right one and applies it, so the user never copy-pastes markdown.

## When to Use

Trigger when the user:
- Names a reference product/brand for visual direction ("Linear 느낌으로", "like Stripe's site")
- Describes a mood instead of a brand ("minimal", "editorial", "playful", "high contrast")
- Mentions Refero, Refero Styles, or DESIGN.md
- Shares a `https://styles.refero.design/...` URL

Do **not** trigger when the project already has its own design system and the user is
editing it — that is ordinary styling work, not a reference lookup.

## Workflow

### Step 1 — Resolve the reference

Turn the request into either a **brand slug** or **mood keywords**. If the user gave a
Refero URL, skip to Step 2 with that URL.

```bash
python3 refero-styles/scripts/refero_fetch.py search linear
python3 refero-styles/scripts/refero_fetch.py search "minimal editorial"
```

Output is `slug<TAB>url`, one candidate per line. If more than one plausibly matches
what the user asked for, ask the user which — do not silently pick.

If `search` finds nothing, fall back to the WebSearch tool:
`site:styles.refero.design/style/ <brand>`.

### Step 2 — Pull the DESIGN.md

```bash
# by slug, or by full URL
python3 refero-styles/scripts/refero_fetch.py fetch linear --full
python3 refero-styles/scripts/refero_fetch.py fetch https://styles.refero.design/style/linear --full

# save straight into the project instead of the cache
python3 refero-styles/scripts/refero_fetch.py fetch linear --save design/refero-linear.DESIGN.md
```

The site is JS-rendered and sits behind a WAF. If plain HTTP is refused the script
retries through a real browser automatically; force it with `--render`, and add
`--show-browser` when debugging. `probe` reports which endpoints are reachable at all:

```bash
python3 refero-styles/scripts/refero_fetch.py probe
```

`--render` needs patchright or playwright. This repo's NotebookLM venv already has
patchright, so run it with that interpreter when the plain HTTP path is blocked:

```bash
.venv/bin/python refero-styles/scripts/refero_fetch.py fetch linear --render
```

WebFetch on the style URL is a valid alternative when the script is unavailable — but
prefer the script, since it saves the full document instead of a model summary.

### Step 3 — Read the project before writing anything

Never apply tokens blind. First establish what the project already uses:

- Tailwind (`tailwind.config.*`, `@theme` in CSS) vs. plain CSS variables vs. CSS-in-JS
- Where the existing palette/type scale lives
- Whether a dark theme exists

The DESIGN.md is a **reference**, not a drop-in file. It describes another product's
site; the local naming and structure win.

### Step 4 — Map, don't paste

Translate the reference into the project's own token layer:

- Colors → existing semantic names (`--color-primary`, `bg-brand`), not raw hexes at call sites
- Typography → the project's existing scale steps, keeping its font loading strategy
- Spacing / radius / elevation → the project's scale, rounded to steps it already has
- Components → adjust the components that exist; do not introduce a component library

Keep the reference file in the repo (`design/refero-<slug>.DESIGN.md`) so later sessions
can see where the direction came from.

### Step 5 — Preserve what the reference cannot know

The DESIGN.md has no knowledge of this app's users or a11y baseline. Non-negotiable:

- Contrast stays at WCAG AA or whatever the project already meets — if a reference pair
  fails, darken/lighten to pass and say so
- Focus states, hit targets, and reduced-motion handling survive
- Do not change semantics, ARIA, or DOM structure to chase a look

### Step 6 — Report

State the slug used, the source URL, which tokens changed, and anything from the
reference you deliberately did not apply (and why).

## Command Reference

| Command | Purpose |
|---|---|
| `search <query> [--limit N]` | Candidate style pages for a brand or mood |
| `fetch <slug\|url> [--save PATH] [--full]` | Extract and store one DESIGN.md |
| `probe` | Report endpoint reachability (diagnostics) |
| `--render` / `--show-browser` | Route through a real browser |

Fetched documents are cached at `~/.claude/skills/refero-styles/cache/<slug>.DESIGN.md`
(override with `REFERO_CACHE_DIR`). Check the cache before refetching.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `probe` shows status `0` on every row | Network policy blocks the domain (sandboxed/remote envs deny CONNECT) | Run from a machine with normal egress; nothing in the skill can work around a proxy denial |
| `403` on every URL | WAF rejecting the request | `--render` |
| `200` but "Could not extract a DESIGN.md" | Markdown only exists after hydration, or the page markup changed | `--render`; if it still fails the extraction heuristics need updating — see `_candidates_from_html` |
| `search` returns nothing | The `?q=` endpoint changed shape | Falls back to `sitemap.xml` automatically; then to WebSearch `site:styles.refero.design/style/` |
| `--render` says patchright not importable | No browser lib on the interpreter | Use `.venv/bin/python`, or `pip install patchright && patchright install chrome` |

## Limitations

- Refero publishes no documented public API. `search`/`fetch` probe candidate URL shapes
  and parse the page; a site redesign can break extraction. `probe` tells you which layer
  broke.
- A DESIGN.md describes a **marketing/product site**, not an app shell. Dense product UI
  (tables, dashboards) needs adaptation beyond what the reference specifies.
- Extraction is heuristic — always skim the saved file before applying it.
- Respect the source: this is design *direction*, not permission to clone a brand.
