"""rhinestone install | state | load, for setting up Mixxx and trying the bridge by hand."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .adapter import AdapterError
from .mixxx.bridge import BridgeError
from .mixxx.library import DEFAULT_DB, Library, LibraryError

MAPPING = Path(__file__).parent / "mixxx" / "mapping"
DEFAULT_PLAYLIST = "RH"


def _adapter(args):
    from .mixxx.adapter import MixxxAdapter
    from .mixxx.bridge import Bridge
    return MixxxAdapter(Bridge(args.port), Library(args.db), args.playlist)


def install(args) -> None:
    folder = Path(args.mixxx_dir) / "controllers"
    folder.mkdir(parents=True, exist_ok=True)
    for f in sorted(MAPPING.iterdir()):
        shutil.copy2(f, folder / f.name)
        print(f"copied {f.name} to {folder}")
    print("In Mixxx's preferences, under Controllers, choose Rhinestone Bridge for the IAC bus "
          "and enable it. Reload it there after installing a new version.")


def state(args) -> None:
    print(json.dumps(_adapter(args).state(), indent=2))


def load(args) -> None:
    rows = Library(args.db).playlist(args.playlist)
    query = args.song.casefold()
    found = [t for t in rows if query in t.title.casefold() or query in Path(t.location).name.casefold()]
    if len(found) != 1:
        names = "\n  ".join(f"{t.title} - {t.artist}" for t in found) or "none"
        sys.exit(f"{len(found)} songs in {args.playlist} match {args.song!r}:\n  {names}")
    _adapter(args).load(args.deck, found[0].location)
    print(f"deck {args.deck}: {found[0].title} - {found[0].artist}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="rhinestone")
    parser.add_argument("--port", help="the MIDI port the bridge mapping is enabled on, "
                        "IAC Driver Bus 1 unless RHINESTONE_MIDI_PORT says")
    parser.add_argument("--db", help=f"Mixxx's database, {DEFAULT_DB} by default")
    parser.add_argument("--playlist", default=DEFAULT_PLAYLIST,
                        help=f"the crate playlist, {DEFAULT_PLAYLIST} by default")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install", help="copy the bridge mapping into Mixxx")
    p.add_argument("--mixxx-dir", default=str(DEFAULT_DB.parent),
                   help="Mixxx's settings folder")
    p.set_defaults(run=install)
    sub.add_parser("state", help="what Mixxx's decks hold").set_defaults(run=state)
    p = sub.add_parser("load", help="load a song from the crate playlist onto a deck")
    p.add_argument("deck", type=int)
    p.add_argument("song", help="words from its title or file name")
    p.set_defaults(run=load)
    args = parser.parse_args()
    try:
        args.run(args)
    except (AdapterError, BridgeError, LibraryError) as e:
        sys.exit(str(e))
