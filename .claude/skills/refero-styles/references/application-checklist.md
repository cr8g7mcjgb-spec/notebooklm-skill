# Application checklist

Run before shipping a deliverable built from a Refero style. The goal is a transfer that is
both faithful to the reference's values and usable for what is actually being built.

## 1. Fidelity — the values shipped are the values published

- [ ] Every color in the source appears in the output, or its absence is explained
- [ ] Font families match the reference, including the fallback chain
- [ ] Type scale sizes and line heights are the published numbers, not rounded
- [ ] Spacing, radius and shadow values are the published ones, not snapped to a familiar scale
- [ ] Nothing was carried over from a style used earlier in the session

```bash
grep -oiE '#[0-9a-f]{6}' <design.md> | tr 'A-F' 'a-f' | sort -u > /tmp/want.txt
grep -oiE '#[0-9a-f]{6}' <built-file> | tr 'A-F' 'a-f' | sort -u > /tmp/got.txt
comm -23 /tmp/want.txt /tmp/got.txt   # in the reference, missing from the build
comm -13 /tmp/want.txt /tmp/got.txt   # in the build, absent from the reference — invented
```

The second list should be empty. For `.pptx` / `.docx` / `.pdf`, run the check against the
generation script instead of the binary.

## 1b. How tokens land, per deliverable

| Deliverable | How the tokens land |
|---|---|
| HTML page / artifact | `--asset css`, or `--format css` into `:root`; rules reference `var(--…)` |
| React / Tailwind v4 | `--asset tailwind` into the CSS entry; use the generated utilities |
| React / CSS-in-JS | `--asset tokens`, or `--format json`, imported as the theme object |
| `.pptx` (python-pptx) | `--format python`; hexes → `RGBColor.from_string`, sizes → `Pt` |
| `.docx` (python-docx) | `--format python`; styles set from the same dict |
| Poster / chart / canvas | `--format json`; palette drives fills, type scale drives labels |
| Existing codebase | The exact values under the project's existing token names |

Carry across what is not a token too: layout notes, component sizing, imagery direction
and do/don't rules are part of the reference.

## 2. Fonts that cannot be loaded

If the reference's family is unavailable to the app, pick the closest local or web-safe
fallback and **keep the reference's weights, sizes, letter-spacing and hierarchy**. Say
which family you substituted and why. Never silently drop to a system default.

## 3. Fit for the deliverable

A DESIGN.md is extracted from a marketing or product site. Do not push its decorative
patterns onto interfaces they were never meant for:

- [ ] Dashboards, tools, tables and forms stay **dense, scannable and task-focused** — the
      reference's palette and type apply; its hero spacing and full-bleed rhythm do not
- [ ] Information architecture is driven by the product's needs, not the reference's page
- [ ] Motion-heavy effects are not introduced into operational UI
- [ ] Long-form and data-dense views keep their own measure and line length

Tokens transfer everywhere. Layout transfers only where the context matches.

## 4. Accessibility — the one place fidelity yields

- [ ] Every foreground/background pair meets WCAG AA at its used size
- [ ] Any pair adjusted for contrast is reported, with the before and after values
- [ ] Focus states are visible on all interactive elements
- [ ] Hit targets stay at least 44×44px on touch
- [ ] `prefers-reduced-motion` is respected
- [ ] Semantics, ARIA and DOM structure were not changed to chase a look

## 5. Ownership

- [ ] No logos, wordmarks, brand illustrations or copy from the reference were reproduced
- [ ] Imagery direction was followed; imagery assets were not lifted
- [ ] Only style principles and token values crossed over

## 6. Responsive and states

- [ ] The result holds at the app's existing breakpoints, not the reference site's
- [ ] Hover, active, disabled, loading, empty and error states are styled from the same tokens
- [ ] Dark mode is handled if the reference or the app defines one

## 7. Reporting

State the slug, the source URL, the saved file path, and which published asset was used
(`css` / `tailwind` / `tokens` / `design-md`). Then list anything not applied verbatim,
with the reason — the only acceptable ones being a contrast fix, an unavailable font, or a
value the reference does not specify. Decisions the reference did not make are yours, and
should be labelled that way.

## 8. Claims

Do not describe the result as using Refero MCP or any private Refero access unless an MCP
server is actually configured in the session. Public pages are what this skill reads.
