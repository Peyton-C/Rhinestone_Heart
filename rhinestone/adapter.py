"""The contract between the server and a DJ app, so another app can be connected later.

The server never names an app's control, and an adapter never sees a semantic move:
the server compiles moves into a Timeline of lanes in the neutral units below, and the
adapter turns those into its app's controls and plays them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Every control a lane can set, with its unit. Values are in these units whatever the
# app's own curves are, so a bass swap means the same thing in every app
CONTROLS = {
    "volume": "the deck's fader as a gain, 0 to 1",
    "eq_low": "dB, 0 at unity, -inf kills the band",
    "eq_mid": "dB, 0 at unity, -inf kills the band",
    "eq_high": "dB, 0 at unity, -inf kills the band",
    "filter": "-1 is the low-pass fully closed, 0 off, 1 the high-pass fully closed",
    "stem1": "the stem slot's volume, 0 to 1",
    "stem2": "the stem slot's volume, 0 to 1",
    "stem3": "the stem slot's volume, 0 to 1",
    "stem4": "the stem slot's volume, 0 to 1",
    "play": "1 plays, 0 pauses",
    "sync": "1 locks the deck's tempo and phase to the others, 0 frees it",
    "position": "jumps the deck to a bar of its own track",
}

# Controls whose points are set as the clock passes them, where the rest ramp in a
# straight line from point to point in the units above
SET_CONTROLS = frozenset({"play", "sync", "position"})


@dataclass
class Lane:
    """One control on one deck over time. Points are (bar, value), the bars counted on
    the track loaded on the clock deck, so the lane follows that track's play position.
    A ramp does nothing before its first point, so a timeline sets every control it
    relies on at its start rather than inherit what the deck had."""
    deck: int
    control: str
    clock: int
    points: list[tuple[float, float]]


@dataclass
class Timeline:
    id: str
    lanes: list[Lane] = field(default_factory=list)


@dataclass(frozen=True)
class Capabilities:
    decks: int
    stem_slots: int
    eq_bands: tuple[str, ...]
    eq_kill: bool
    eq_max_db: float
    controls: tuple[str, ...]


class AdapterError(Exception):
    pass


class Adapter(ABC):
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """What the app can do, for checking plans against before they play."""

    @abstractmethod
    def load(self, deck: int, file: str) -> None:
        """Loads a file onto a deck that is not playing, and confirms it is the one asked for."""

    @abstractmethod
    def bar_to_position(self, file: str, bar: float) -> float:
        """Seconds into a file where a bar starts, on the app's own beatgrid."""

    @abstractmethod
    def run(self, timeline: Timeline) -> None:
        """Plays a timeline, replacing any already playing."""

    @abstractmethod
    def replace(self, timeline: Timeline, from_bar: float) -> None:
        """Replaces the unplayed part of what is playing, from a bar on each lane's
        clock: for a change of course mid-blend."""

    @abstractmethod
    def state(self) -> dict:
        """What each deck holds and is doing, with the bar each loaded deck is at on its
        own track, and an id that changes when the app restarts."""
