"""Mixxx's library, read from its database and never written.

The database sits in Mixxx's sandbox container, which macOS protects: whatever runs the
server needs Full Disk Access to read it.
"""
from __future__ import annotations

import os
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = (Path.home() / "Library/Containers/org.mixxx.mixxx/Data/Library/Application Support"
              / "Mixxx/mixxxdb.sqlite")

BEATS_PER_BAR = 4


@dataclass(frozen=True)
class Grid:
    """A constant-tempo beatgrid. Mixxx takes its first beat as the first downbeat, so
    bar 0 starts there and bars count in fours from it, before it as well as after."""
    bpm: float
    first_beat: float  # seconds

    def bar_time(self, bar: float) -> float:
        return self.first_beat + bar * BEATS_PER_BAR * 60 / self.bpm

    def bar_at(self, seconds: float) -> float:
        return (seconds - self.first_beat) * self.bpm / (BEATS_PER_BAR * 60)


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


class LibraryError(Exception):
    pass


class Library:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("RHINESTONE_MIXXX_DB", DEFAULT_DB))

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
            "l.beats_version, l.beats, l.samplerate "
            "FROM Playlists p JOIN PlaylistTracks pt ON pt.playlist_id = p.id "
            "JOIN library l ON l.id = pt.track_id JOIN track_locations t ON t.id = l.location "
            "WHERE p.name = ? AND p.hidden = 0 ORDER BY pt.position", (name,))
        if not rows and not self._query("SELECT 1 FROM Playlists WHERE name = ? AND hidden = 0",
                                        (name,)):
            raise LibraryError(f"Mixxx has no playlist named {name!r}")
        return [Track(id=r[0], location=r[1], title=r[2] or "", artist=r[3] or "",
                      duration=r[4] or 0.0, bpm=r[5] or 0.0, missing=bool(r[6]),
                      grid=decode_grid(r[7], r[8], r[9])) for r in rows]


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
