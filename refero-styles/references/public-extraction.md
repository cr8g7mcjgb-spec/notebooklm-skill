# Public extraction

How to get a complete style out of a public `styles.refero.design` page, and what to do
when what comes back is partial.

## Priority order

A style page publishes several outputs of the same design. Take them in this order — each
step down adds a place where fidelity can be lost:

| Rank | Source | Why |
|---|---|---|
| 1 | Published `CSS Variables` / `Tailwind v4` / `Design Tokens` | Already implementation-ready. Nothing is re-derived, so nothing can drift |
| 2 | `DESIGN.md` → `refero_tokens.py` | One parsing step, values still copied literally |
| 3 | `DESIGN.md` read directly | For anything the parser did not recognise |
| 4 | WebFetch on the page | A model summary, not the document — last resort only |

```bash
python3 scripts/refero_fetch.py fetch <slug> --asset all      # everything it publishes
python3 scripts/refero_fetch.py fetch <slug> --asset css      # just CSS Variables
python3 scripts/refero_fetch.py fetch <slug> --asset tailwind # just the v4 @theme block
```

Match the asset to the stack: `css` for plain CSS and CSS modules, `tailwind` for Tailwind
v4, `tokens` for a theme object in CSS-in-JS, `design-md` when translating into a custom
component system or a non-web deliverable (pptx, docx, poster).

## When the page comes back partial

Symptoms and what each one means:

| Symptom | What it is | Next step |
|---|---|---|
| `probe` returns `0` on every row | The host's egress blocks the domain | Nothing in the skill fixes this. Run where the network is open |
| `403` on every URL | Site-side WAF rejecting the request | `--render` |
| `200`, but no asset extracted | Content only exists after hydration | `--render` |
| `--render` works but only some assets found | The page genuinely publishes only those | Use what exists; derive the rest from `design-md` |
| An asset extracts but looks truncated | A scoring tie picked a fragment | Re-run with `--asset all` and compare; if wrong, read the DESIGN.md directly |
| Nothing anywhere | Slug is wrong | Re-run `search`; the slug may not match the brand name |

## Finding the right page

```bash
python3 scripts/refero_fetch.py search "<brand or mood>"
```

`search` tries the site's query endpoints, then falls back to `sitemap.xml` / `llms.txt`
filtered by the query terms. If it returns nothing, use WebSearch:

```
site:styles.refero.design/style/ <brand> Refero Styles
```

Search moods in English even when the request was in another language — the slugs and page
text are English.

## What a complete extraction contains

Check that you have all of these before building; a gap here becomes an invented value later:

- theme (light/dark, overall stance)
- color palette
- typography — families, scale, weights
- spacing
- radius
- elevation / shadows
- layout
- components
- imagery
- do/don't rules
- agent prompt guide

Anything genuinely absent from the page is a decision you make, and it must be reported as
yours rather than presented as part of the reference.

## Never fill a gap by guessing

A failed or partial extraction is reported, not papered over. Do not substitute a plausible
"minimal" palette, do not round a missing spacing step to a familiar scale, and do not
carry values over from a style used earlier in the session. Stop and say what is missing.
