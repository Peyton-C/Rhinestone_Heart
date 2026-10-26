import assert from "node:assert/strict";
import {test} from "node:test";

import {LOAD_MS, encode, fakeMixxx} from "./fake_mixxx.mjs";

function started(options) {
    const m = fakeMixxx(options);
    m.B.init("Rhinestone", false);
    return m;
}

test("says hello with a session on start", () => {
    const m = started();
    const [hello] = m.messages();
    assert.equal(hello.event, "hello");
    assert.ok(hello.session);
});

test("answers with its session, and ignores its own echoes", () => {
    const m = started();
    for (const f of encode({id: 9, op: "hello"}, 0, 0x02)) {
        m.B.incomingData(f, f.length);
    }
    assert.equal(m.reply(9), undefined);
    m.request({id: 1, op: "hello"});
    const r = m.reply(1);
    assert.equal(r.ok, true);
    assert.equal(r.decks, 4);
    assert.equal(r.session, m.messages()[0].session);
});

test("takes a request as an ArrayBuffer, as Mixxx passes it", () => {
    const m = started();
    const [f] = encode({id: 1, op: "hello"});
    m.B.incomingData(new Uint8Array(f).buffer, f.length);
    assert.equal(m.reply(1).ok, true);
});

test("an unknown op is refused with the ops there are", () => {
    const m = started();
    m.request({id: 1, op: "dance"});
    const r = m.reply(1);
    assert.equal(r.ok, false);
    assert.match(r.error, /unknown op "dance", the ops are .*load.*run/);
});

test("replies are ASCII whatever they carry", () => {
    const m = started();
    m.request({id: 1, op: "dance é"});
    assert.ok(m.sent.every(([, a, b]) => a < 0x80 && b < 0x80));
    assert.match(m.reply(1).error, /dance é/);
});

test("resend sends a kept reply again without doing its request again", () => {
    const m = started();
    m.request({id: 7, op: "move", delta: 2});
    m.request({id: 8, op: "resend", of: 7});
    const replies = m.messages().filter((x) => x.id === 7);
    assert.equal(replies.length, 2);
    assert.deepEqual(replies[0], replies[1]);
    assert.equal(m.sets.filter((s) => s[1] === "SelectTrackKnob").length, 1);
    m.request({id: 9, op: "resend", of: 99});
    assert.match(m.reply(9).error, /no reply kept for 99/);
});

test("keeps only the latest replies", () => {
    const m = started();
    for (let i = 1; i <= 20; i++) {
        m.request({id: i, op: "hello"});
    }
    m.request({id: 100, op: "resend", of: 1});
    assert.equal(m.reply(100).ok, false);
    m.request({id: 101, op: "resend", of: 20});
    assert.equal(m.messages().filter((x) => x.id === 20).length, 2);
});

test("state rounds its numbers for the wire", () => {
    const m = started();
    m.put("[Channel1]", "playposition", 0.123456789123);
    m.put("[Channel1]", "bpm", 125.999999999);
    m.request({id: 1, op: "state"});
    const d = m.reply(1).decks[0];
    assert.equal(d.playposition, 0.12345679);
    assert.equal(d.bpm, 126);
});

test("move moves the selection, and a zero move sets nothing", () => {
    const m = started();
    m.request({id: 1, op: "move", delta: -3});
    m.request({id: 2, op: "move", delta: 0});
    assert.deepEqual(m.sets.filter((s) => s[1] === "SelectTrackKnob"), [["[Playlist]", "SelectTrackKnob", -3]]);
    assert.equal(m.reply(2).ok, true);
});

test("loads onto an empty deck without ejecting, and waits for the track", () => {
    const m = started();
    m.mixxx.selected = {duration: 117.8, file_bpm: 87};
    m.request({id: 1, op: "load", group: "[Channel1]"});
    assert.equal(m.reply(1), undefined);
    m.advance(LOAD_MS + 50);
    assert.equal(m.reply(1), undefined, "waits for Mixxx's seek to the cue point");
    m.advance(m.B.SETTLE_MS);
    assert.deepEqual(m.reply(1), {id: 1, session: m.B.session, ok: true, loaded: true,
        duration: 117.8, file_bpm: 87});
    assert.ok(!m.sets.some((s) => s[1] === "eject"));
});

test("empties a loaded deck first, so a failed load reads as not loaded", () => {
    const m = started();
    m.request({id: 1, op: "load", group: "[Channel2]"});
    m.advance(1000);
    m.mixxx.selected = "missing";
    m.request({id: 2, op: "load", group: "[Channel2]", timeout_ms: 500});
    m.advance(2000);
    assert.ok(m.sets.some((s) => s[0] === "[Channel2]" && s[1] === "eject" && s[2] === 1));
    const r = m.reply(2);
    assert.equal(r.loaded, false);
    assert.equal(m.get("[Channel2]", "track_loaded"), 0);
});

test("keeps ejects on one deck 600 ms apart, so Mixxx does not un-eject", () => {
    const m = started();
    m.request({id: 1, op: "load", group: "[Channel1]"});
    m.advance(1000);
    m.request({id: 2, op: "load", group: "[Channel1]"});
    m.advance(250);
    m.request({id: 3, op: "load", group: "[Channel1]"});
    const ejects = [];
    for (let t = 0; t < 2000; t += 10) {
        const before = m.sets.length;
        m.advance(10);
        if (m.sets.slice(before).some((s) => s[1] === "eject" && s[2] === 1)) {
            ejects.push(t);
        }
    }
    assert.equal(m.reply(3).loaded, true);
    // Load 2 ejected at 1000 ms and load 3 came at 1250, so its eject waits until 1600,
    // which this loop, starting at 1250, sees in the step from 340 ms
    assert.deepEqual(ejects, [340]);
});

test("will not load a playing deck, and pauses the preview deck to load it", () => {
    const m = started();
    m.put("[Channel1]", "play", 1);
    m.request({id: 1, op: "load", group: "[Channel1]"});
    assert.match(m.reply(1).error, /playing/);
    m.put("[PreviewDeck1]", "play", 1);
    m.request({id: 2, op: "load", group: "[PreviewDeck1]"});
    m.advance(1000);
    assert.equal(m.reply(2).loaded, true);
    assert.equal(m.get("[PreviewDeck1]", "play"), 0);
    m.request({id: 3, op: "load", group: "[Master]"});
    assert.match(m.reply(3).error, /deck or preview deck/);
});

test("refresh flips the sort order and puts it back", () => {
    const m = started();
    m.put("[Library]", "sort_column", 23);
    m.request({id: 1, op: "refresh"});
    m.advance(1000);
    assert.deepEqual(m.sets.filter((s) => s[1] === "sort_order").map((s) => s[2]), [1, 0]);
    assert.deepEqual(m.reply(1), {id: 1, session: m.B.session, ok: true, sort_column: 23, sort_order: 0});
});

test("sort ends ascending on the column, from another column or the same one", () => {
    for (const [column, order] of [[3, 0], [3, 1], [23, 0], [23, 1]]) {
        const m = started();
        m.put("[Library]", "sort_column", column);
        m.put("[Library]", "sort_order", order);
        m.request({id: 1, op: "sort", column: 23});
        m.advance(1000);
        const r = m.reply(1);
        assert.deepEqual([r.sort_column, r.sort_order], [23, 0], `from ${column}, ${order}`);
    }
});

function deckAt(m, group, seconds, duration = 200) {
    m.put(group, "duration", duration);
    m.put(group, "playposition", seconds / duration);
}

test("ramps follow the clock deck's position, and sets fire once each", () => {
    const m = started();
    deckAt(m, "[Channel1]", 0);
    m.request({id: 1, op: "run", timeline: {id: "t1", clocks: {"[Channel1]": 200}, lanes: [
        {group: "[Channel1]", key: "volume", clock: "[Channel1]", kind: "ramp", points: [[10, 1], [20, 0]]},
        {group: "[Channel2]", key: "play", clock: "[Channel1]", kind: "set", points: [[12, 1], [18, 0]]},
    ]}});
    assert.equal(m.reply(1).lanes, 2);
    const vol = () => m.get("[Channel1]", "volume");
    deckAt(m, "[Channel1]", 5);
    assert.equal(vol(), 0, "nothing before the first point");
    deckAt(m, "[Channel1]", 15);
    assert.equal(vol(), 0.5);
    assert.equal(m.get("[Channel2]", "play"), 1);
    deckAt(m, "[Channel1]", 14);
    assert.ok(Math.abs(vol() - 0.6) < 1e-9, "a ramp follows the position back");
    assert.equal(m.sets.filter((s) => s[1] === "play").length, 1, "a set fires once");
    deckAt(m, "[Channel1]", 25);
    assert.equal(vol(), 0);
    assert.equal(m.get("[Channel2]", "play"), 0);
    assert.ok(m.messages().some((x) => x.event === "timeline_done" && x.timeline === "t1"));
    assert.ok(m.connections.every((c) => !c.live));
});

test("a timeline starting mid-way catches up at once", () => {
    const m = started();
    deckAt(m, "[Channel1]", 30);
    m.request({id: 1, op: "run", timeline: {id: "t", lanes: [
        {group: "[Channel2]", key: "sync_enabled", clock: "[Channel1]", kind: "set", points: [[0, 1]]},
        {group: "[Channel1]", key: "volume", clock: "[Channel1]", kind: "ramp", points: [[20, 1], [40, 0]]},
    ]}});
    assert.equal(m.get("[Channel2]", "sync_enabled"), 1);
    assert.equal(m.get("[Channel1]", "volume"), 0.5);
});

test("a jump of the clock deck is made before other lanes are judged", () => {
    const m = started();
    deckAt(m, "[Channel1]", 46);
    m.request({id: 1, op: "run", timeline: {id: "t", lanes: [
        {group: "[Channel2]", key: "play", clock: "[Channel1]", kind: "set", points: [[30, 1]]},
        {group: "[Channel1]", key: "playposition", clock: "[Channel1]", kind: "set", points: [[-1e9, 0.1]]},
        {group: "[Channel1]", key: "volume", clock: "[Channel1]", kind: "ramp", points: [[10, 0], [30, 1]]},
    ]}});
    assert.equal(m.get("[Channel1]", "playposition"), 0.1);
    assert.equal(m.get("[Channel2]", "play"), 0, "bar 30 s is still ahead after the jump to 20 s");
    assert.ok(Math.abs(m.get("[Channel1]", "volume") - 0.5) < 1e-9);
    deckAt(m, "[Channel1]", 31);
    assert.equal(m.get("[Channel2]", "play"), 1);
});

test("a track changed under a clock deck stops its lanes and says so", () => {
    const m = started();
    deckAt(m, "[Channel1]", 0);
    m.request({id: 1, op: "run", timeline: {id: "t", clocks: {"[Channel1]": 200}, lanes: [
        {group: "[Channel1]", key: "volume", clock: "[Channel1]", kind: "ramp", points: [[0, 1], [10, 0]]},
    ]}});
    deckAt(m, "[Channel1]", 5, 150);
    deckAt(m, "[Channel1]", 6, 150);
    const changed = m.messages().filter((x) => x.event === "clock_changed");
    assert.equal(changed.length, 1);
    assert.equal(changed[0].duration, 150);
    assert.equal(m.get("[Channel1]", "volume"), 1);
});

test("run replaces the timeline running, and stop ends it", () => {
    const m = started();
    deckAt(m, "[Channel1]", 0);
    const lane = (key) => ({group: "[Channel1]", key, clock: "[Channel1]", kind: "ramp", points: [[10, 1], [20, 0]]});
    m.request({id: 1, op: "run", timeline: {id: "a", lanes: [lane("volume")]}});
    m.request({id: 2, op: "run", timeline: {id: "b", lanes: [lane("pregain")]}});
    deckAt(m, "[Channel1]", 15);
    assert.equal(m.get("[Channel1]", "volume"), 0);
    assert.equal(m.get("[Channel1]", "pregain"), 0.5);
    m.request({id: 3, op: "stop"});
    assert.equal(m.reply(3).stopped, "b");
    deckAt(m, "[Channel1]", 18);
    assert.equal(m.get("[Channel1]", "pregain"), 0.5);
});

test("a bad lane is refused and leaves the timeline running", () => {
    const m = started();
    deckAt(m, "[Channel1]", 0);
    m.request({id: 1, op: "run", timeline: {id: "a", lanes: [
        {group: "[Channel1]", key: "volume", clock: "[Channel1]", kind: "ramp", points: [[10, 1], [20, 0]]}]}});
    m.request({id: 2, op: "run", timeline: {id: "b", lanes: [
        {group: "[Channel1]", key: "volume", clock: "[Channel1]", kind: "ramp", points: [[20, 1], [10, 0]]}]}});
    assert.match(m.reply(2).error, /lane 0: points must be in time order/);
    m.request({id: 3, op: "state"});
    assert.equal(m.reply(3).timeline.id, "a");
});

test("a request split over many frames is joined", () => {
    const m = started();
    deckAt(m, "[Channel1]", 0);
    const points = Array.from({length: 400}, (_, i) => [i * 0.5, i / 400]);
    const message = {id: 1, op: "run", timeline: {id: "big", lanes: [
        {group: "[Channel1]", key: "volume", clock: "[Channel1]", kind: "ramp", points}]}};
    const frames = encode(message, 77);
    assert.ok(frames.length > 3);
    for (const f of frames.reverse()) {
        m.B.incomingData(f, f.length);
    }
    assert.equal(m.reply(1).lanes, 1);
});
