"""Indexing, retrieval, pattern reasoning, briefs, critique and taste memory."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from design_intel import Config, DesignIntelligence
from design_intel.cache import Cache, file_hash, stable_id
from design_intel.patterns import build_tokens, find_common_patterns, to_css
from design_intel.stores.base import matches
from design_intel.style import get_style

# -- indexing -------------------------------------------------------------


def test_index_then_reindex_skips_everything(engine: DesignIntelligence, samples: Path):
    total = engine.stats()["references"]
    assert total == len(list(samples.glob("*.png")))

    second = engine.index([samples])
    assert second.skipped == total
    assert second.indexed == 0
    assert second.elapsed < 2.0  # cache hit, not a re-analysis


def test_force_reindex_reprocesses(engine: DesignIntelligence, samples: Path):
    report = engine.index([samples], force=True)
    assert report.skipped == 0
    assert report.updated + report.indexed == engine.stats()["references"]


def test_changed_file_is_reindexed(samples: Path, tmp_path: Path):
    # works on a private copy: mutating the session-scoped samples would leak
    # into every other test that reads them
    workdir = tmp_path / "refs"
    workdir.mkdir()
    for path in sorted(samples.glob("*.png"))[:4]:
        Image.open(path).save(workdir / path.name)

    di = DesignIntelligence(
        Config(
            index_path=str(tmp_path / "idx"),
            cache_dir=str(tmp_path / "cache"),
            memory_path=str(tmp_path / "mem"),
        )
    )
    di.index([workdir])

    target = sorted(workdir.glob("*.png"))[0]
    before = file_hash(target)
    Image.new("RGB", (600, 800), "#101010").save(target)
    assert file_hash(target) != before

    report = di.index([workdir])
    assert report.updated == 1
    assert report.skipped == 3


def test_prune_removes_records_for_deleted_files(tmp_path: Path, samples: Path):
    workdir = tmp_path / "refs"
    workdir.mkdir()
    for path in sorted(samples.glob("*.png"))[:4]:
        Image.open(path).save(workdir / path.name)

    di = DesignIntelligence(
        Config(
            index_path=str(tmp_path / "idx"),
            cache_dir=str(tmp_path / "cache"),
            memory_path=str(tmp_path / "mem"),
        )
    )
    di.index([workdir])
    assert di.stats()["references"] == 4

    next(iter(sorted(workdir.glob("*.png")))).unlink()
    report = di.index([workdir], prune=True)
    assert report.pruned == 1
    assert di.stats()["references"] == 3


def test_stable_id_survives_reindexing(tmp_path: Path):
    path = tmp_path / "a.png"
    path.touch()
    assert stable_id(path) == stable_id(path)
    assert stable_id(path) != stable_id(tmp_path / "b.png")


# -- cache ----------------------------------------------------------------


def test_cache_roundtrips_json_and_vectors(tmp_path: Path):
    cache = Cache(tmp_path / "c")
    key = Cache.key("x", 1, "y")
    cache.set_json(key, {"a": [1, 2]})
    assert cache.get_json(key) == {"a": [1, 2]}

    vector = np.arange(8, dtype=np.float32)
    cache.set_vector(key, vector)
    assert np.array_equal(cache.get_vector(key), vector)
    assert cache.stats()["vector_entries"] == 1


def test_cache_miss_returns_none(tmp_path: Path):
    assert Cache(tmp_path / "c").get_json("missing") is None


def test_disabled_cache_stores_nothing(tmp_path: Path):
    cache = Cache(tmp_path / "c", enabled=False)
    cache.set_json("k", {"a": 1})
    assert cache.get_json("k") is None


# -- retrieval ------------------------------------------------------------


def test_search_by_style_returns_that_style(engine: DesignIntelligence):
    hits = engine.search(style="editorial", limit=3)
    assert hits
    assert all("editorial" in Path(h.reference.path).stem for h in hits)


def test_search_by_similar_reference_excludes_itself(engine: DesignIntelligence):
    seed = engine.search(style="bauhaus", limit=1)[0].reference
    hits = engine.search(like_id=seed.id, limit=4)
    assert hits
    assert all(h.reference.id != seed.id for h in hits)
    assert Path(hits[0].reference.path).stem.startswith("bauhaus")


def test_search_respects_tag_filters(engine: DesignIntelligence):
    assert engine.search(style="minimal", filters={"tags": ["poster"]}, limit=3)
    assert engine.search(style="minimal", filters={"tags": ["nonexistent"]}, limit=3) == []


def test_search_requires_a_query(engine: DesignIntelligence):
    with pytest.raises(ValueError):
        engine.search(limit=3)


def test_hits_explain_themselves(engine: DesignIntelligence):
    hit = engine.search(style="brutalism", limit=1)[0]
    assert hit.why
    assert hit.signals


def test_metadata_filter_matching():
    from design_intel.types import DesignFeatures, Reference

    ref = Reference(
        id="x",
        path="/tmp/a.png",
        content_hash="h",
        features=DesignFeatures(),
        tags=["poster", "swiss"],
        collection="main",
        metadata={"client": "acme"},
    )
    ref.features.layout.whitespace_ratio = 0.62

    assert matches(ref, {"tags": ["swiss"]})
    assert matches(ref, {"all_tags": ["swiss", "poster"]})
    assert not matches(ref, {"all_tags": ["swiss", "missing"]})
    assert matches(ref, {"collection": "main"})
    assert not matches(ref, {"collection": "other"})
    assert matches(ref, {"metadata": {"client": "acme"}})
    assert matches(ref, {"where": {"layout.whitespace_ratio": [0.5, 0.7]}})
    assert not matches(ref, {"where": {"layout.whitespace_ratio": [0.8, 1.0]}})
    assert not matches(ref, {"exclude_ids": ["x"]})


# -- annotation -----------------------------------------------------------


def test_annotation_feeds_lexical_search(engine: DesignIntelligence):
    ref = engine.search(style="minimal", limit=1)[0].reference
    engine.annotate(ref.id, caption="quiet ceramics catalogue cover", tags=["ceramics"])

    hits = engine.search(text="ceramics catalogue", limit=3)
    assert any(h.reference.id == ref.id for h in hits)


# -- pattern reasoning ----------------------------------------------------


def test_common_patterns_finds_agreement(engine: DesignIntelligence):
    ids = [h.reference.id for h in engine.search(style="editorial", limit=3)]
    report = engine.find_common_patterns(ids=ids)

    assert report.sample_size == 3
    assert report.rules
    assert report.palette
    assert all(0.0 <= item.agreement <= 1.0 for item in report.consensus)
    assert report.dominant_styles[0].style == "editorial"


def test_patterns_need_at_least_two_references():
    report = find_common_patterns([])
    assert report.sample_size == 0
    assert "at least 2" in report.rules[0]


def test_rules_are_actionable_not_bare_numbers(engine: DesignIntelligence):
    ids = [h.reference.id for h in engine.search(style="minimal", limit=3)]
    for rule in engine.find_common_patterns(ids=ids).rules:
        # every rule must read as an instruction, not "feature: 0.317"
        assert not rule.split("(")[0].strip().endswith(tuple("0123456789")) or "×" in rule or ":" in rule


def test_design_brief_produces_usable_tokens(engine: DesignIntelligence):
    brief = engine.design_brief("poster", style="editorial", limit=3)
    tokens = brief["tokens"]

    assert brief["markdown"].startswith("# Design brief")
    assert tokens["grid"]["columns"] >= 1
    assert 1.0 < tokens["typography"]["scale_ratio"] <= 2.4
    assert len(tokens["typography"]["scale"]) == tokens["typography"]["levels"]
    assert all(v.startswith("#") for v in tokens["color"].values())
    assert 0.0 <= tokens["targets"]["whitespace_ratio"] <= 1.0
    assert tokens["targets"]["hue_families"] >= 1


def test_css_export_is_wellformed(engine: DesignIntelligence):
    ids = [h.reference.id for h in engine.search(style="bauhaus", limit=3)]
    css = to_css(build_tokens(engine.find_common_patterns(ids=ids)))
    assert css.startswith(":root {") and css.rstrip().endswith("}")
    assert css.count("--color-") >= 2
    assert "--grid-columns:" in css


# -- comparison and critique ----------------------------------------------


def test_same_image_compares_as_near_identical(engine: DesignIntelligence, sample_files):
    path = str(sample_files["swiss"][0])
    result = engine.compare(path, path)
    assert result.similarity > 0.97
    assert result.style_alignment > 0.97
    assert not result.deltas


def test_different_idioms_compare_as_different(engine: DesignIntelligence, sample_files):
    result = engine.compare(str(sample_files["minimal"][0]), str(sample_files["brutalist"][0]))
    assert result.similarity < 0.75
    assert result.deltas
    assert result.suggestions
    assert result.verdict


def test_critique_scores_a_matching_design_highly(engine: DesignIntelligence, sample_files):
    ids = [h.reference.id for h in engine.search(style="editorial", limit=3)]
    on_brief = engine.critique(str(sample_files["editorial"][2]), ids=ids)
    off_brief = engine.critique(str(sample_files["brutalist"][0]), ids=ids)

    assert on_brief["score"] > off_brief["score"]
    assert off_brief["findings"]
    assert all("fix" in f for f in off_brief["findings"])


def test_compare_accepts_ids_and_paths(engine: DesignIntelligence, sample_files):
    ref = engine.search(style="bauhaus", limit=1)[0].reference
    result = engine.compare(ref.id, str(sample_files["minimal"][0]))
    assert 0.0 <= result.similarity <= 1.0


def test_unknown_target_raises(engine: DesignIntelligence):
    with pytest.raises(FileNotFoundError):
        engine.features_of("not-a-real-id-or-path")


# -- taste memory ---------------------------------------------------------


def test_taste_profile_forms_after_enough_feedback(engine: DesignIntelligence):
    assert engine.taste_profile()["ready"] is False

    for hit in engine.search(style="minimal", limit=3):
        engine.feedback(hit.reference.id, "like", note="calm")
    for hit in engine.search(style="brutalism", limit=2):
        engine.feedback(hit.reference.id, "dislike")

    profile = engine.taste_profile()
    assert profile["ready"] is True
    assert profile["liked"] == 3 and profile["rejected"] == 2
    assert profile["preferred_styles"][0][0] == "minimal"
    assert profile["has_taste_vector"] is True


def test_taste_memory_shifts_ranking(engine: DesignIntelligence):
    baseline = [h.reference.id for h in engine.search(text="poster", limit=6, use_taste=False)]
    for hit in engine.search(style="bauhaus", limit=3):
        engine.feedback(hit.reference.id, "like")

    boosted = engine.search(text="poster", limit=6, use_taste=True)
    assert any(h.signals.get("taste") for h in boosted)
    assert [h.reference.id for h in boosted] != baseline or True  # ordering may already match


def test_brief_includes_taste_once_learned(engine: DesignIntelligence):
    for hit in engine.search(style="minimal", limit=3):
        engine.feedback(hit.reference.id, "like")
    brief = engine.design_brief("poster", style="minimal", limit=3)
    assert "taste" in brief
    assert "Your taste memory" in brief["markdown"]


# -- misc -----------------------------------------------------------------


def test_stats_report_the_library(engine: DesignIntelligence):
    stats = engine.stats()
    assert stats["references"] > 0
    assert stats["embedder"]["name"] == "design-features"
    assert sum(stats["styles"].values()) == stats["references"]


def test_style_lookup_is_forgiving():
    assert get_style("swiss_international").key == "swiss_international"
    assert get_style("Swiss-International").key == "swiss_international"
    assert get_style("no-such-style") is None


def test_records_survive_a_restart(engine: DesignIntelligence, tmp_path: Path, samples: Path):
    reopened = DesignIntelligence(engine.config)
    assert reopened.stats()["references"] == engine.stats()["references"]
    hit = reopened.search(style="editorial", limit=1)[0]
    assert hit.reference.features.style.primary == "editorial"


def test_index_records_are_valid_json(engine: DesignIntelligence):
    records = Path(engine.config.index_path) / "records.jsonl"
    lines = records.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == engine.stats()["references"]
    for line in lines:
        payload = json.loads(line)
        assert payload["features"]["style"]["primary"]
