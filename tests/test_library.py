import sqlite3

import pytest

from rhinestone.mixxx.library import Grid, Library, LibraryError, decode_grid

# Two grids as Mixxx 2.6 stored them, for pink diamond (RF AT) and Rhinestone Heart (RF)
PINK_DIAMOND = bytes.fromhex("0A09090000000000A06140120308C674")
RHINESTONE_HEART = bytes.fromhex("0A09090000000000C05B40120308E747")


def test_decodes_mixxx_beatgrids():
    g = decode_grid("BeatGrid-2.0", PINK_DIAMOND, 44100)
    assert g.bpm == 141.0
    assert g.first_beat == pytest.approx(14918 / 44100)
    g = decode_grid("BeatGrid-2.0", RHINESTONE_HEART, 44100)
    assert g.bpm == 111.0
    assert g.first_beat == pytest.approx(9191 / 44100)


def test_bars_count_in_fours_from_the_first_beat():
    g = Grid(bpm=120.0, first_beat=0.5)
    assert g.bar_time(0) == 0.5
    assert g.bar_time(16) == pytest.approx(0.5 + 32)
    assert g.bar_time(-1) == pytest.approx(-1.5)
    assert g.bar_at(g.bar_time(37.25)) == pytest.approx(37.25)


def test_bar_0_starts_on_the_downbeat_when_the_grid_starts_before_it():
    # Take It Off: three count-in beats before the music, at 125 BPM
    g = Grid(bpm=125.0, first_beat=0.026, downbeat=3)
    assert g.bar_time(0) == pytest.approx(0.026 + 3 * 0.48)
    assert g.bar_time(1) == pytest.approx(0.026 + 7 * 0.48)
    assert g.bar_at(g.bar_time(5.5)) == pytest.approx(5.5)


def test_the_downbeat_is_the_beat_nearest_a_time_counted_in_fours():
    g = Grid(bpm=125.0, first_beat=0.026)
    assert g.with_downbeat_at(1.4655).downbeat == 3
    assert g.with_downbeat_at(0.03).downbeat == 0
    assert g.with_downbeat_at(0.026 + 9 * 0.48 + 0.1).downbeat == 1
    # Sound before the grid's first beat counts back from it
    assert g.with_downbeat_at(0.026 - 0.48).downbeat == 3


def test_no_grid_without_beats_or_for_beatmaps():
    assert decode_grid(None, None, 44100) is None
    assert decode_grid("BeatGrid-2.0", PINK_DIAMOND, 0) is None
    assert decode_grid("BeatMap-1.0", PINK_DIAMOND, 44100) is None


def _db(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE Playlists (id INTEGER PRIMARY KEY, name TEXT, hidden INTEGER);
        CREATE TABLE PlaylistTracks (id INTEGER PRIMARY KEY, playlist_id INTEGER,
            track_id INTEGER, position INTEGER, pl_datetime_added);
        CREATE TABLE track_locations (id INTEGER PRIMARY KEY, location TEXT, fs_deleted INTEGER);
        CREATE TABLE library (id INTEGER PRIMARY KEY, title TEXT, artist TEXT, location INTEGER,
            duration FLOAT, bpm FLOAT, beats BLOB, beats_version TEXT, samplerate INTEGER);
        CREATE TABLE cues (id INTEGER PRIMARY KEY, track_id INTEGER, type INTEGER,
            position INTEGER, length INTEGER, hotcue INTEGER, label TEXT, color INTEGER);
        INSERT INTO Playlists VALUES (1, 'RH', 0), (2, '2026-10-26', 2);
        INSERT INTO track_locations VALUES (10, '/m/a.stem.mp4', 0), (11, '/m/b.stem.mp4', 1);
        INSERT INTO library VALUES (100, 'pink diamond', 'Charli xcx', 10, 124.0, 141.0,
            NULL, NULL, 44100);
        INSERT INTO library VALUES (101, 'Rhinestone Heart', 'Slayyyter', 11, 149.9, 111.0,
            NULL, NULL, 44100);
        INSERT INTO PlaylistTracks VALUES (1, 1, 101, 2, NULL), (2, 1, 100, 1, NULL);
    """)
    db.execute("UPDATE library SET beats = ?, beats_version = 'BeatGrid-2.0' WHERE id = 100",
               (PINK_DIAMOND,))
    # Sound starting one beat after the grid's first beat, with a main cue elsewhere
    # that is not read. Positions are engine samples, two to a frame
    beat_frames = 44100 * 60 / 141
    db.execute("INSERT INTO cues (track_id, type, position) VALUES (100, 8, ?), (100, 2, 0)",
               (round((14918 + beat_frames) * 2),))
    db.commit()
    db.close()


def test_reads_a_playlist_in_its_own_order(tmp_path):
    path = tmp_path / "mixxxdb.sqlite"
    _db(path)
    rows = Library(path).playlist("RH")
    assert [t.title for t in rows] == ["pink diamond", "Rhinestone Heart"]
    assert [t.missing for t in rows] == [False, True]
    assert rows[0].grid.bpm == 141.0
    assert rows[0].grid.downbeat == 1
    assert rows[1].grid is None


def test_unknown_and_history_playlists_are_refused(tmp_path):
    path = tmp_path / "mixxxdb.sqlite"
    _db(path)
    with pytest.raises(LibraryError, match="no playlist"):
        Library(path).playlist("Nope")
    with pytest.raises(LibraryError, match="no playlist"):
        Library(path).playlist("2026-10-26")


def test_an_unreadable_database_says_why(tmp_path):
    with pytest.raises(LibraryError, match="Full Disk Access"):
        Library(tmp_path / "missing.sqlite").playlist("RH")
