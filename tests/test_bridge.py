import json
import threading

import pytest

from rhinestone.mixxx import bridge as bridge_module
from rhinestone.mixxx import frames
from rhinestone.mixxx.bridge import Bridge, BridgeError


class FakeScript:
    """The controller script on the far side of fake ports: it answers each request,
    keeps its replies, and loses the ones listed in `lose` the first time round."""

    def __init__(self, lose=(), silent=False, slow_ops=()):
        self.lose = set(lose)
        self.silent = silent
        self.slow_ops = set(slow_ops)
        self.kept = {}
        self.ops = []
        self.bridge = None

    def send_message(self, frame):
        request = json.loads(bytes(frame[8:-1]).decode("ascii"))
        self.ops.append(request["op"])
        if self.silent:
            return
        if request["op"] == "resend":
            reply = self.kept.get(request["of"]) or {"id": request["id"], "ok": False,
                                                     "error": "no reply kept"}
        elif request["op"] in self.slow_ops:
            threading.Timer(0.6, self._answer, [request]).start()
            return
        else:
            reply = self.kept[request["id"]] = {"id": request["id"], "session": "s", "ok": True,
                                                "op": request["op"]}
            if request["op"] in self.lose:
                self.lose.discard(request["op"])
                return
        self._deliver(reply)

    def _answer(self, request):
        reply = self.kept[request["id"]] = {"id": request["id"], "session": "s", "ok": True}
        self._deliver(reply)

    def _deliver(self, reply):
        for m in frames.encode_reply(reply, 0):
            self.bridge._receive((m, 0.0))

    def close_port(self):
        pass


def connect(script):
    b = Bridge(midi=(script, script))
    script.bridge = b
    return b


def test_a_lost_reply_is_sent_again_without_repeating_the_request():
    script = FakeScript(lose={"load"})
    b = connect(script)
    assert b.call("load", group="[Channel1]")["op"] == "load"
    assert script.ops == ["load", "resend"]


def test_a_slow_request_is_waited_for_through_resends():
    script = FakeScript(slow_ops={"load"})
    b = connect(script)
    assert b.call("load", timeout=3)["ok"] is True
    assert script.ops[0] == "load" and set(script.ops[1:]) == {"resend"}


def test_no_answer_at_all_says_mixxx_is_not_there(monkeypatch):
    monkeypatch.setattr(bridge_module, "RESEND_SECONDS", 0.05)
    b = connect(FakeScript(silent=True))
    with pytest.raises(BridgeError, match="did not answer state"):
        b.call("state", timeout=0.3)


def test_an_error_reply_raises_with_its_message():
    script = FakeScript()
    b = connect(script)

    def refuse(frame):
        request = json.loads(bytes(frame[8:-1]).decode("ascii"))
        script._deliver({"id": request["id"], "ok": False, "error": "[Channel1] is playing"})

    script.send_message = refuse
    with pytest.raises(BridgeError, match="is playing"):
        b.call("load")


def test_a_new_session_marks_a_restart_and_events_are_queued():
    script = FakeScript()
    b = connect(script)
    b.call("hello")
    assert not b.restarted.is_set()
    script._deliver({"event": "hello", "session": "t"})
    assert b.restarted.is_set()
    assert b.events.get_nowait()["event"] == "hello"
