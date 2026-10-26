"""The server's side of the bridge: requests as SysEx over a MIDI port to the controller
script, and its replies back as short messages; frames.py has both.

The port is an IAC bus, which outlives both ends, so Mixxx and the server can start in
either order. Replies are matched to requests by id, and messages the script sends
unasked, its events, are queued for whoever wants them. The bus loses a short message
now and then under load, and with it a whole reply, so a reply that does not come is
asked for again; the script keeps its recent replies, so the request is not repeated.
"""
from __future__ import annotations

import os
import queue
import threading
import time

from . import frames

DEFAULT_PORT = "IAC Driver Bus 1"

# How long a request may go unanswered before the script counts as not there. Every
# operation but load answers within a frame or two
ANSWER_SECONDS = 1.5

# A reply takes a few milliseconds, so one missing this long was lost and is asked for again
RESEND_SECONDS = 0.25

# Between frames of one message. Mixxx reads its MIDI input every millisecond into a
# buffer of about 4 KB, and a frame is under 1 KB, so 2 ms keeps well clear of filling it
FRAME_GAP_SECONDS = 0.002


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, port: str | None = None, midi=None):
        """`midi` is an (output, input) pair of opened ports, for tests; by default the
        IAC bus named `port` is opened with rtmidi."""
        self.port = port or os.environ.get("RHINESTONE_MIDI_PORT", DEFAULT_PORT)
        if midi:
            self._out, self._in = midi
        else:
            import rtmidi  # here, so the rest of the package and its tests run without it

            self._out = rtmidi.MidiOut()
            self._in = rtmidi.MidiIn()
            self._out.open_port(self._find(self._out.get_ports(), "output"))
            self._in.open_port(self._find(self._in.get_ports(), "input"))
            self._in.ignore_types(sysex=True, timing=True, active_sense=True)
            self._in.set_callback(self._receive)
        self._replies = frames.ReplyReader()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._seq = 0
        self._waiting: dict[int, list] = {}
        self.events: queue.Queue[dict] = queue.Queue()
        self.session: str | None = None
        # Set when the session id changes, which is Mixxx or the script restarting.
        # Whoever keeps state about Mixxx's library view clears it and resets this
        self.restarted = threading.Event()

    def _find(self, ports: list[str], kind: str) -> int:
        for i, name in enumerate(ports):
            if self.port in name:
                return i
        raise BridgeError(f"no MIDI {kind} named {self.port!r}, there are: {', '.join(ports) or 'none'}. "
                          "Turn the IAC Driver on in Audio MIDI Setup")

    def close(self) -> None:
        self._in.close_port()
        self._out.close_port()

    def call(self, op: str, timeout: float = ANSWER_SECONDS, **args) -> dict:
        """Sends one request and returns the script's reply, raising BridgeError for an
        error reply or none at all."""
        rid = self._new_id()
        with self._lock:
            slot = self._waiting[rid] = [threading.Event(), None]
        try:
            self._send({"id": rid, "op": op, **args})
            deadline = time.monotonic() + timeout
            gap = RESEND_SECONDS
            while not slot[0].wait(max(0.0, min(gap, deadline - time.monotonic()))):
                if time.monotonic() >= deadline:
                    raise BridgeError(f"Mixxx did not answer {op} within {timeout:g} s. Check Mixxx "
                                      f"is running with the Rhinestone Bridge mapping enabled on "
                                      f"{self.port}")
                # Its answer to this is ignored: a kept reply comes under the first id, and
                # a request still running, like a slow load, has none kept yet
                self._send({"id": self._new_id(), "op": "resend", "of": rid})
                gap = min(gap * 2, 2.0)
        finally:
            with self._lock:
                self._waiting.pop(rid, None)
        reply = slot[1]
        if not reply.get("ok"):
            raise BridgeError(reply.get("error") or f"{op} failed")
        return reply

    def _new_id(self) -> int:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            return rid

    def _send(self, message: dict) -> None:
        with self._send_lock:
            seq = self._seq
            self._seq = (self._seq + 1) & 0x3FFF
            for i, frame in enumerate(frames.encode(message, seq)):
                if i:
                    time.sleep(FRAME_GAP_SECONDS)
                self._out.send_message(frame)

    def _receive(self, event, _data=None) -> None:
        # The bus echoes this side's own SysEx requests back, which the reader ignores
        message = self._replies.feed(event[0])
        if message is None:
            return
        self._observe_session(message.get("session"))
        rid = message.get("id")
        with self._lock:
            slot = self._waiting.get(rid) if rid is not None else None
        if slot:
            slot[1] = message
            slot[0].set()
        elif "event" in message:
            self.events.put(message)

    def _observe_session(self, session: str | None) -> None:
        if session and session != self.session:
            if self.session is not None:
                self.restarted.set()
            self.session = session
