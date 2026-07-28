#!/usr/bin/env python3
"""
Parse a Refero DESIGN.md into structured tokens and emit them in a form the
target deliverable can consume literally.

The point is fidelity: values are carried across verbatim (exact hex, exact font
stack, exact px), never re-derived or "interpreted". Anything the parser is not
confident about is left out of the token set rather than guessed at — the raw
DESIGN.md stays the source of truth and should be read alongside.

Usage:
    refero_tokens.py <design.md> --format css
    refero_tokens.py <design.md> --format tailwind
    refero_tokens.py <design.md> --format json
    refero_tokens.py <design.md> --format python
    refero_tokens.py <design.md> --format summary
"""

import argparse
import json
import re
import sys
from pathlib import Path

HEX = r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b"
FUNC_COLOR = r"(?:rgba?|hsla?|oklch|lab|color)\([^)]{3,80}\)"
COLOR = f"(?:{HEX}|{FUNC_COLOR})"

# Labels that carry meaning worth keeping as a token name.
LABEL_CHARS = r"[A-Za-z][A-Za-z0-9 _/&+-]{0,40}"

GENERIC_FONT = {
    "sans-serif", "serif", "monospace", "system-ui", "ui-sans-serif", "ui-serif",
    "ui-monospace", "cursive", "fantasy", "inherit", "initial", "-apple-system",
    "blinkmacsystemfont", "segoe ui",
}


# --------------------------------------------------------------------------
# Section splitting
# --------------------------------------------------------------------------

def split_sections(md):
    """{heading: body} for every ## / ### heading, plus '' for the preamble."""
    sections, current, buf = {}, "", []
    for line in md.splitlines():
        m = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", line)
        if m:
            sections[current] = "\n".join(buf)
            current, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf)
    return sections


def sections_matching(sections, *keywords):
    """Bodies of every section whose heading mentions one of the keywords."""
    out = []
    for heading, body in sections.items():
        low = heading.lower()
        if any(k in low for k in keywords):
            out.append(body)
    return out


def slugify(label):
    s = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------

def extract_colors(md, sections):
    """Ordered {name: value}. Labelled forms win; bare colors fill in after."""
    colors, seen_values = {}, set()

    def add(name, value):
        name = slugify(name)
        if not name or name in colors:
            return
        colors[name] = value
        seen_values.add(value.lower())

    scoped = "\n".join(sections_matching(sections, "color", "palette", "theme", "brand")) or md

    for text in (scoped, md):
        # | Label | #hex |  (markdown table)
        for label, value in re.findall(
            rf"^\s*\|\s*({LABEL_CHARS}?)\s*\|[^|]*?({COLOR})", text, re.MULTILINE
        ):
            add(label, value)
        # - Label: #hex   /   * Label — #hex   /   Label = #hex
        for label, value in re.findall(
            rf"^\s*(?:[-*+]\s*)?({LABEL_CHARS}?)\s*[:=—–-]\s*(?:`)?({COLOR})", text, re.MULTILINE
        ):
            add(label, value)
        # --custom-property: #hex
        for label, value in re.findall(rf"--([a-z0-9-]+)\s*:\s*({COLOR})", text, re.IGNORECASE):
            add(label, value)
        if colors:
            break

    # Bare colors that no label claimed, in document order. Colors that only ever
    # appear inside a shadow value are part of that shadow, not the palette.
    shadow_only = set()
    for body in sections_matching(sections, "shadow", "elevation", "depth"):
        shadow_only.update(v.lower() for v in re.findall(COLOR, body))

    extra = 0
    for value in re.findall(COLOR, md):
        if value.lower() in seen_values or value.lower() in shadow_only:
            continue
        extra += 1
        seen_values.add(value.lower())
        colors[f"palette-{extra}"] = value

    return colors


def extract_fonts(md, sections):
    """Ordered {role: font stack}."""
    fonts = {}
    scoped = "\n".join(sections_matching(sections, "typograph", "font", "type")) or md

    def add(role, stack):
        stack = stack.strip().rstrip(",;").strip()
        # Only unwrap quotes that wrap the *whole* stack — a stack like
        # '"Inter Variable", Inter, sans-serif' must keep its inner quoting.
        while len(stack) > 1 and stack[0] == stack[-1] and stack[0] in "`\"'":
            stack = stack[1:-1].strip()
        if not stack:
            return
        head = stack.split(",")[0].strip().strip("`\"'")
        if not head or head.lower() in GENERIC_FONT or len(head) > 60:
            return
        role = slugify(role) or "font"
        if role not in fonts:
            fonts[role] = stack

    for prop, stack in re.findall(
        r"--font-([a-z0-9-]+)\s*:\s*([^;\n]+)", scoped, re.IGNORECASE
    ):
        add(prop, stack)
    for stack in re.findall(r"font-family\s*:\s*([^;\n}]+)", scoped, re.IGNORECASE):
        add(f"family-{len(fonts) + 1}", stack)
    # - Display: Inter, 48px/1.1   →  role "display", stack "Inter"
    for role, stack in re.findall(
        rf"^\s*(?:[-*+]\s*)?({LABEL_CHARS}?)\s*[:—–]\s*([A-Z][A-Za-z0-9 .'-]{{1,40}}(?:,\s*[A-Za-z0-9 .'-]+)*)",
        scoped,
        re.MULTILINE,
    ):
        if re.search(r"\d", stack.split(",")[0]):
            continue
        add(role, stack)

    return fonts


def extract_type_scale(sections):
    """[{'size': '48px', 'line_height': '1.1', 'label': 'Display'}] in doc order."""
    scale, seen = [], set()
    # "type" covers a bare "Type" heading; it deliberately excludes "Spacing scale".
    for body in sections_matching(sections, "typograph", "type", "font size", "text"):
        for line in body.splitlines():
            sizes = re.findall(r"\b(\d{1,3}(?:\.\d+)?)(px|rem|pt)\b", line)
            if not sizes:
                continue
            label = re.match(rf"^\s*(?:[-*+|]\s*)?({LABEL_CHARS}?)\s*[:|—–-]", line)
            lh = re.search(r"/\s*([\d.]+)|line[- ]height\s*[:=]?\s*([\d.]+)", line, re.IGNORECASE)
            leading = (lh.group(1) or lh.group(2)) if lh else ""
            if not leading:
                # Table form: a unitless decimal in its own column is the leading.
                for cand in re.findall(r"(?<![\w.])(\d\.\d{1,3})(?![\w%])", line):
                    if 0.8 <= float(cand) <= 3.0:
                        leading = cand
                        break
            size = f"{sizes[0][0]}{sizes[0][1]}"
            key = (size, label.group(1).strip() if label else "")
            if key in seen:
                continue
            seen.add(key)
            scale.append({
                "label": (label.group(1).strip() if label else ""),
                "size": size,
                "line_height": leading,
            })
    return scale


def extract_scale(sections, *keywords):
    """Ordered, de-duplicated length values under the matching sections."""
    values, seen = [], set()
    for body in sections_matching(sections, *keywords):
        for value in re.findall(r"\b(\d{1,4}(?:\.\d+)?)(px|rem|em|%)\b", body):
            v = f"{value[0]}{value[1]}"
            if v not in seen:
                seen.add(v)
                values.append(v)
        # Bare number sequences: "4 / 8 / 12 / 16"
        for run in re.findall(r"(?:\b\d{1,3}\b\s*[/,·]\s*){2,}\b\d{1,3}\b", body):
            for n in re.findall(r"\d{1,3}", run):
                v = f"{n}px"
                if v not in seen:
                    seen.add(v)
                    values.append(v)
    return values


def extract_shadows(sections):
    shadows, seen = [], set()
    for body in sections_matching(sections, "shadow", "elevation", "depth"):
        for line in body.splitlines():
            # Commas belong to the value (rgba(...), multi-layer shadows); only a
            # table pipe or the line end terminates it.
            m = re.search(r"((?:inset\s+)?-?\d+(?:px|rem)\s+-?\d+(?:px|rem)[^|\n]*)", line)
            if m and ("rgba" in line or "rgb" in line or "#" in line or "shadow" in line.lower()):
                v = m.group(1).strip().rstrip(";|").strip()
                if v not in seen:
                    seen.add(v)
                    shadows.append(v)
    return shadows


def parse(md):
    sections = split_sections(md)
    return {
        "colors": extract_colors(md, sections),
        "fonts": extract_fonts(md, sections),
        "type_scale": extract_type_scale(sections),
        "spacing": extract_scale(sections, "spacing", "space", "gap", "grid"),
        "radius": extract_scale(sections, "radius", "corner", "rounding"),
        "shadows": extract_shadows(sections),
        "sections": [h for h in sections if h],
    }


# --------------------------------------------------------------------------
# Emitters
# --------------------------------------------------------------------------

def emit_css(t, selector=":root"):
    lines = [f"{selector} {{"]
    for name, value in t["colors"].items():
        lines.append(f"  --color-{name}: {value};")
    for role, stack in t["fonts"].items():
        lines.append(f"  --font-{role}: {stack};")
    for i, step in enumerate(t["type_scale"]):
        label = slugify(step["label"]) or f"step-{i + 1}"
        lines.append(f"  --text-{label}: {step['size']};")
        if step["line_height"]:
            lines.append(f"  --leading-{label}: {step['line_height']};")
    for i, value in enumerate(t["spacing"]):
        lines.append(f"  --spacing-{i + 1}: {value};")
    for i, value in enumerate(t["radius"]):
        lines.append(f"  --radius-{i + 1}: {value};")
    for i, value in enumerate(t["shadows"]):
        lines.append(f"  --shadow-{i + 1}: {value};")
    lines.append("}")
    return "\n".join(lines)


def emit_tailwind(t):
    """Tailwind v4 @theme block — utilities generate straight off these names."""
    body = emit_css(t, selector="@theme")
    return "@import \"tailwindcss\";\n\n" + body


def emit_python(t):
    """Literal dict for python-pptx / python-docx / matplotlib scripts."""
    return "REFERO_TOKENS = " + json.dumps(t, indent=4, ensure_ascii=False)


def emit_summary(t):
    out = []
    out.append(f"colors     {len(t['colors'])}  " + ", ".join(
        f"{k}={v}" for k, v in list(t["colors"].items())[:8]))
    out.append(f"fonts      {len(t['fonts'])}  " + "; ".join(
        f"{k}: {v}" for k, v in list(t["fonts"].items())[:4]))
    out.append(f"type scale {len(t['type_scale'])}  " + ", ".join(
        f"{s['label'] or '?'}={s['size']}" for s in t["type_scale"][:8]))
    out.append(f"spacing    {len(t['spacing'])}  " + ", ".join(t["spacing"][:12]))
    out.append(f"radius     {len(t['radius'])}  " + ", ".join(t["radius"][:8]))
    out.append(f"shadows    {len(t['shadows'])}")
    out.append("sections   " + ", ".join(t["sections"][:12]))
    return "\n".join(out)


EMITTERS = {
    "css": emit_css,
    "tailwind": emit_tailwind,
    "json": lambda t: json.dumps(t, indent=2, ensure_ascii=False),
    "python": emit_python,
    "summary": emit_summary,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("design_md", help="path to a fetched DESIGN.md ('-' for stdin)")
    ap.add_argument("--format", choices=sorted(EMITTERS), default="summary")
    ap.add_argument("--save", help="write the emitted output to this path")
    args = ap.parse_args()

    md = sys.stdin.read() if args.design_md == "-" else Path(args.design_md).read_text(encoding="utf-8")
    tokens = parse(md)

    if not tokens["colors"] and not tokens["fonts"]:
        print(
            "No tokens recognised. Apply the DESIGN.md by hand — read it directly "
            "rather than trusting this parser.",
            file=sys.stderr,
        )
        return 1

    output = EMITTERS[args.format](tokens)
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(output + "\n", encoding="utf-8")
        print(f"saved: {args.save}", file=sys.stderr)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
