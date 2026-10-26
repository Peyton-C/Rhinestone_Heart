import json
import unicodedata

import pytest

from rhinestone import metadata
from rhinestone.mixxx.library import Grid, place_downbeat

# 125 BPM, a beat every 0.48 s, the grid's first beat at 0.026 s
GRID = Grid(bpm=125.0, first_beat=0.026)


def beat(n, off=0.0):
    return 0.026 + (n + off) * 0.48


def song(sections=(), drums=None, bpm=125.0, beats=64):
    s = {"path": "/m/a.stem.mp4", "bpm": round(bpm),
         "beats": [round(0.026 + i * 60 / bpm, 3) for i in range(beats)],
         "sections": [{"start": 0.0, "detected": False}] +
                     [{"start": t, "detected": True} for t in sections]}
    if drums is not None:
        s["stems"] = {"tracks": [{"code": "drums", "first_entry": drums}]}
    return s


def test_section_starts_set_the_downbeat_over_drums_and_first_sound():
    # Take It Off: sections on beat 0 of the bar, the sound starting on a pickup at beat 3
    g, source, warnings = place_downbeat(GRID, song([beat(4), beat(20), beat(36)], drums=beat(20)),
                                         first_sound=beat(3))
    assert (g.downbeat, source, warnings) == (0, "sections", ())


def test_a_drum_fill_a_beat_early_does_not_move_the_downbeat():
    # Rhinestone Heart: drums enter on beat 31, its sections on bar lines
    g, source, _ = place_downbeat(GRID, song([beat(32), beat(64), beat(96)], drums=beat(31)), None)
    assert (g.downbeat, source) == (0, "sections")


def test_too_few_or_disagreeing_sections_fall_back_to_the_drums():
    g, source, _ = place_downbeat(GRID, song([beat(7)], drums=beat(31)), beat(0))
    assert (g.downbeat, source) == (3, "drums")
    g, source, warnings = place_downbeat(
        GRID, song([beat(1), beat(2), beat(3), beat(4)], drums=beat(31)), beat(0))
    assert (g.downbeat, source) == (3, "drums")
    assert "different beats" in warnings[0]


def test_starts_between_beats_count_for_nothing():
    g, source, _ = place_downbeat(GRID, song([beat(5, 0.4), beat(9, 0.5), beat(13, 0.3)]), beat(2))
    assert (g.downbeat, source) == (2, "first sound")


def test_without_the_analysis_first_sound_then_the_grid():
    g, source, warnings = place_downbeat(GRID, None, beat(3))
    assert (g.downbeat, source, warnings) == (3, "first sound", ("not in the shared analysis",))
    g, source, _ = place_downbeat(GRID, None, None)
    assert (g.downbeat, source) == (0, "grid")


@pytest.mark.parametrize("bpm, words", [(250.0, "half the tempo"), (62.5, "twice the tempo"),
                                        (124.0, "drift apart")])
def test_tempos_that_disagree_are_warned_about(bpm, words):
    _, _, warnings = place_downbeat(GRID, song(bpm=bpm), None)
    assert any(words in w for w in warnings)


def test_a_matching_tempo_is_not_warned_about():
    _, _, warnings = place_downbeat(GRID, song(bpm=125.0, beats=400), None)
    assert warnings == ()


def test_loads_an_export_and_matches_paths_in_either_unicode_form(tmp_path):
    composed = unicodedata.normalize("NFC", "/m/Beyoncé.stem.mp4")
    decomposed = unicodedata.normalize("NFD", composed)
    f = tmp_path / "crate.json"
    f.write_text(json.dumps({"format": "crate-metadata", "schema_version": 1,
                             "songs": [{"path": decomposed, "sections": []}], "skipped": []}))
    songs = metadata.load(f)
    assert metadata.key(composed) in songs


def test_refuses_another_format_or_version(tmp_path):
    f = tmp_path / "crate.json"
    f.write_text(json.dumps({"format": "crate-metadata", "schema_version": 2, "songs": []}))
    with pytest.raises(metadata.MetadataError, match="version"):
        metadata.load(f)
    with pytest.raises(metadata.MetadataError, match="cannot read"):
        metadata.load(tmp_path / "none.json")


def test_no_export_named_is_none(monkeypatch):
    monkeypatch.delenv("RHINESTONE_METADATA", raising=False)
    assert metadata.load(None) is None
