"""20 tests for the rule-based intent parser."""

import pytest

from autocut.intent import IntentParser, get_parser

PRESETS = ["paris_vlog", "travel"]


@pytest.fixture(scope="module")
def p():
    return get_parser("rules")


def parse(p, text):
    return p.parse(text, available_presets=PRESETS)


def test_01_canonical_example(p):
    s = parse(p, "funny 60 second tiktok with captions")
    assert s.mood == "funny" and s.target_duration == 60
    assert s.platform == "tiktok" and s.aspect_ratio == "9:16"
    assert s.captions is True and s.warnings == []


def test_02_minutes_and_landscape(p):
    s = parse(p, "calm 2 minute landscape edit")
    assert s.mood == "calm" and s.target_duration == 120 and s.aspect_ratio == "16:9"


def test_03_clock_format(p):
    assert parse(p, "hype edit 1:30 long").target_duration == 90


def test_04_word_numbers(p):
    assert parse(p, "thirty second emotional clip").target_duration == 30
    assert parse(p, "half a minute of funny bits").target_duration == 30


def test_05_minute_and_a_half(p):
    assert parse(p, "a minute and a half recap").target_duration == 90


def test_06_under_qualifier_stays_below_limit(p):
    s = parse(p, "under 60 seconds")
    assert s.target_duration * (1 + s.duration_tolerance) <= 60


def test_07_platform_default_length_and_aspect(p):
    s = parse(p, "make me a reel")
    assert s.platform == "reels" and s.aspect_ratio == "9:16" and s.target_duration == 30


def test_08_explicit_duration_beats_platform_default(p):
    assert parse(p, "45 sec youtube shorts").target_duration == 45


def test_09_aspect_overrides_platform(p):
    s = parse(p, "square instagram post")
    assert s.aspect_ratio == "1:1" and s.platform == "instagram"


def test_10_no_captions(p):
    assert parse(p, "hype tiktok without subtitles").captions is False
    assert parse(p, "funny clip, no captions").captions is False


def test_11_exclude_keywords(p):
    s = parse(p, "funny video without the airport and no swearing")
    assert "airport" in s.exclude_keywords and "swearing" in s.exclude_keywords


def test_12_include_keywords(p):
    s = parse(p, "informative recap about the dumplings")
    assert s.mood == "informative" and s.include_keywords == ["dumplings"]


def test_13_visual_query(p):
    s = parse(p, "calm edit showing food close ups")
    assert s.visual_query == "food close ups"


def test_14_style_preset_exact(p):
    assert parse(p, "funny edit in the style of paris_vlog").style_preset == "paris_vlog"


def test_15_style_preset_fuzzy_and_unknown(p):
    assert parse(p, "use the paris preset").style_preset == "paris_vlog"
    s = parse(p, "edit in the style of wedding")
    assert s.style_preset is None and any("wedding" in w for w in s.warnings)


def test_16_versions(p):
    assert parse(p, "3 versions of a funny tiktok").versions == 3
    assert parse(p, "give me two variations").versions == 2


def test_17_unknown_words_become_warnings(p):
    s = parse(p, "funny banana spaceship tiktok")
    assert s.mood == "funny"
    assert s.warnings and "banana" in s.warnings[0] and "spaceship" in s.warnings[0]


def test_18_mood_majority_and_synonyms(p):
    assert parse(p, "hilarious lol bloopers but kind of chill").mood == "funny"
    assert parse(p, "epic highlights").mood == "hype"
    assert parse(p, "heartfelt wholesome moments").mood == "emotional"


def test_19_empty_and_music(p):
    s = parse(p, "")
    assert s.mood == "auto" and s.target_duration is None and s.warnings == []
    assert parse(p, "chill vlog with no music").music is False


def test_20_interface_and_summary(p):
    assert isinstance(p, IntentParser)
    s = parse(p, "funny 60 second vertical video with captions, 2 versions")
    assert s.versions == 2 and s.aspect_ratio == "9:16"
    summary = s.summary()
    assert "funny" in summary and "60 s" in summary and "captions" in summary
    assert s.model_dump()["source_text"].startswith("funny")
