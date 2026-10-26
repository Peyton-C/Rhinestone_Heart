"""Throwaway test: one bass swap played in Mixxx through the adapter, to see whether a
timeline lands on the bars it names. Run with `uv run python spike/timeline/bass_swap.py`
while Mixxx has the bridge enabled and the crate playlist open.

Deck 1 plays from its bar 0, and deck 2, cued at its bar 0 with its bass killed and
sync on, comes in at deck 1's bar 8. At bar 16 the low bands swap, and deck 1 fades out
over the next 8 bars and stops. While it plays, this prints each deck's bar, and how far
deck 2's bars sit from deck 1's in beats, which is 0 when their downbeats meet.
"""
import sys
import time

from rhinestone import metadata
from rhinestone.adapter import Lane, Timeline
from rhinestone.mixxx.adapter import MixxxAdapter
from rhinestone.mixxx.bridge import Bridge
from rhinestone.mixxx.library import Library

OUTGOING, INCOMING = "Super Graphic", "Take It Off"
IN_AT, SWAP_AT, OUT_BY = 8, 16, 24
# Deck 2's bar it starts from. Take It Off's bar 0 is three quiet beats and a pickup
# into bar 1, where the music starts on deck 1's bar 9
CUE = 0

# Before deck 1's bar 0 by a long way, so these points are passed the moment the
# timeline starts and set the decks up then
NOW = -1000

KILL = float("-inf")


def song(rows, words):
    found = [t for t in rows if words.lower() in t.title.lower()]
    if len(found) != 1:
        sys.exit(f"{len(found)} songs match {words!r}")
    return found[0]


def main():
    bridge = Bridge()
    # Downbeats from the shared analysis named by RHINESTONE_METADATA
    library = Library(metadata=metadata.load())
    adapter = MixxxAdapter(bridge, library, "RH")
    rows = library.playlist("RH")
    out, inc = song(rows, OUTGOING), song(rows, INCOMING)
    # Pause decks left playing by an earlier run, with a lane already past on their clock
    for d in (1, 2):
        g = f"[Channel{d}]"
        bridge.call("run", timeline={"id": "pause", "lanes": [
            {"group": g, "key": "play", "clock": g, "kind": "set", "points": [[-1e9, 0]]}]})
    adapter.load(1, out.location)
    adapter.load(2, inc.location)
    print(f"deck 1: {out.title} at {out.grid.bpm:g} BPM, deck 2: {inc.title} at {inc.grid.bpm:g} BPM")

    lanes = [
        Lane(1, "position", 1, [(NOW, 0)]),
        Lane(1, "play", 1, [(NOW, 1), (OUT_BY, 0)]),
        Lane(1, "volume", 1, [(NOW, 1.0), (SWAP_AT, 1.0), (OUT_BY, 0.0)]),
        Lane(1, "eq_low", 1, [(NOW, 0.0), (SWAP_AT, 0.0), (SWAP_AT, KILL)]),
        Lane(1, "eq_mid", 1, [(NOW, 0.0)]),
        Lane(1, "eq_high", 1, [(NOW, 0.0)]),
        Lane(2, "position", 1, [(NOW, CUE)]),
        Lane(2, "sync", 1, [(NOW, 1)]),
        Lane(2, "volume", 1, [(NOW, 1.0)]),
        Lane(2, "eq_low", 1, [(NOW, KILL), (SWAP_AT, KILL), (SWAP_AT, 0.0)]),
        Lane(2, "eq_mid", 1, [(NOW, 0.0)]),
        Lane(2, "eq_high", 1, [(NOW, 0.0)]),
        Lane(2, "play", 1, [(IN_AT, 1)]),
    ]
    adapter.run(Timeline("bass-swap", lanes))
    print(f"running: deck 2 in at bar {IN_AT}, swap at {SWAP_AT}, deck 1 out by {OUT_BY}\n")

    offsets = []
    last = None
    deadline = time.time() + 120
    while time.time() < deadline:
        s = adapter.state()
        d1, d2 = s["decks"][0], s["decks"][1]
        bar1, bar2 = d1.get("bar"), d2.get("bar")
        line = f"deck 1 bar {bar1:6.2f} {'plays' if d1['play'] else 'stopped'} {d1['bpm']:7.2f} BPM"
        if d1["play"] and d2["play"] and bar2 is not None:
            # Synced, both decks go through bars at one rate, so deck 2's bars since its
            # cue match deck 1's since IN_AT when the downbeats meet. In beats
            off = ((bar2 - CUE) - (bar1 - IN_AT)) * 4
            offsets.append(off)
            line += f" | deck 2 bar {bar2:6.2f} {d2['bpm']:7.2f} BPM, {off:+.3f} beats off"
        if int(bar1) != last:
            print(line)
            last = int(bar1)
        if not s["timeline"] and not d1["play"]:
            break
        time.sleep(0.25)
    while not bridge.events.empty():
        e = bridge.events.get()
        print("event:", e.get("event"), e.get("timeline", ""))
    if offsets:
        print(f"\ndeck 2 against deck 1: {min(offsets):+.3f} to {max(offsets):+.3f} beats")


if __name__ == "__main__":
    main()
