"""Mixxx's library, read from its database and never written.

The database sits in Mixxx's sandbox container, which macOS protects: whatever runs the
server needs Full Disk Access to read it.
"""
from __future__ import annotations

import os
import sqlite3
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .. import metadata as shared

DEFAULT_DB = (Path.home() / "Library/Containers/org.mixxx.mixxx/Data/Library/Application Support"
              / "Mixxx/mixxxdb.sqlite")

BEATS_PER_BAR = 4

# Mixxx's cue types (CueType in cueinfo.h): where its analyzer found the sound start
FIRST_SOUND_CUE = 8

# Cue positions are in Mixxx's engine samples, two to a frame whatever the file holds
ENGINE_CHANNELS = 2

# A time counts as on one of Mixxx's beats within this share of a beat, 29 ms at 125 BPM
ON_BEAT = 0.15

# The section starts on a beat must number this many, and this share of them fall on
# one beat of the bar, for that beat to be the downbeat
MIN_SECTIONS = 3
SECTIONS_AGREE = 0.6

# Mixxx's beat and the analysis's further apart than this, after allowing for one
# counting twice the other's tempo, are two grids that drift apart over the song
TEMPO_TOLERANCE = 0.003


@dataclass(frozen=True)
class Grid:
    """A constant-tempo beatgrid, with bars counted in fours from a downbeat, before it
    as well as after. Mixxx's grid knows beats but not bars, and its first beat is
    wherever its analyzer started, which need not be a downbeat: Flashing Lights' grid
    starts three beats before one. So bar 0 starts `downbeat` beats after the grid's
    first beat; place_downbeat works out which."""
    bpm: float
    first_beat: float  # seconds
    downbeat: int = 0  # beats, 0 to 3

    @property
    def beat(self) -> float:
        return 60 / self.bpm

    def bar_time(self, bar: float) -> float:
        return self.first_beat + (self.downbeat + bar * BEATS_PER_BAR) * self.beat

    def bar_at(self, seconds: float) -> float:
        return ((seconds - self.first_beat) / self.beat - self.downbeat) / BEATS_PER_BAR

    def with_downbeat_at(self, seconds: float) -> "Grid":
        """This grid with bar 0 on the beat nearest `seconds`."""
        return Grid(self.bpm, self.first_beat, round(self.beats_at(seconds)) % BEATS_PER_BAR)

    def beats_at(self, seconds: float) -> float:
        """Beats from the grid's first beat to `seconds`."""
        return (seconds - self.first_beat) / self.beat


@dataclass(frozen=True)
class Track:
    id: int
    location: str
    title: str
    artist: str
    duration: float
    bpm: float
    missing: bool
    grid: Grid | None
    # What set the grid's downbeat: sections, drums, first sound, or grid for none
    downbeat_from: str = "grid"
    # What is wrong with the grid against the shared analysis, for a person to fix
    warnings: tuple[str, ...] = ()


class LibraryError(Exception):
    pass


def place_downbeat(grid: Grid, song: dict | None,
                   first_sound: float | None) -> tuple[Grid, str, tuple[str, ...]]:
    """`grid` with its downbeat set, what set it, and any warnings.

    Section starts from the shared analysis are trusted first: they fall on one beat of
    the bar, all 16 of Take It Off's and all 20 of Flashing Lights'. Drums can come in
    on a fill a beat early, as Rhinestone Heart's do, and the sound can start on a
    pickup, as Take It Off's does a beat before its first full bar, so those come after,
    in that order."""
    warnings: list[str] = []
    if song:
        warnings += _tempo_warnings(grid, song)
        on = [round(b) % BEATS_PER_BAR for b in map(grid.beats_at, shared.section_starts(song))
              if abs(b - round(b)) <= ON_BEAT]
        if len(on) >= MIN_SECTIONS:
            beat, n = Counter(on).most_common(1)[0]
            if n / len(on) >= SECTIONS_AGREE:
                return Grid(grid.bpm, grid.first_beat, beat), "sections", tuple(warnings)
            warnings.append(f"its section starts fall on different beats of the bar "
                            f"({', '.join(f'{k}: {v}' for k, v in sorted(Counter(on).items()))})")
        drums = shared.drum_entry(song)
        if drums is not None and abs(grid.beats_at(drums) - round(grid.beats_at(drums))) <= ON_BEAT:
            return grid.with_downbeat_at(drums), "drums", tuple(warnings)
    else:
        warnings.append("not in the shared analysis")
    if first_sound is not None:
        return grid.with_downbeat_at(first_sound), "first sound", tuple(warnings)
    return grid, "grid", tuple(warnings)


def _tempo_warnings(grid: Grid, song: dict) -> list[str]:
    beats = song.get("beats") or []
    if len(beats) < 2:
        return []
    # Across the whole song, since the export rounds each beat to the millisecond
    theirs = (beats[-1] - beats[0]) / (len(beats) - 1)
    ratio = theirs / grid.beat
    if abs(ratio - 1) <= TEMPO_TOLERANCE:
        return []
    bpm = 60 / theirs
    if abs(ratio - 2) <= 2 * TEMPO_TOLERANCE:
        return [f"Mixxx's grid counts twice the tempo the analysis found ({grid.bpm:g} against "
                f"{bpm:.2f}), so its bars are half as long"]
    if abs(ratio - 0.5) <= TEMPO_TOLERANCE / 2:
        return [f"Mixxx's grid counts half the tempo the analysis found ({grid.bpm:g} against "
                f"{bpm:.2f}), so its bars are twice as long"]
    return [f"Mixxx's grid is {grid.bpm:g} BPM where the analysis found {bpm:.2f}, so the two "
            "drift apart over the song"]


class Library:
    def __init__(self, path: str | Path | None = None, metadata: dict[str, dict] | None = None):
        """`metadata` is the shared analysis by path, from rhinestone.metadata.load."""
        self.path = Path(path or os.environ.get("RHINESTONE_MIXXX_DB", DEFAULT_DB))
        self.metadata = metadata

    def _query(self, sql: str, args=()) -> list[tuple]:
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5) as db:
                return db.execute(sql, args).fetchall()
        except sqlite3.OperationalError as e:
            raise LibraryError(f"cannot read Mixxx's library at {self.path}: {e}. Reading it "
                               "needs Full Disk Access for the app running the server") from e

    def playlist(self, name: str) -> list[Track]:
        """A playlist's tracks in its own order, which is the order its view shows
        sorted by # ascending. Missing files are kept, since the view shows them too."""
        rows = self._query(
            "SELECT l.id, t.location, l.title, l.artist, l.duration, l.bpm, t.fs_deleted, "
            "l.beats_version, l.beats, l.samplerate, s.position "
            "FROM Playlists p JOIN PlaylistTracks pt ON pt.playlist_id = p.id "
            "JOIN library l ON l.id = pt.track_id JOIN track_locations t ON t.id = l.location "
            "LEFT JOIN (SELECT track_id, MIN(position) AS position FROM cues "
            f"WHERE type = {FIRST_SOUND_CUE} AND position >= 0 GROUP BY track_id) s "
            "ON s.track_id = l.id "
            "WHERE p.name = ? AND p.hidden = 0 ORDER BY pt.position", (name,))
        if not rows and not self._query("SELECT 1 FROM Playlists WHERE name = ? AND hidden = 0",
                                        (name,)):
            raise LibraryError(f"Mixxx has no playlist named {name!r}")
        tracks = []
        for r in rows:
            grid = decode_grid(r[7], r[8], r[9])
            source, warnings = "grid", ()
            if grid:
                song = self.metadata.get(shared.key(r[1])) if self.metadata is not None else None
                first_sound = r[10] / ENGINE_CHANNELS / r[9] if r[10] is not None else None
                grid, source, warnings = place_downbeat(grid, song, first_sound)
                if self.metadata is None:
                    warnings = ()  # no analysis to measure against
            tracks.append(Track(id=r[0], location=r[1], title=r[2] or "", artist=r[3] or "",
                                duration=r[4] or 0.0, bpm=r[5] or 0.0, missing=bool(r[6]),
                                grid=grid, downbeat_from=source, warnings=warnings))
        return tracks


def decode_grid(version: str | None, blob: bytes | None, samplerate: int | None) -> Grid | None:
    """A track's grid from Mixxx's beats column, or None when it has none this reads.
    Every track analyzed so far has a BeatGrid; a BeatMap, with its tempo changes, is
    left for when one turns up."""
    if not blob or not samplerate:
        return None
    if version == "BeatGrid-2.0":
        fields = _protobuf(blob)
        bpm = _protobuf(fields.get(1, b"")).get(1)
        frame = _protobuf(fields.get(2, b"")).get(1, 0)
        if not isinstance(bpm, float) or bpm <= 0:
            return None
        return Grid(bpm=bpm, first_beat=_int32(frame) / samplerate)
    if version == "BeatGrid-1.0" and len(blob) == 16:
        # BeatGridV1Data in beats.cpp: the tempo, then the first beat in frames
        bpm, frame = struct.unpack("<dd", blob)
        return Grid(bpm=bpm, first_beat=frame / samplerate) if bpm > 0 else None
    return None


def _protobuf(data: bytes) -> dict[int, object]:
    """The fields of a protobuf message, the last of each number kept: enough for the
    two small messages in beats.proto. Doubles come back as floats, length-delimited
    fields as bytes and varints as ints."""
    fields: dict[int, object] = {}
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            fields[number], i = _varint(data, i)
        elif wire == 1:
            fields[number] = struct.unpack_from("<d", data, i)[0]
            i += 8
        elif wire == 2:
            size, i = _varint(data, i)
            fields[number] = bytes(data[i:i + size])
            i += size
        elif wire == 5:
            fields[number] = struct.unpack_from("<f", data, i)[0]
            i += 4
        else:
            raise ValueError(f"protobuf wire type {wire} is not read here")
    return fields


def _varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, i


def _int32(value) -> int:
    """A protobuf int32: a negative one is sent as a 64-bit varint."""
    value = int(value)
    return value - (1 << 64) if value >= 1 << 63 else value
