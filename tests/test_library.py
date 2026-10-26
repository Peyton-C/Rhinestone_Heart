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
    db.commit()
    db.close()


def test_reads_a_playlist_in_its_own_order(tmp_path):
    path = tmp_path / "mixxxdb.sqlite"
    _db(path)
    rows = Library(path).playlist("RH")
    assert [t.title for t in rows] == ["pink diamond", "Rhinestone Heart"]
    assert [t.missing for t in rows] == [False, True]
    assert rows[0].grid.bpm == 141.0
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
