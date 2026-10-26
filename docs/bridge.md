# Bridge
The server reaches Mixxx through a controller mapping, Rhinestone Bridge, whose script takes requests from the server over an IAC bus, runs them inside Mixxx and replies the same way. Everything specific to Mixxx sits behind the adapter contract in `rhinestone/adapter.py`, so the rest of the server never names a Mixxx control.

```
rhinestone/mixxx/adapter.py  ->  bridge.py  ->  IAC bus  ->  mapping/Rhinestone-Bridge.js
 rows, grids, units          SysEx in, CC out               runs ops and timelines in Mixxx
        |
   library.py  <-  mixxxdb.sqlite, read only
```

## Setup
1. In Audio MIDI Setup, open the MIDI Studio window, open IAC Driver and tick Device is online.
2. Run `uv run rhinestone install`, which copies the mapping into Mixxx's settings folder.
3. In Mixxx's preferences, under Controllers, choose Rhinestone Bridge for IAC Driver Bus 1 and enable it.
4. Give the app that runs the server Full Disk Access, under Privacy & Security. Mixxx's database sits in its sandbox, which macOS protects.
5. Make the crate playlist, `RH` unless you pass `--playlist`, and leave it open in Mixxx. The server sorts it by `#` itself.

`uv run rhinestone state` checks the connection, and `uv run rhinestone load 1 "rhinestone heart"` loads a song from the crate onto deck 1.

## Messages
A message is JSON with every character escaped to ASCII, so every byte fits a MIDI data byte unchanged and floats keep full precision. The two directions travel differently.

To Mixxx, a request goes as SysEx. Mixxx drops what passes 1024 bytes of one SysEx message, so a request travels in frames of at most 960 characters:

```
F0 7D 52 01 <seq hi> <seq lo> <part> <parts> <ASCII...> F7
```

| Byte | What |
| --- | --- |
| `7D 52` | The manufacturer id for non-commercial use, then "R" |
| `01` | To Mixxx. The script ignores SysEx without it |
| seq | The request's number, 14 bits |
| part, parts | This frame's place, from 0, and how many frames the request has |

Mixxx passes SysEx to a script only through an input mapping for status `0xF0`, which is the mapping's one control. The server leaves 2 ms between frames, since Mixxx reads its MIDI input every millisecond into a buffer of about 4 KB.

From Mixxx, a reply goes as Control Change messages, never SysEx. Mixxx 2.6 on macOS jams its MIDI output to an IAC bus once SysEx goes out faster than about one message a second, or one message is long: the first gets through, and everything after it, short messages too, waits until Mixxx closes the port. Short messages go through at full speed, 500 in about 10 ms.

| Status | Data |
| --- | --- |
| `B0` | Start: the reply's number, 7 bits |
| `B1` | Two characters |
| `B2` | The last character, when there is an odd one |
| `B3` | End: the reply's number again and the sum of its characters, 7 bits |

The server drops a reply that is cut short or whose sum does not match. The IAC bus loses a short message now and then under load, about one reply in thirty while `state` was asked four times a second, so the script keeps its last 16 replies and the server asks for one again with `resend` when it has heard nothing for a quarter of a second. The request itself is never sent twice, since a load may already have happened. The script rounds its numbers to keep replies short.

If Mixxx's output ever jams, as it will after SysEx sent from a script, only restarting Mixxx clears it.

## Operations
A request is `{"id": n, "op": ..., ...}` and its reply carries the same id, `ok`, and the script's session, a random id picked when the mapping starts. The script also sends events unasked: `hello` when it starts, so a running server learns Mixxx restarted, and `timeline_done` and `clock_changed` as a timeline plays.

| Op | What |
| --- | --- |
| `hello` | The script's version and how many decks and preview decks Mixxx has |
| `state` | Each deck's track, length, BPM, play state and position, the preview deck, the library's sort, and the timeline playing |
| `move` | Moves the library's selection by `delta` rows |
| `refresh` | Makes the open playlist read the database again |
| `sort` | Sorts the open view by `column` ascending |
| `load` | Loads the selected row onto `group`, a deck or `[PreviewDeck1]`, and answers once it has loaded or `timeout_ms` has passed |
| `run` | Plays a timeline, replacing any playing |
| `stop` | Ends the timeline playing. The decks keep playing |
| `resend` | Sends the reply to request `of` again, without doing it again |

`load` refuses a deck that is playing. It empties the deck first, so a load that fails, as a missing file's does, reads as `loaded: false` and not as the old track. Mixxx reloads the last ejected track when an empty deck is ejected, and the one before when a deck is ejected twice within half a second, so the script ejects only a deck with a track on it and keeps ejects on one deck 600 ms apart. Once a track has loaded, Mixxx still seeks it to its cue point a moment later, so `load` answers 250 ms after the track is in, and a position set after it holds.

## Loading by row
Mixxx gives a script no way to name a track: it can only move the library's selection and load what is selected. Every song therefore has a fixed row in the crate playlist, sorted by `#` ascending, and the adapter keeps track of which row is selected. When the view is sorted otherwise, the adapter sorts it back before loading. Moves wrap at both ends, so it moves by the difference between rows, whichever way round is shorter.

To learn which row is selected, the adapter loads it into the preview deck, which no one hears, and matches its length and BPM against the crate, stepping down a row at a time until one row explains everything it has seen. With nothing selected the preview loads nothing and the first step selects row 0. The adapter works the row out again after Mixxx restarts and whenever a load brings the wrong track, as it does when someone clicks in the library.

Rows added to the crate while Mixxx runs reach the open view when it sorts, so the adapter flips the sort order and back. The sidebar's count of the playlist's tracks stays stale, which does no harm.

## Timelines
A timeline is a set of lanes, each setting one control from the play position of a clock deck, in seconds into that deck's track. It follows the music and not the wall clock, so timing never waits on the server and a paused deck holds its lanes.

| Lane | What |
| --- | --- |
| `group`, `key` | The Mixxx control |
| `clock` | The deck whose position it follows |
| `kind` | `ramp` sets straight lines between points from the first point on, and `set` sets each point's value once as the clock passes it |
| `points` | `[seconds, value]` pairs in time order |

Lanes that set a clock deck's own position run first in each update, and the rest are judged by where that jump left the deck. The timeline carries the length of the track each clock deck had when it was written. A clock deck whose track changes stops its lanes and sends `clock_changed`.

The adapter writes these from neutral lanes, whose points are bars on the clock deck's track and whose values are in the units `CONTROLS` lists. Beats come from Mixxx's beatgrid, but Mixxx knows nothing of bars, and its grid's first beat is not always a downbeat: Flashing Lights' starts three beats before one. So the server works out which beat of the grid is the downbeat, and counts bars in fours from it, before as well as after. Mixxx's grids are not changed.

| Source | Used when |
| --- | --- |
| Section starts from the shared analysis, the beat of the bar most of them fall on | At least 3 fall on a beat, and 60% of those on one beat of the bar |
| Where the drum stem first comes in, from the same analysis | The sections do not settle it and the drums enter on a beat. Drums can come in on a fill a beat early, as Rhinestone Heart's do, so they come second |
| Where Mixxx's analyzer found the sound starts | There is no shared analysis for the track. Take It Off's sound starts on a pickup a beat before its first full bar, so this is a last resort |
| Mixxx's first beat | Nothing else |

`uv run rhinestone --metadata crate.json grids` lists each crate track's downbeat and what set it, and flags a grid whose tempo disagrees with the analysis: one off by a fraction of a BPM drifts against the music over the song, and one at half or double the tempo has bars twice or half as long. The export is `pink diamond --export-metadata crate.json`, named with `--metadata` or `RHINESTONE_METADATA`, and songs are matched to Mixxx's tracks by path. EQ is ramped in dB, so its lanes are split into short straight pieces of gain. `position` jumps a deck to a bar of its own track, and a lane turning `sync` on turns quantize on too, since Mixxx's sync matches phase only with it. The script is updated on each change of play position, once per audio buffer, so a point lands within a buffer or two of its time.
