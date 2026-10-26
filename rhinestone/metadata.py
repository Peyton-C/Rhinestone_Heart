"""The shared metadata export: one analysis of the whole crate that every harness in the
comparison reads, so they all see the same song data. It comes from pink diamond's
`--export-metadata`, whose docs/metadata.md lays the format out.

Every position in it is in seconds from the start of the file, so it can be placed on
any app's beatgrid. Songs are matched to an app's library by path.
"""
from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path

FORMAT = "crate-metadata"
SCHEMA_VERSIONS = (1,)


class MetadataError(Exception):
    pass


def key(path: str) -> str:
    """A path as songs are matched by. macOS keeps an accented name in either of two
    Unicode forms, and the export and an app's library need not agree on which."""
    return unicodedata.normalize("NFC", path)


def load(path: str | Path | None = None) -> dict[str, dict] | None:
    """The export's songs by path, from `path` or RHINESTONE_METADATA, or None when
    neither names a file."""
    path = path or os.environ.get("RHINESTONE_METADATA")
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise MetadataError(f"cannot read the metadata export {path}: {e}") from e
    if data.get("format") != FORMAT or data.get("schema_version") not in SCHEMA_VERSIONS:
        raise MetadataError(f"{path} is not a {FORMAT} export of a version this reads "
                            f"({', '.join(map(str, SCHEMA_VERSIONS))})")
    return {key(s["path"]): s for s in data.get("songs", [])}


def drum_entry(song: dict) -> float | None:
    """Where a stem file's drums first come in, in seconds."""
    for track in (song.get("stems") or {}).get("tracks", []):
        if track.get("code") == "drums":
            return track.get("first_entry")
    return None


def section_starts(song: dict) -> list[float]:
    """The section starts the analysis found. A first section added from 0 to the first
    one found is left out: it is no downbeat."""
    return [s["start"] for s in song.get("sections", []) if s.get("detected")]
