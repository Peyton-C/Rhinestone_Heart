"""The Mixxx adapter: the contract carried out through the bridge and Mixxx's library.

A controller script cannot hand Mixxx a path, so loading works through the library's
selection. Every song lives on a fixed row of one crate playlist, which Mixxx shows
sorted by # ascending. The script can only move the selection by a number of rows, and
the move wraps at both ends, so this side keeps track of which row is selected and
moves by the difference. Which row that is gets worked out by loading the selection
into the preview deck, which no one hears, and matching its length against the crate.
"""
from __future__ import annotations

import math

from ..adapter import (CONTROLS, SET_CONTROLS, Adapter, AdapterError, Capabilities, Lane,
                       Timeline)
from .bridge import BridgeError
from .library import Library, Track

PREVIEW = "[PreviewDeck1]"

# Mixxx's sort column id for a playlist's # column (TrackModel::SortColumnId::Position)
SORT_POSITION = 23

# A stem file takes a few seconds to decode before it counts as loaded
LOAD_SECONDS = 10

# How closely a loaded track must match the crate's row. Mixxx reports the same length
# for a file as its library holds; versions of one song, which can share a length to
# the second, still differ in BPM
DURATION_TOLERANCE = 0.1
BPM_TOLERANCE = 0.01

# Previews taken to work out the selected row before giving up. Each step down rules
# out rows whose neighbours do not match, so only a crate with a run of identical
# tracks needs more than two
ANCHOR_TRIES = 4

# Mixxx's EQ knobs go up to four times unity gain, about +12 dB
EQ_MAX_GAIN = 4.0

# Below this an EQ band is as good as killed, so it is set to a gain of 0
EQ_KILL_DB = -60.0

# The script ramps in straight lines of Mixxx's own values. An EQ is ramped in dB, which
# is a curve in gain, so each segment of an EQ ramp gets this many straight pieces
CURVE_PIECES = 16

EQ_KEYS = {"eq_low": "parameter1", "eq_mid": "parameter2", "eq_high": "parameter3"}


def _eq_gain(db: float) -> float:
    if db <= EQ_KILL_DB:
        return 0.0
    return min(10 ** (db / 20), EQ_MAX_GAIN)


def control(deck: int, name: str):
    """Mixxx's group and key for a neutral control on a deck, and the function from
    the neutral value to Mixxx's."""
    if name == "volume":
        return f"[Channel{deck}]", "volume", float
    if name in EQ_KEYS:
        return f"[EqualizerRack1_[Channel{deck}]_Effect1]", EQ_KEYS[name], _eq_gain
    if name == "filter":
        return f"[QuickEffectRack1_[Channel{deck}]]", "super1", lambda v: 0.5 + v / 2
    if name.startswith("stem") and name[4:].isdigit():
        return f"[Channel{deck}_Stem{name[4:]}]", "volume", float
    if name == "play":
        return f"[Channel{deck}]", "play", lambda v: 1.0 if v else 0.0
    if name == "sync":
        return f"[Channel{deck}]", "sync_enabled", lambda v: 1.0 if v else 0.0
    raise AdapterError(f"no control {name!r}, the controls are {', '.join(CONTROLS)}")


class MixxxAdapter(Adapter):
    def __init__(self, bridge, library: Library, playlist: str):
        self.bridge = bridge
        self.library = library
        self.playlist = playlist
        self._rows: list[Track] = []
        self._current: int | None = None  # the selected row, when known
        self._loaded: dict[int, tuple[Track, float]] = {}  # deck: track, length Mixxx gave
        self._decks: int | None = None

    def _call(self, op: str, **args) -> dict:
        try:
            return self.bridge.call(op, **args)
        except BridgeError as e:
            raise AdapterError(str(e)) from e

    def _check_restart(self) -> None:
        if self.bridge.restarted.is_set():
            self.bridge.restarted.clear()
            self._current = None
            self._loaded.clear()
            self._decks = None

    def capabilities(self) -> Capabilities:
        self._check_restart()
        if self._decks is None:
            self._decks = int(self._call("hello")["decks"])
        return Capabilities(decks=self._decks, stem_slots=4,
                            eq_bands=("low", "mid", "high"), eq_kill=True,
                            eq_max_db=round(20 * math.log10(EQ_MAX_GAIN), 1),
                            controls=tuple(CONTROLS))

    # ------------------------------------------------------------------ loading

    def _crate(self) -> list[Track]:
        rows = self.library.playlist(self.playlist)
        if len(rows) != len(self._rows) and self._rows:
            # Rows were added while Mixxx ran, and its open view shows them only once
            # it queries again. The selection stays on its row across the refresh
            self._call("refresh")
        self._rows = rows
        return rows

    def load(self, deck: int, file: str) -> None:
        self._check_restart()
        rows = self._crate()
        target = next((i for i, t in enumerate(rows) if t.location == file), None)
        if target is None:
            raise AdapterError(f"{file} is not in the {self.playlist} playlist")
        track = rows[target]
        if track.missing:
            raise AdapterError(f"Mixxx cannot find {file}")
        library = self._call("state")["library"]
        if library["sort_column"] != SORT_POSITION or library["sort_order"] != 0:
            # Rows are only where the crate puts them in that order. The selection
            # stays on its track across the sort but lands on another row, so the row
            # is worked out again
            library = self._call("sort", column=SORT_POSITION)
            self._current = None
            if library["sort_column"] != SORT_POSITION or library["sort_order"] != 0:
                raise AdapterError(f"sort the {self.playlist} playlist by # ascending in Mixxx")
        group = f"[Channel{deck}]"
        for attempt in range(2):
            if self._current is None:
                self._anchor(rows)
            n = len(rows)
            delta = (target - self._current) % n
            if delta > n // 2:
                delta -= n
            self._call("move", delta=delta)
            self._current = target
            got = self._call("load", group=group, timeout=LOAD_SECONDS + 1,
                             timeout_ms=LOAD_SECONDS * 1000)
            if got["loaded"] and _matches(track, got):
                self._loaded[deck] = (track, got["duration"])
                return
            self._loaded.pop(deck, None)
            if not got["loaded"]:
                raise AdapterError(f"Mixxx could not load {file}")
            # Something else was selected than this side thought, as when someone
            # clicks in the library: work out the row again and try once more
            self._current = None
        raise AdapterError(f"Mixxx loaded something other than {file} twice. Check the "
                           f"{self.playlist} playlist is the one open in Mixxx")

    def _anchor(self, rows: list[Track]) -> None:
        """Works out which row is selected. Each try previews the selection and steps
        down a row, until one starting row explains everything seen. With nothing
        selected the preview loads nothing and the first step lands on row 0, which a
        missing file's row would explain too, so that is weighed as one more start."""
        n = len(rows)
        seen: list[dict] = []
        for _ in range(ANCHOR_TRIES):
            seen.append(self._call("load", group=PREVIEW, timeout=LOAD_SECONDS + 1,
                                   timeout_ms=LOAD_SECONDS * 1000))
            starts = [i for i in range(n)
                      if all(_matches(rows[(i + k) % n], s) for k, s in enumerate(seen))]
            # None selected: the first preview is empty and each step after reaches row k - 1
            if not seen[0]["loaded"] and all(_matches(rows[(k - 1) % n], s)
                                             for k, s in enumerate(seen) if k):
                starts.append(None)
            if len(starts) == 1:
                start, moves = starts[0], len(seen) - 1
                if start is not None:
                    self._current = (start + moves) % n
                elif moves:
                    self._current = moves - 1
                else:
                    self._call("move", delta=1)
                    self._current = 0
                return
            if not starts:
                break
            self._call("move", delta=1)
        raise AdapterError(f"cannot tell which row of the {self.playlist} playlist is selected. "
                           "Check it is the playlist open in Mixxx")

    # --------------------------------------------------------------- timelines

    def bar_to_position(self, file: str, bar: float) -> float:
        track = next((t for t in self._rows or self._crate() if t.location == file), None)
        if track is None:
            raise AdapterError(f"{file} is not in the {self.playlist} playlist")
        if track.grid is None:
            raise AdapterError(f"Mixxx has no beatgrid for {file}: analyze it in Mixxx")
        return track.grid.bar_time(bar)

    def run(self, timeline: Timeline) -> None:
        self._check_restart()
        decks = self.capabilities().decks
        lanes, clocks = [], {}
        for i, lane in enumerate(timeline.lanes):
            where = f"lane {i} ({lane.control} on deck {lane.deck})"
            for d in (lane.deck, lane.clock):
                if not 1 <= d <= decks:
                    raise AdapterError(f"{where}: Mixxx has decks 1 to {decks}, not {d}")
            if lane.clock not in self._loaded:
                raise AdapterError(f"{where}: deck {lane.clock} has no track loaded by the server "
                                   "to count bars on")
            track, length = self._loaded[lane.clock]
            if track.grid is None:
                raise AdapterError(f"{where}: Mixxx has no beatgrid for {track.location}")
            if not lane.points:
                raise AdapterError(f"{where}: no points")
            group, key, convert = control(lane.deck, lane.control)
            points = _points(lane, convert, track.grid.bar_time)
            clocks[f"[Channel{lane.clock}]"] = length
            lanes.append({"group": group, "key": key, "clock": f"[Channel{lane.clock}]",
                          "kind": "set" if lane.control in SET_CONTROLS else "ramp",
                          "points": points})
        self._call("run", timeline={"id": timeline.id, "clocks": clocks, "lanes": lanes})

    def replace(self, timeline: Timeline, from_bar: float) -> None:
        lanes = []
        for lane in timeline.lanes:
            points = _from_bar(lane.points, from_bar, lane.control in SET_CONTROLS)
            if points:
                lanes.append(Lane(lane.deck, lane.control, lane.clock, points))
        self.run(Timeline(timeline.id, lanes))

    def state(self) -> dict:
        self._check_restart()
        s = self._call("state")
        return {"session": s["session"], "decks": s["decks"], "timeline": s["timeline"],
                "loaded": {d: t.location for d, (t, _) in self._loaded.items()}}


def _matches(track: Track, seen: dict) -> bool:
    """Whether a load report fits a crate row. A load that fails fits a missing file."""
    if not seen.get("loaded"):
        return track.missing
    if abs(seen["duration"] - track.duration) > DURATION_TOLERANCE:
        return False
    return not track.bpm or abs(seen["file_bpm"] - track.bpm) <= BPM_TOLERANCE


def _points(lane: Lane, convert, bar_time) -> list[list[float]]:
    pts = sorted(lane.points, key=lambda p: p[0])
    if lane.control not in EQ_KEYS:
        return [[bar_time(b), convert(v)] for b, v in pts]
    out = [[bar_time(pts[0][0]), convert(pts[0][1])]]
    for (b0, v0), (b1, v1) in zip(pts, pts[1:]):
        steps = CURVE_PIECES if b1 > b0 and v0 != v1 else 1
        for k in range(1, steps + 1):
            f = k / steps
            v = v1 if k == steps else _between(v0, v1, f)
            out.append([bar_time(b0 + (b1 - b0) * f), convert(v)])
    return out


def _between(v0: float, v1: float, f: float) -> float:
    # A ramp from or to a kill runs to the kill threshold, not through infinity
    v0, v1 = max(v0, EQ_KILL_DB), max(v1, EQ_KILL_DB)
    return v0 + (v1 - v0) * f


def _from_bar(points, from_bar: float, is_set: bool) -> list[tuple[float, float]]:
    pts = sorted(points, key=lambda p: p[0])
    later = [p for p in pts if p[0] >= from_bar]
    if is_set:
        return later
    before = [p for p in pts if p[0] < from_bar]
    if before and later and later[0][0] > from_bar:
        (b0, v0), (b1, v1) = before[-1], later[0]
        f = (from_bar - b0) / (b1 - b0)
        later.insert(0, (from_bar, _between(v0, v1, f)))
    return later
