---
name: refero-styles
description: Build visual deliverables from a Refero Styles DESIGN.md, applied verbatim. Use for any UI, page, artifact, poster, deck, document or chart carrying a style direction — a brand, a mood, or a styles.refero.design URL. Look the style up fresh on every request.
---

# Refero Styles → deliverable, verbatim

The values that ship are the values published. Not "inspired by", not rounded.

## Every request is a fresh lookup

1. Never reuse the previous request's style — re-derive intent from *this* message.
2. Never invent a palette. If the lookup fails, stop and say so.
3. Never paraphrase values. Copy them.

## Loop

```bash
S=refero-styles/scripts

# 1. pick — searches document contents, not slug spelling
python3 $S/refero_index.py find "<mood | brand | font name | #hex>"
python3 $S/refero_index.py crawl --limit 500     # first time only, one-off cost

# 2. pull — writes to disk, prints a path. Do NOT pass --full.
python3 $S/refero_fetch.py fetch <slug> --asset all

# 3. build from this, not from the document
python3 $S/refero_tokens.py <cache>/<slug>.DESIGN.md --format brief
```

Prefer the page's published `css` / `tailwind` / `tokens` block over re-parsing the
markdown. Open the saved DESIGN.md only when the build needs layout, component or
do/don't detail — it is several thousand tokens of prose that mostly restates the tokens.

The index selects; it never supplies. The chosen style is always fetched live.

## Apply

- New deliverable → the exact published values.
- Existing codebase → the exact values under its existing token names.
- Tokens transfer everywhere. Layout transfers only where the context matches: no landing
  page rhythm on dashboards, tables or forms.
- Accessibility is the sole exception — fix a WCAG-AA-failing pair minimally, and say so.

## Verify before reporting

```bash
comm -23 <(grep -oiE '#[0-9a-f]{6}' <design.md> | tr A-F a-f | sort -u) \
         <(grep -oiE '#[0-9a-f]{6}' <built-file> | tr A-F a-f | sort -u)
```

Empty, or every miss explained. Then report the slug, the source URL, and anything not
applied verbatim.

## More

- `references/public-extraction.md` — asset priority, partial pages, commands, troubleshooting
- `references/application-checklist.md` — per-format mapping, guardrails, pre-ship checks
