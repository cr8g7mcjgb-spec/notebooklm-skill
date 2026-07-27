# Design Intelligence System

A design **reasoning** engine, not an image RAG.

Retrieving a similar-looking JPEG does not help Claude design better — Claude
cannot see the grid inside that JPEG while it writes CSS. This system measures
references (grid, type scale, palette, whitespace, composition), works out what
a *set* of references agrees on, and emits that consensus as hard constraints
and design tokens. Those constraints are what raise the quality of generated
work, because they replace model defaults with the proportions of references
you chose.

```
reference images → measured features → cross-reference consensus → design brief → generated work → critique
                                                                        ↑                              │
                                                                        └──────── taste memory ────────┘
```

---

## What it does

| Capability | How |
| --- | --- |
| Image vector search | Pluggable embedders — `design-features` (no torch), SigLIP2, OpenCLIP, ColPali |
| Hybrid search | Image + text + lexical + style channels fused with Reciprocal Rank Fusion |
| Style extraction | 12-style taxonomy scored by soft numeric constraints, with evidence |
| Layout / type / colour / grid analysis | Deterministic CV, numpy + Pillow only |
| Common-pattern extraction | Per-feature agreement statistics across the top-N references |
| Design briefs | Consensus → markdown constraints + CSS tokens |
| Critique | Score a candidate against the references and list what to fix |
| MCP server | 12 Claude-callable tools |
| Plugin architecture | Registry + entry points for embedders and stores |
| Cache + incremental indexing | Content-addressed; re-indexing an unchanged library is ~free |
| Local and cloud | Embedded (numpy/LanceDB) or server (Qdrant), same code path |

---

## Quick start

```bash
pip install -e ".[mcp]"                      # core is just numpy + pillow

python tools/make_samples.py --out samples   # or point at your own folder
design-intel index samples --tags poster

design-intel search --text "editorial grid, restrained palette" --limit 5
design-intel brief "conference poster" --style editorial --out brief.md
design-intel critique my-draft.png --style editorial
```

`design-intel --help` lists every command: `index`, `search`, `analyze`,
`compare`, `patterns`, `brief`, `critique`, `feedback`, `taste`, `stats`,
`styles`, `serve`.

### As a library

```python
from design_intel import DesignIntelligence

di = DesignIntelligence()
di.index(["~/design-refs"], tags=["poster"])

brief = di.design_brief("conference poster", style="swiss_international")
print(brief["markdown"])       # constraints, palette, checklist
print(brief["tokens"]["grid"]) # {'columns': 6, 'gutter': '1.8%', ...}

report = di.critique("draft.png", text="swiss poster")
print(report["grade"], report["findings"][:3])
```

---

## Connecting it to Claude

`claude_desktop_config.json` (or `.mcp.json` for Claude Code):

```json
{
  "mcpServers": {
    "design-intelligence": {
      "command": "design-intel",
      "args": ["serve"],
      "env": { "DI_INDEX_PATH": "/Users/you/design-refs/.index" }
    }
  }
}
```

Docker instead of a local install:

```json
{
  "mcpServers": {
    "design-intelligence": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "-v", "/Users/you/design-data:/data",
               "-v", "/Users/you/design-refs:/references:ro",
               "design-intel:latest", "serve"]
    }
  }
}
```

### The tools Claude sees

| Tool | Purpose |
| --- | --- |
| `search_reference` | Find references by text, image, similar-to-id, style, tags |
| `extract_style` | Name one design's style, with the measurements behind the call |
| `compare_design` | Where exactly two designs diverge, and how to close the gap |
| `find_common_patterns` | What a reference set agrees on, and where it doesn't |
| `build_design_brief` | Consensus → constraints + tokens. **Call this before designing** |
| `critique_design` | Score your own output against the references |
| `index_references` | Incremental indexing |
| `annotate_reference` | Add captions/tags so keyword search finds things |
| `record_taste` / `taste_profile` | Teach and inspect the learned aesthetic |
| `list_styles` / `index_stats` | Taxonomy and library introspection |

The intended loop is **brief → design → critique**, not "search and eyeball".

---

## Architecture

```
src/design_intel/
├── analysis/     colour, layout, grid, typography, composition  (numpy + Pillow only)
├── style/        taxonomy (12 styles as soft constraints) + classifier
├── patterns/     consensus, palette merging, briefs, comparison, critique
├── embedders/    design-features · siglip2 · openclip · colpali    (registry plugins)
├── stores/       numpy · lancedb · qdrant                          (registry plugins)
├── search/       RRF hybrid retrieval + MaxSim reranking
├── index/        incremental indexer
├── memory/       taste memory (Rocchio vector + feature profile)
├── mcp/          FastMCP server
├── engine.py     the façade everything else is built on
└── cli.py
```

### What was borrowed from where

The instruction was to combine the best of existing work rather than start from
scratch. Concretely:

- **ColPali / ColBERT late interaction** — patch-level vectors scored with
  MaxSim rather than mean-pooled. Implemented as the standard two-stage
  pattern: pooled vector for ANN recall, cached patch grids for reranking
  (`embedders/colpali.py`, `search/hybrid.py`).
- **SigLIP2** as the recommended single-vector tower — the strongest open
  image/text model for mixed document-and-image retrieval, and its
  native-aspect-ratio handling matters when a 3:4 poster must not be
  centre-cropped square.
- **Reciprocal Rank Fusion** (from hybrid BM25+dense retrieval practice) for
  combining channels whose scores are on incompatible scales.
- **LanceDB** for embedded storage — a directory you can mount, with no server
  — and **Qdrant** for the shared/cloud deployment, both behind one interface.
- **Design-token extractors** (d-extract, dembrandt and similar) inspired the
  token output shape — but those read the DOM, which only works for live
  websites. Here the same tokens are recovered from pixels, so the input can be
  a poster, a print scan or a screenshot.
- **Rocchio relevance feedback** for the taste memory, because it is
  inspectable and needs no training.

### Design decisions worth knowing

**Analysis has no heavyweight dependencies.** No OpenCV, no scipy, no torch in
the core path. It installs anywhere and runs at ~0.6 s per image on CPU.

**The default embedder is not a stub.** `design-features` builds a 400-dim
descriptor from perceptual colour distribution, edge orientation, spatial ink
layout and tonal structure. It encodes exactly what designers argue about, so
it retrieves compositions well without a model download. Switch to SigLIP2 or
ColPali when *semantic* content ("a poster about coffee") matters.

**Everything is explainable.** Style scores carry the constraints that fired;
search hits carry why they matched; consensus items carry agreement
percentages. Nothing returns a bare number you cannot interrogate.

**Disagreement is reported, not hidden.** A brief lists "free choices" — traits
the references disagree on. Knowing where the references *don't* agree is
knowing where you are allowed to invent.

---

## Swapping models and stores

```yaml
# design-intel.yaml
embedder: siglip2
embedder_options:
  model: google/siglip2-so400m-patch14-384
  device: cuda
store: qdrant
store_options:
  url: http://localhost:6333
```

Register your own without touching this package:

```python
from design_intel.registry import register_embedder
from design_intel.embedders.base import BaseEmbedder

@register_embedder("my-model")
class MyEmbedder(BaseEmbedder):
    name, dim, supports_text = "my-model", 768, True
    def embed_images(self, images): ...
    def embed_text(self, texts): ...
```

or ship it as a package exposing a `design_intel.embedders` entry point.

Changing the embedder invalidates cached vectors but **keeps** the cached
analysis, so re-indexing after a model swap is cheap.

---

## Deployment

```bash
docker build -t design-intel .                              # CPU, ~250 MB
docker build --build-arg BUILD_EXTRAS="server,torch" -t design-intel:torch .

docker compose up design-intel                              # local, LanceDB
docker compose --profile cloud up                           # + Qdrant
docker compose --profile http up                            # HTTP transport
```

Config resolution order: defaults → config file → `DI_*` env vars → explicit
arguments. See `design-intel.example.yaml`.

---

## Tests

```bash
pip install -e ".[dev]"
pytest -q          # 51 tests, ~30 s
```

Fixtures generate their own synthetic reference library (`tools/make_samples.py`)
— no copyrighted design work is checked in. Tests assert directional properties
("a dense page has less whitespace than a minimal one") rather than exact
numbers, so tuning the analyzer does not produce meaningless failures.

---

## Honest limitations

- **Typography is measured, not read.** There is no OCR and no font
  identification. Line heights, type scale, alignment, stroke weight and
  hierarchy depth are recovered geometrically; the actual typeface is not
  identified. `letterform_class` is a coarse heuristic from stroke-width
  statistics, not a classification you should trust for font matching.
- **Style classification is rules-first.** On the synthetic test set it matches
  the intended label 18/20; the two misses are Swiss references sparse enough to
  read as minimal, which is genuinely ambiguous. Enabling a text-capable
  embedder adds zero-shot scores to the ensemble and improves this on real
  imagery — but the rule scores remain the anchor, by design.
- **Grid inference reports what is *visible*.** A 12-column grid used loosely,
  with elements spanning 4 columns each, reads as a 3-column grid — because
  that is what the composition shows. `column_confidence` tells you how much to
  trust the call.
- **Taste memory is retrieval-side.** It re-ranks and shapes briefs. No model
  weights change anywhere.
- **Consensus needs a coherent set.** Feeding 8 unrelated references produces
  low agreement on everything, which the report will say plainly rather than
  inventing rules.

## License

MIT
