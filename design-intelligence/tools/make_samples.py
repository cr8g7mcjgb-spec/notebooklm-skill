"""Generate a synthetic reference library.

Real design libraries are copyrighted, so the demo and the test-suite build
their own: seeded, deterministic posters in five idioms with enough internal
variation that consensus extraction has something real to chew on.

    python tools/make_samples.py --out samples --per-style 4
"""

from __future__ import annotations

import argparse
import random
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 900, 1200


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def text_block(draw: ImageDraw.ImageDraw, xy, width, size, lines, fill, rng, align="left"):
    x, y = xy
    for _ in range(lines):
        line_width = int(width * rng.uniform(0.55, 1.0))
        start = x
        if align == "center":
            start = x + (width - line_width) // 2
        draw.text((start, y), "x" * max(3, line_width // max(1, size // 2)), font=font(size), fill=fill)
        y += int(size * 1.55)
    return y


def swiss(seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), "#f4f3f1")
    d = ImageDraw.Draw(img)
    margin = int(W * 0.09)
    cols, gutter = 6, int(W * 0.02)
    col_w = (W - 2 * margin - gutter * (cols - 1)) // cols
    accent = rng.choice(["#d92b2b", "#1a4fd6", "#111111"])

    d.text((margin, margin), "Neue\nGrafik", font=font(int(H * 0.075)), fill="#111111", spacing=8)
    y = margin + int(H * 0.21)
    d.rectangle([margin, y, margin + col_w * 2 + gutter, y + 6], fill=accent)

    y += int(H * 0.05)
    for c in range(rng.choice([2, 3])):
        x = margin + c * (col_w + gutter) * 2
        text_block(d, (x, y), col_w * 2, int(H * 0.013), rng.randint(7, 12), "#333333", rng)

    d.rectangle([margin, H - margin - 4, W - margin, H - margin], fill="#111111")
    return img


def bauhaus(seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), "#efe9dd")
    d = ImageDraw.Draw(img)
    red, blue, yellow = "#d62828", "#1d3fb5", "#f6bd16"

    d.ellipse([W * 0.1, H * 0.12, W * 0.72, H * 0.52], fill=rng.choice([red, blue]))
    d.polygon(
        [(W * 0.18, H * 0.78), (W * 0.62, H * 0.42), (W * 0.88, H * 0.82)], fill=yellow
    )
    d.rectangle([W * 0.05, H * 0.62, W * 0.42, H * 0.9], fill=rng.choice([blue, red]))
    d.line([(0, H * 0.58), (W, H * 0.30)], fill="#111111", width=7)
    d.text((int(W * 0.08), int(H * 0.05)), "BAUHAUS", font=font(int(H * 0.05)), fill="#111111")
    return img


def brutalist(seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(img)
    y = 24
    d.rectangle([0, 0, W, int(H * 0.12)], fill="#000000")
    d.text((20, 24), "RAW//INDEX", font=font(int(H * 0.055)), fill="#ffffff")
    y = int(H * 0.14)
    for _ in range(rng.randint(6, 9)):
        y = text_block(d, (20, y), W - 40, int(H * 0.012), rng.randint(4, 7), "#000000", rng)
        d.line([(20, y), (W - 20, y)], fill="#000000", width=3)
        y += 14
        if y > H - 60:
            break
    return img


def editorial(seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), "#fbf8f3")
    d = ImageDraw.Draw(img)
    margin = int(W * 0.07)
    d.text((margin, int(H * 0.06)), "The\nQuiet\nIssue", font=font(int(H * 0.08)), fill="#1a1a1a", spacing=10)
    d.line([(margin, int(H * 0.34)), (W - margin, int(H * 0.34))], fill="#1a1a1a", width=2)
    d.text((margin, int(H * 0.36)), "essays on making", font=font(int(H * 0.022)), fill="#8a6b3a")

    cols = rng.choice([2, 3])
    gutter = int(W * 0.03)
    col_w = (W - 2 * margin - gutter * (cols - 1)) // cols
    for c in range(cols):
        text_block(
            d,
            (margin + c * (col_w + gutter), int(H * 0.42)),
            col_w,
            int(H * 0.0115),
            rng.randint(22, 30),
            "#2b2b2b",
            rng,
        )
    return img


def minimal(seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), "#f7f7f5")
    d = ImageDraw.Draw(img)
    cx, cy = rng.uniform(0.55, 0.72), rng.uniform(0.38, 0.52)
    r = W * rng.uniform(0.05, 0.09)
    d.ellipse([cx * W - r, cy * H - r, cx * W + r, cy * H + r], outline="#22201d", width=2)
    d.text((int(W * 0.12), int(H * 0.83)), "ma", font=font(int(H * 0.03)), fill="#22201d")
    return img


GENERATORS = {
    "swiss": swiss,
    "bauhaus": bauhaus,
    "brutalist": brutalist,
    "editorial": editorial,
    "minimal": minimal,
}


def generate(out: Path, per_style: int = 4, styles: list[str] | None = None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in styles or list(GENERATORS):
        maker = GENERATORS[name]
        for i in range(per_style):
            path = out / f"{name}_{i:02d}.png"
            # crc32, not hash(): str hashing is salted per process, so hash()
            # would regenerate different "samples" on every run
            maker(zlib.crc32(f"{name}:{i}".encode()) & 0xFFFF).save(path)
            written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="samples", type=Path)
    parser.add_argument("--per-style", type=int, default=4)
    parser.add_argument("--styles", nargs="*", choices=sorted(GENERATORS))
    args = parser.parse_args()
    written = generate(args.out, args.per_style, args.styles)
    print(f"wrote {len(written)} images to {args.out}")


if __name__ == "__main__":
    main()
