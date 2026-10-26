import json

from rhinestone.mixxx import frames


def join(out):
    """What the script makes of request frames, all in order and from one message."""
    return json.loads(b"".join(bytes(f[8:-1]) for f in out).decode("ascii"))


def test_a_small_request_is_one_frame():
    [frame] = frames.encode({"id": 1, "op": "state"}, 5)
    assert frame[:8] == [0xF0, 0x7D, 0x52, frames.TO_MIXXX, 0, 5, 0, 1]
    assert frame[-1] == 0xF7
    assert all(b < 0x80 for b in frame[1:-1])


def test_request_frames_stay_under_mixxx_limit():
    message = {"id": 2, "lanes": [[i * 0.123456789, -i] for i in range(800)], "name": "Kesha é"}
    out = frames.encode(message, 300)
    assert len(out) > 1
    assert all(len(f) <= 1024 for f in out)
    assert [f[6:8] for f in out] == [[i, len(out)] for i in range(len(out))]
    assert join(out) == message


def test_seq_wraps_to_14_bits():
    [frame] = frames.encode({}, 0x4000 + 3)
    assert frame[4:6] == [0, 3]


def test_replies_rejoin_with_odd_and_even_lengths():
    for message in ({"id": 1}, {"id": 12}, {"id": 3, "error": "unknown op é", "x": 1.0 / 3}):
        r = frames.ReplyReader()
        out = frames.encode_reply(message, 200)
        assert all(len(m) == 3 and m[1] < 0x80 and m[2] < 0x80 for m in out)
        results = [r.feed(m) for m in out]
        assert results[:-1] == [None] * (len(out) - 1)
        assert results[-1] == message


def test_a_reply_cut_short_or_corrupted_is_dropped_and_the_next_read():
    r = frames.ReplyReader()
    first = frames.encode_reply({"id": 1, "ok": True}, 1)
    second = frames.encode_reply({"id": 2, "ok": True}, 2)
    for m in first[:3]:
        assert r.feed(m) is None
    assert [r.feed(m) for m in second][-1] == {"id": 2, "ok": True}
    bad = [list(m) for m in frames.encode_reply({"id": 3}, 3)]
    bad[1][1] ^= 1
    assert [r.feed(m) for m in bad][-1] is None


def test_the_echo_of_a_request_and_other_midi_are_ignored():
    r = frames.ReplyReader()
    for f in frames.encode({"id": 1, "op": "state"}, 0):
        assert r.feed(f) is None
    assert r.feed([0x90, 60, 100]) is None
    assert r.feed([0xB1, 65, 66]) is None  # no start seen
