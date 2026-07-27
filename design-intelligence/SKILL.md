---
name: design-intelligence
description: Use this skill when designing anything visual (poster, landing page, deck, UI, print layout) and a reference library is available. Retrieves references, extracts measured design constraints (grid, type scale, palette, whitespace) from them, and critiques the result. Also use when asked to analyse a design's style, compare two designs, or learn the user's taste.
---

# Design Intelligence

A design reasoning engine over an indexed reference library. It measures
references and converts them into constraints you can actually design against.

## When to use

- The user asks for any visual artifact and has (or wants) a reference library
- "make this look like X", "match our style", "what style is this?"
- "why does my draft feel off compared to these references?"
- The user reacts to a direction ("love this", "too busy") — record it

## The one rule

**Get the brief before you design.** Searching and eyeballing a thumbnail does
not transfer a design language; measured constraints do.

```
build_design_brief  →  design against its tokens  →  critique_design  →  fix findings
```

## Workflow

### 1. Check the library exists

```
index_stats()
```

Empty? Index first — ask for a folder of references:

```
index_references(paths=["~/design-refs"], collection="posters", tags=["poster"])
```

### 2. Get constraints

```
build_design_brief(intent="conference poster for a type foundry",
                   style="swiss_international", limit=8)
```

Returns markdown constraints **and** `tokens`: colour variables, a type scale,
grid columns, gutters, spacing steps, target whitespace and contrast.

Use `find_common_patterns` instead when you want the raw agreement analysis
without the token scaffolding.

### 3. Design against the tokens

Treat `rules` as hard constraints. Use the exact hex values, the exact column
count, the stated number of type sizes. Do not average them toward your default
— the entire point is that the references' proportions replace your defaults.

`divergence` (free choices) marks where the references disagree: invent there.

### 4. Critique what you made

Render your output to an image, then:

```
critique_design(target="/path/to/render.png", text="swiss poster")
```

Findings come with the measured value, the expected range and the fix. Apply
them and re-run. A score below ~0.7 means it does not read as the same family.

### 5. Record reactions

When the user praises or rejects a direction:

```
record_taste(ref_id="<id>", verdict="like", note="<their words>")
```

After a handful of judgements, `taste_profile()` reports the traits they are
consistent about, and future briefs carry them automatically.

## Other tools

- `search_reference(text=, image_path=, like_id=, style=, tags=, limit=)` —
  find specific references. Combine freely; partial queries work.
- `extract_style(target=)` — name a design's style with the evidence.
- `compare_design(a=, b=)` — where two designs diverge, with suggestions.
- `annotate_reference(ref_id=, caption=, tags=)` — captions make keyword search
  work; add them when the user describes what a reference is.
- `list_styles()` — the taxonomy (swiss_international, bauhaus, brutalism,
  neo_brutalism, editorial, minimal, japanese_minimal, corporate_modern, y2k,
  memphis, glassmorphism, retro_print).

## Reading the output

- `agreement` on a consensus item: how strongly the references concur. Above
  ~0.8 treat as binding; 0.6–0.8 as a strong default.
- `column_confidence`: how clearly a grid is visible. Low means the references
  genuinely lack a strict grid — do not invent one.
- `confidence` on a style call: below ~0.5 the design is stylistically
  ambiguous; say so rather than asserting a label.

## Don't

- Don't dump feature JSON at the user. Report the decision and the constraints.
- Don't call `search_reference` and then design from the filenames. Get the brief.
- Don't assert a font. Typography is measured geometrically; the typeface is
  not identified.
- Don't run `find_common_patterns` over an incoherent set — mixed references
  yield low agreement on everything, which is a real (and reportable) answer.

## Setup

```bash
pip install -e "design-intelligence[mcp]"
design-intel index ~/design-refs
```

MCP registration and Docker deployment: see `design-intelligence/README.md`.
