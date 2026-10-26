import threading

import pytest

from rhinestone.adapter import AdapterError, Lane, Timeline
from rhinestone.mixxx import adapter as mixxx_adapter
from rhinestone.mixxx.adapter import MixxxAdapter
from rhinestone.mixxx.bridge import BridgeError
from rhinestone.mixxx.library import Grid, Track


def track(i, duration, bpm, missing=False, grid=None):
    return Track(id=i, location=f"/m/{i}.stem.mp4", title=f"song {i}", artist="", duration=duration,
                 bpm=bpm, missing=missing, grid=grid or Grid(bpm=bpm or 120.0, first_beat=0.5))


class FakeMixxx:
    """The script and Mixxx 2.6 as the bridge sees them: a playlist view whose selection
    moves as WTrackTableView::moveSelection moves it, and players that load what is
    selected unless its file is missing."""

    def __init__(self, rows, selected=None):
        self.rows = rows  # what the open view shows
        self.selected = selected
        self.players = {}
        self.playing = set()
        self.sort = (23, 0)
        self.sort_sticks = True
        self.calls = []
        self.restarted = threading.Event()
        self.session = "s1"

    def call(self, op, timeout=None, **args):
        self.calls.append((op, args))
        if op == "hello":
            return {"ok": True, "decks": 4}
        if op == "state":
            return {"ok": True, "session": self.session, "decks": [], "timeline": None,
                    "library": {"sort_column": self.sort[0], "sort_order": self.sort[1]}}
        if op == "move":
            n, delta = len(self.rows), args["delta"]
            while delta:
                step = 1 if delta > 0 else -1
                if self.selected is None:
                    self.selected = 0 if step > 0 else n - 1
                else:
                    self.selected = (self.selected + step) % n
                delta -= step
            return {"ok": True}
        if op == "refresh":
            self.rows = self.refreshed
            return {"ok": True}
        if op == "sort":
            if not self.sort_sticks:
                return {"ok": True, "sort_column": self.sort[0], "sort_order": self.sort[1]}
            # Back to # order, the selection kept on its track
            track = self.rows[self.selected] if self.selected is not None else None
            self.rows = sorted(self.rows, key=lambda t: t.id)
            self.selected = self.rows.index(track) if track else None
            self.sort = (23, 0)
            return {"ok": True, "sort_column": 23, "sort_order": 0}
        if op == "load":
            g = args["group"]
            if g in self.playing:
                raise BridgeError(f"{g} is playing")
            self.players.pop(g, None)
            if self.selected is None or self.rows[self.selected].missing:
                return {"ok": True, "loaded": False, "duration": 0, "file_bpm": 0}
            t = self.rows[self.selected]
            self.players[g] = t
            return {"ok": True, "loaded": True, "duration": t.duration, "file_bpm": t.bpm}
        if op == "run":
            self.timeline = args["timeline"]
            return {"ok": True}
        raise AssertionError(op)


class FakeLibrary:
    def __init__(self, rows):
        self.rows = rows

    def playlist(self, name):
        return list(self.rows)


CRATE = [track(0, 200.0, 126.0), track(1, 157.0, 137.75), track(2, 186.0, 132.0),
         track(3, 194.0, 115.0), track(4, 124.0, 141.0), track(5, 124.0, 141.67),
         track(6, 212.0, 150.0), track(7, 150.0, 111.0)]


def setup(selected=None, rows=CRATE):
    m = FakeMixxx(list(rows), selected)
    return m, MixxxAdapter(m, FakeLibrary(list(rows)), "RH")


@pytest.mark.parametrize("selected", [None, 0, 3, 7])
def test_loads_the_right_row_from_any_selection(selected):
    m, a = setup(selected)
    for deck, row in [(1, 6), (2, 1), (1, 0), (2, 5), (1, 4)]:
        a.load(deck, CRATE[row].location)
        assert m.players[f"[Channel{deck}]"] is CRATE[row]


def test_versions_that_share_a_length_are_told_apart_by_bpm():
    # Rows 4 and 5 are two versions of one song, both 124 s
    m, a = setup(4)
    a.load(1, CRATE[2].location)
    assert m.players["[Channel1]"] is CRATE[2]


def test_identical_neighbours_need_more_steps_to_anchor():
    rows = [track(0, 100.0, 120.0), track(1, 100.0, 120.0), track(2, 90.0, 120.0),
            track(3, 80.0, 120.0)]
    m, a = setup(0, rows)
    a.load(1, rows[3].location)
    assert m.players["[Channel1]"] is rows[3]


def test_a_selection_moved_by_hand_is_found_again():
    m, a = setup(0)
    a.load(1, CRATE[3].location)
    m.selected = 6  # someone clicked in the library
    a.load(2, CRATE[1].location)
    assert m.players["[Channel2]"] is CRATE[1]


def test_a_missing_selected_row_is_not_mistaken_for_no_selection():
    rows = list(CRATE)
    rows[2] = track(2, 186.0, 132.0, missing=True)
    m, a = setup(2, rows)
    a.load(1, rows[5].location)
    assert m.players["[Channel1]"] is rows[5]


def test_missing_files_and_songs_outside_the_crate_are_refused():
    rows = list(CRATE)
    rows[2] = track(2, 186.0, 132.0, missing=True)
    m, a = setup(0, rows)
    with pytest.raises(AdapterError, match="cannot find"):
        a.load(1, rows[2].location)
    with pytest.raises(AdapterError, match="not in the RH playlist"):
        a.load(1, "/m/elsewhere.mp3")


def test_a_view_sorted_otherwise_is_sorted_by_position_and_the_row_found_again():
    m, a = setup(0)
    a.load(1, CRATE[1].location)
    # Someone sorts by title in Mixxx: the selection stays on its track, another row
    m.rows = sorted(CRATE, key=lambda t: -t.duration)
    m.selected = m.rows.index(CRATE[1])
    m.sort = (2, 0)
    a.load(2, CRATE[6].location)
    assert ("sort", {"column": 23}) in m.calls
    assert m.players["[Channel2]"] is CRATE[6]


def test_a_sort_that_does_not_take_is_reported():
    m, a = setup(0)
    m.sort = (2, 0)
    m.sort_sticks = False
    with pytest.raises(AdapterError, match="sort"):
        a.load(1, CRATE[1].location)


def test_a_playing_deck_is_not_loaded():
    m, a = setup(0)
    m.playing.add("[Channel1]")
    with pytest.raises(AdapterError, match="playing"):
        a.load(1, CRATE[1].location)


def test_rows_added_while_running_are_refreshed_in():
    m, a = setup(0)
    a.load(1, CRATE[1].location)
    added = list(CRATE) + [track(8, 118.0, 87.0)]
    a.library.rows = added
    m.refreshed = added  # the open view gets the row once it queries again
    a.load(2, added[8].location)
    assert m.players["[Channel2]"] is added[8]
    assert ("refresh", {}) in m.calls


def test_a_restart_forgets_the_selection():
    m, a = setup(0)
    a.load(1, CRATE[1].location)
    m.selected = None
    m.restarted.set()
    a.load(2, CRATE[3].location)
    assert m.players["[Channel2]"] is CRATE[3]


def test_runs_lanes_in_seconds_on_the_clock_decks_grid():
    m, a = setup(0)
    a.load(1, CRATE[0].location)  # grid: 126 BPM, first beat at 0.5 s
    a.run(Timeline("t1", [
        Lane(deck=1, control="volume", clock=1, points=[(0, 1.0), (8, 0.0)]),
        Lane(deck=2, control="play", clock=1, points=[(4, 1)]),
        Lane(deck=2, control="filter", clock=1, points=[(4, -1.0), (8, 0.0)]),
        Lane(deck=1, control="stem2", clock=1, points=[(0, 0.0)]),
    ]))
    bar = 4 * 60 / 126.0
    t = m.timeline
    assert t["clocks"] == {"[Channel1]": 200.0}
    vol, play, filt, stem = t["lanes"]
    assert vol == {"group": "[Channel1]", "key": "volume", "clock": "[Channel1]", "kind": "ramp",
                   "points": [[0.5, 1.0], [pytest.approx(0.5 + 8 * bar), 0.0]]}
    assert (play["group"], play["key"], play["kind"]) == ("[Channel2]", "play", "set")
    assert play["points"] == [[pytest.approx(0.5 + 4 * bar), 1.0]]
    assert (filt["group"], filt["key"]) == ("[QuickEffectRack1_[Channel2]]", "super1")
    assert [p[1] for p in filt["points"]] == [0.0, 0.5]
    assert (stem["group"], stem["key"]) == ("[Channel1_Stem2]", "volume")


def test_eq_ramps_follow_the_db_curve_and_kill_at_zero_gain():
    m, a = setup(0)
    a.load(1, CRATE[0].location)
    a.run(Timeline("t", [Lane(1, "eq_low", 1, [(0, 0.0), (4, float("-inf")), (6, 6.0)])]))
    [lane] = m.timeline["lanes"]
    assert lane["group"] == "[EqualizerRack1_[Channel1]_Effect1]"
    assert lane["key"] == "parameter1"
    points = lane["points"]
    assert len(points) == 1 + 2 * mixxx_adapter.CURVE_PIECES
    assert points[0][1] == 1.0
    assert points[mixxx_adapter.CURVE_PIECES][1] == 0.0
    # Halfway down a ramp to the kill is -30 dB, not half the gain
    assert points[mixxx_adapter.CURVE_PIECES // 2][1] == pytest.approx(10 ** (-30 / 20))
    assert points[-1][1] == pytest.approx(10 ** (6 / 20))


def test_a_lane_needs_a_clock_the_server_loaded():
    m, a = setup(0)
    with pytest.raises(AdapterError, match="deck 2 has no track"):
        a.run(Timeline("t", [Lane(1, "volume", 2, [(0, 1.0)])]))
    a.load(1, CRATE[0].location)
    with pytest.raises(AdapterError, match="no control"):
        a.run(Timeline("t", [Lane(1, "pitch", 1, [(0, 1.0)])]))
    with pytest.raises(AdapterError, match="decks 1 to 4"):
        a.run(Timeline("t", [Lane(5, "volume", 1, [(0, 1.0)])]))


def test_replace_keeps_what_is_left_from_a_bar():
    m, a = setup(0)
    a.load(1, CRATE[0].location)
    a.replace(Timeline("t2", [
        Lane(1, "volume", 1, [(0, 1.0), (8, 0.0)]),
        Lane(2, "play", 1, [(2, 1), (6, 0)]),
        Lane(1, "stem1", 1, [(0, 1.0)]),
    ]), from_bar=4)
    bar = 4 * 60 / 126.0
    vol, play = m.timeline["lanes"]
    assert vol["points"] == [[pytest.approx(0.5 + 4 * bar), 0.5],
                             [pytest.approx(0.5 + 8 * bar), 0.0]]
    assert play["points"] == [[pytest.approx(0.5 + 6 * bar), 0.0]]
