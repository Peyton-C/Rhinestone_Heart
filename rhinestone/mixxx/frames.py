"""The bridge's wire format, one way for each direction; see docs/bridge.md.

A message is JSON with every character escaped to ASCII, so its bytes are all under
0x80 and fit in MIDI data bytes unchanged, and floats keep their full precision.

To Mixxx, a message goes as SysEx. Mixxx drops whatever of an incoming SysEx message
passes 1024 bytes without a word (MIXXX_SYSEX_BUFFER_LEN in portmidicontroller.h), so
it is cut into frames:

    F0 7D 52 <direction> <seq hi> <seq lo> <part> <parts> <ASCII...> F7

7D is the manufacturer id set aside for non-commercial use, and 52 is "R". The
direction byte is always 1, to Mixxx, and the script ignores SysEx without it.

From Mixxx, a message goes as Control Change messages instead. Mixxx 2.6 on macOS
jams its MIDI output to an IAC bus for good once SysEx goes out faster than about one
message a second, or one message is long: the first gets through and everything after
it, short messages too, is held until Mixxx closes the port. Short messages go through
at full speed, 500 in about 10 ms. So a reply is a start, the text two characters to a
message, and an end carrying a checksum:

    B0 <seq> 00    B1 <char> <char> ...    [B2 <char> 00]    B3 <seq> <sum>
"""
from __future__ import annotations

import json

TO_MIXXX = 0x01

# The statuses of a reply from Mixxx, Control Change on channels 1 to 4
START, PAIR, SINGLE, END = 0xB0, 0xB1, 0xB2, 0xB3

HEADER = (0xF0, 0x7D, 0x52)

# ASCII per frame. Nine bytes of framing keep a frame at 969 bytes, under Mixxx's 1024
CHUNK = 960

MAX_PARTS = 127


def encode(message: dict, seq: int) -> list[list[int]]:
    """The SysEx frames carrying a request to Mixxx, numbered `seq` (kept to 14 bits)."""
    text = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
    data = text.encode("ascii")
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    if len(chunks) > MAX_PARTS:
        raise ValueError(f"a {len(data)} byte message needs more than {MAX_PARTS} frames")
    seq &= 0x3FFF
    return [[*HEADER, TO_MIXXX, seq >> 7, seq & 0x7F, i, len(chunks), *chunk, 0xF7]
            for i, chunk in enumerate(chunks)]


def encode_reply(message: dict, seq: int) -> list[list[int]]:
    """The short messages carrying a reply from Mixxx, as the controller script sends
    them. The server only reads these; this is for its tests."""
    data = json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    seq &= 0x7F
    out = [[START, seq, 0]]
    out += [[PAIR, data[i], data[i + 1]] for i in range(0, len(data) - 1, 2)]
    if len(data) % 2:
        out.append([SINGLE, data[-1], 0])
    out.append([END, seq, sum(data) & 0x7F])
    return out


class ReplyReader:
    """Joins the short messages of replies from Mixxx back into messages. One sender's
    messages arrive in order and the script sends each reply whole, so a reply is read
    from its start to its end; one that is cut short or fails its checksum is dropped."""

    def __init__(self):
        self._seq: int | None = None
        self._data = bytearray()

    def feed(self, message: list[int] | bytes) -> dict | None:
        m = list(message)
        if len(m) != 3:
            return None
        status, a, b = m
        if status == START:
            self._seq, self._data = a, bytearray()
        elif self._seq is None:
            return None
        elif status == PAIR:
            self._data += bytes((a, b))
        elif status == SINGLE:
            self._data.append(a)
        elif status == END:
            seq, data, self._seq = self._seq, self._data, None
            if a == seq and sum(data) & 0x7F == b:
                return json.loads(data.decode("ascii"))
        return None
