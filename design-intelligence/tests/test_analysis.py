"""Analysis-layer tests.

These assert *directional* properties ("a dense brutalist page has less
whitespace than a minimal one") rather than exact numbers. Pinning exact values
would make every tuning change a test failure without saying anything about
whether the analyzer got better.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from design_intel.analysis import analyze_image, load_image
from design_intel.analysis.imaging import (
    connected_components,
    from_hex,
    kmeans,
    lab_to_rgb,
    rgb_to_lab,
    to_hex,
    wcag_contrast,
)

# -- colour space ---------------------------------------------------------


def test_lab_roundtrip_is_stable():
    rng = np.random.default_rng(0)
    rgb = rng.random((64, 3), dtype=np.float32)
    back = lab_to_rgb(rgb_to_lab(rgb))
    assert np.allclose(rgb, back, atol=2e-3)


def test_known_lab_values():
    white = rgb_to_lab(np.array([[1.0, 1.0, 1.0]], dtype=np.float32))[0]
    assert white[0] == pytest.approx(100.0, abs=0.5)
    assert abs(white[1]) < 1.0 and abs(white[2]) < 1.0

    black = rgb_to_lab(np.array([[0.0, 0.0, 0.0]], dtype=np.float32))[0]
    assert black[0] == pytest.approx(0.0, abs=0.5)


def test_wcag_contrast_matches_spec():
    white = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    black = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    assert wcag_contrast(white, black) == pytest.approx(21.0, abs=0.05)
    assert wcag_contrast(white, white) == pytest.approx(1.0, abs=1e-6)


def test_hex_roundtrip():
    assert to_hex(from_hex("#3a7bd5")) == "#3a7bd5"
    assert to_hex(from_hex("#fff")) == "#ffffff"


# -- kernels --------------------------------------------------------------


def test_kmeans_is_deterministic():
    rng = np.random.default_rng(3)
    data = np.vstack([rng.normal(0, 0.1, (60, 2)), rng.normal(5, 0.1, (60, 2))]).astype(np.float32)
    a_centroids, a_labels = kmeans(data, k=2)
    b_centroids, b_labels = kmeans(data, k=2)
    assert np.array_equal(a_labels, b_labels)
    assert np.allclose(a_centroids, b_centroids)
    # the two clusters must actually be separated
    assert np.linalg.norm(a_centroids[0] - a_centroids[1]) > 4.0


def test_connected_components_counts_disjoint_blobs():
    mask = np.zeros((40, 40), dtype=bool)
    mask[2:8, 2:8] = True
    mask[20:30, 20:35] = True
    boxes = connected_components(mask)
    assert len(boxes) == 2
    areas = sorted(b[4] for b in boxes)
    assert areas == [36, 150]


def test_connected_components_joins_diagonal_touch():
    mask = np.zeros((10, 10), dtype=bool)
    mask[2, 2] = mask[3, 3] = True  # 8-connected
    assert len(connected_components(mask)) == 1


# -- loading --------------------------------------------------------------


def test_alpha_is_flattened_onto_white(tmp_path: Path):
    img = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    path = tmp_path / "transparent.png"
    img.save(path)
    loaded = load_image(path)
    assert loaded.rgb.mean() > 0.95  # white, not black


def test_original_dimensions_survive_downscaling(tmp_path: Path):
    path = tmp_path / "big.png"
    Image.new("RGB", (2400, 1200), "white").save(path)
    loaded = load_image(path, max_edge=256)
    assert (loaded.width, loaded.height) == (2400, 1200)
    assert max(loaded.shape) <= 256


# -- end-to-end analysis --------------------------------------------------


def test_whitespace_ordering_matches_intuition(sample_files):
    minimal = analyze_image(sample_files["minimal"][0]).layout.whitespace_ratio
    brutalist = analyze_image(sample_files["brutalist"][0]).layout.whitespace_ratio
    assert minimal > 0.85
    assert brutalist < 0.4
    assert minimal > brutalist


def test_colourful_reference_reports_multiple_hues(sample_files):
    bauhaus = analyze_image(sample_files["bauhaus"][0]).color
    swiss = analyze_image(sample_files["swiss"][0]).color
    assert bauhaus.hue_count >= 2
    assert bauhaus.mean_chroma > swiss.mean_chroma


def test_hue_count_is_never_zero(sample_files):
    """A fully desaturated design still has one (achromatic) family."""
    for paths in sample_files.values():
        assert analyze_image(paths[0]).color.hue_count >= 1


def test_text_heavy_reference_finds_more_lines(sample_files):
    dense = analyze_image(sample_files["editorial"][0]).typography
    sparse = analyze_image(sample_files["minimal"][0]).typography
    assert dense.text_lines > 10
    assert sparse.text_lines <= 2
    assert dense.hierarchy_depth >= 2


def test_flush_left_layouts_are_detected_as_left_aligned(sample_files):
    for style in ("editorial", "brutalist"):
        assert analyze_image(sample_files[style][0]).typography.dominant_alignment == "left"


def test_multi_column_layout_reports_a_grid(sample_files):
    grid = analyze_image(sample_files["editorial"][0]).grid
    assert grid.columns >= 2
    assert grid.column_confidence > 0.3


def test_grid_scoring_is_not_biased_toward_more_columns(sample_files):
    """Chance-correction must stop 16 columns winning by having more lines."""
    counts = [analyze_image(p[0]).grid.columns for p in sample_files.values()]
    assert counts.count(16) <= 1


def test_noise_is_low_for_clean_vector_art(sample_files):
    """Grain must measure texture in flat areas, not edge density."""
    assert analyze_image(sample_files["minimal"][0]).composition.noise < 0.2


def test_analysis_is_deterministic(sample_files):
    path = sample_files["swiss"][0]
    first = analyze_image(path).to_dict()
    second = analyze_image(path).to_dict()
    assert first == second


def test_blank_canvas_does_not_crash(tmp_path: Path):
    path = tmp_path / "blank.png"
    Image.new("RGB", (400, 400), "white").save(path)
    features = analyze_image(path)
    assert features.layout.whitespace_ratio >= 0.9
    assert features.typography.text_lines == 0
    assert features.color.contrast_ratio >= 1.0
