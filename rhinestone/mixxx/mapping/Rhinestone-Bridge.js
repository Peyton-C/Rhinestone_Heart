// Rhinestone Heart's side of the bridge, running inside Mixxx as a controller script.
//
// It stays thin. The server decides which row to load, works out every control value
// and turns bars into seconds; this script only carries out what it is told and holds
// the timeline, because timing a ramp on Mixxx's side never waits on the server.
// docs/bridge.md has the wire format and the operations.

var RhinestoneBridge = {};

RhinestoneBridge.VERSION = 1;

RhinestoneBridge.TO_MIXXX = 0x01;

// A message to the server goes as Control Change messages, never SysEx: Mixxx 2.6 on
// macOS jams its output to an IAC bus for good once SysEx goes faster than about one
// message a second, or one is long, while short messages go through at full speed.
// frames.py has the layout
RhinestoneBridge.START = 0xB0;
RhinestoneBridge.PAIR = 0xB1;
RhinestoneBridge.SINGLE = 0xB2;
RhinestoneBridge.END = 0xB3;

// A deck whose track length moves by more than this has a different track on it than
// the timeline was written for, so its lanes stop
RhinestoneBridge.CLOCK_TOLERANCE = 0.01;

// Eject on one deck twice inside 500 ms and Mixxx reloads the track ejected before
// (kUnreplaceDelay in basetrackplayer.h), so ejects on a deck are kept further apart
RhinestoneBridge.EJECT_GAP_MS = 600;

RhinestoneBridge.POLL_MS = 20;

// After a track counts as loaded, Mixxx still seeks it to its cue point, a moment later
// on the audio thread. A load answers only after this, so a position set right after
// it is not overwritten: Take It Off's cue sits three beats in, past its count-in, and a
// jump to its bar 0 sent on the heels of the load was undone
RhinestoneBridge.SETTLE_MS = 250;

// Replies kept to send again. The IAC bus loses a short message now and then under load,
// which loses its whole reply, so the server asks for it again instead of repeating a
// request that may have done something, such as a load
RhinestoneBridge.KEEP = 16;

RhinestoneBridge.now = function() {
    return Date.now();
};

RhinestoneBridge.init = function() {
    var B = RhinestoneBridge;
    B.session = Math.floor(Math.random() * 0x7fffffff).toString(36);
    B.seq = 0;
    B.partial = {};
    B.lastEject = {};
    B.timeline = null;
    B.kept = {};
    B.keptOrder = [];
    // Told unasked, so a server that is already running learns Mixxx restarted
    B.send({event: "hello", session: B.session, version: B.VERSION});
};

RhinestoneBridge.shutdown = function() {
    RhinestoneBridge.clearTimeline();
};

// ------------------------------------------------------------------ messages

RhinestoneBridge.incomingData = function(data, length) {
    var B = RhinestoneBridge;
    // Mixxx passes an ArrayBuffer
    var f = Object.prototype.toString.call(data) === "[object ArrayBuffer]" ? new Uint8Array(data) : data;
    var n = f.length;
    if (n < 9 || f[0] !== 0xF0 || f[1] !== 0x7D || f[2] !== 0x52 || f[n - 1] !== 0xF7 ||
            f[3] !== B.TO_MIXXX) {
        return;
    }
    var seq = (f[4] << 7) | f[5];
    var part = f[6];
    var parts = f[7];
    if (part >= parts) {
        return;
    }
    var chunks = B.partial[seq];
    if (!chunks || chunks.length !== parts) {
        chunks = B.partial[seq] = new Array(parts);
        var keys = Object.keys(B.partial);
        if (keys.length > 8) {
            delete B.partial[keys[0]];
        }
    }
    var text = "";
    for (var i = 8; i < n - 1; i++) {
        text += String.fromCharCode(f[i]);
    }
    chunks[part] = text;
    for (var j = 0; j < parts; j++) {
        if (chunks[j] === undefined) {
            return;
        }
    }
    delete B.partial[seq];
    var message;
    try {
        message = JSON.parse(chunks.join(""));
    } catch (e) {
        B.send({id: null, session: B.session, ok: false, error: "a message was not JSON: " + e});
        return;
    }
    B.handle(message);
};

RhinestoneBridge.send = function(message) {
    var B = RhinestoneBridge;
    var text = JSON.stringify(message).replace(/[\u007f-￿]/g, function(c) {
        return "\\u" + ("000" + c.charCodeAt(0).toString(16)).slice(-4);
    });
    var seq = B.seq;
    B.seq = (B.seq + 1) & 0x7F;
    var sum = 0;
    midi.sendShortMsg(B.START, seq, 0);
    for (var i = 0; i + 1 < text.length; i += 2) {
        var a = text.charCodeAt(i);
        var b = text.charCodeAt(i + 1);
        sum += a + b;
        midi.sendShortMsg(B.PAIR, a, b);
    }
    if (text.length % 2) {
        var c = text.charCodeAt(text.length - 1);
        sum += c;
        midi.sendShortMsg(B.SINGLE, c, 0);
    }
    midi.sendShortMsg(B.END, seq, sum & 0x7F);
};

// ----------------------------------------------------------------- operations

RhinestoneBridge.handle = function(message) {
    var B = RhinestoneBridge;
    var op = B.ops[message.op];
    if (!op) {
        B.fail(message, "unknown op " + JSON.stringify(message.op) + ", the ops are " +
            Object.keys(B.ops).join(", "));
        return;
    }
    try {
        op(message);
    } catch (e) {
        B.fail(message, String(e));
    }
};

RhinestoneBridge.reply = function(message, fields) {
    var out = {id: message.id, session: RhinestoneBridge.session, ok: true};
    for (var k in fields) {
        out[k] = fields[k];
    }
    RhinestoneBridge.answer(out);
};

RhinestoneBridge.fail = function(message, error) {
    RhinestoneBridge.answer({id: message.id, session: RhinestoneBridge.session, ok: false, error: error});
};

RhinestoneBridge.answer = function(out) {
    var B = RhinestoneBridge;
    if (out.id !== null && out.id !== undefined) {
        if (!(out.id in B.kept)) {
            B.keptOrder.push(out.id);
        }
        B.kept[out.id] = out;
        while (B.keptOrder.length > B.KEEP) {
            delete B.kept[B.keptOrder.shift()];
        }
    }
    B.send(out);
};

// Rounds for the wire, where a float's 17 digits cost 9 short messages
RhinestoneBridge.round = function(x, places) {
    var f = Math.pow(10, places);
    return Math.round(x * f) / f;
};

RhinestoneBridge.after = function(ms, fn) {
    engine.beginTimer(Math.max(RhinestoneBridge.POLL_MS, Math.round(ms)), fn, true);
};

// Calls done(true) once test() holds, or done(false) after timeoutMs
RhinestoneBridge.waitFor = function(test, timeoutMs, done) {
    var B = RhinestoneBridge;
    var deadline = B.now() + timeoutMs;
    var check = function() {
        if (test()) {
            done(true);
        } else if (B.now() >= deadline) {
            done(false);
        } else {
            B.after(B.POLL_MS, check);
        }
    };
    check();
};

RhinestoneBridge.isDeck = function(group) {
    return /^\[Channel\d+\]$/.test(group);
};

RhinestoneBridge.isPlayer = function(group) {
    return RhinestoneBridge.isDeck(group) || /^\[PreviewDeck\d+\]$/.test(group);
};

RhinestoneBridge.player = function(group) {
    var v = function(key, places) {
        var x = engine.getValue(group, key);
        return places === undefined ? x : RhinestoneBridge.round(x, places);
    };
    // A play position to 8 places is a twentieth of a millisecond in a ten-minute track
    var out = {group: group, track_loaded: v("track_loaded"), duration: v("duration", 4),
        file_bpm: v("file_bpm", 4), play: v("play"), playposition: v("playposition", 8)};
    if (RhinestoneBridge.isDeck(group)) {
        out.bpm = v("bpm", 4);
        out.sync_enabled = v("sync_enabled");
        out.stem_count = v("stem_count");
    }
    return out;
};

RhinestoneBridge.ops = {};

// Sends a kept reply again, under its own id, without doing its request again
RhinestoneBridge.ops.resend = function(m) {
    var r = RhinestoneBridge.kept[m.of];
    if (r) {
        RhinestoneBridge.send(r);
    } else {
        RhinestoneBridge.fail(m, "no reply kept for " + m.of);
    }
};

RhinestoneBridge.ops.hello = function(m) {
    RhinestoneBridge.reply(m, {version: RhinestoneBridge.VERSION,
        decks: engine.getValue("[App]", "num_decks"),
        preview_decks: engine.getValue("[App]", "num_preview_decks")});
};

RhinestoneBridge.ops.state = function(m) {
    var B = RhinestoneBridge;
    var decks = [];
    for (var i = 1; i <= engine.getValue("[App]", "num_decks"); i++) {
        decks.push(B.player("[Channel" + i + "]"));
    }
    var t = B.timeline;
    B.reply(m, {
        decks: decks,
        preview: B.player("[PreviewDeck1]"),
        library: {sort_column: engine.getValue("[Library]", "sort_column"),
            sort_order: engine.getValue("[Library]", "sort_order")},
        timeline: t ? {id: t.id, lanes_left: t.lanes.filter(function(l) {
            return !l.done;
        }).length} : null,
    });
};

// Moves the library's selection by delta rows. It wraps at both ends, and from no
// selection the first step down selects the first row (WTrackTableView::moveSelection)
RhinestoneBridge.ops.move = function(m) {
    var delta = Math.round(m.delta);
    if (delta) {
        engine.setValue("[Playlist]", "SelectTrackKnob", delta);
    }
    RhinestoneBridge.reply(m, {});
};

// Makes the open playlist query the database again, for rows the server added while it
// was open. Sorting queries it, and keeps the selection by playlist position. Flipping
// the order and back sorts twice and ends where it started. sort_column is left alone:
// writing it flips the order too when the column is unchanged
RhinestoneBridge.ops.refresh = function(m) {
    var B = RhinestoneBridge;
    var order = engine.getValue("[Library]", "sort_order");
    engine.setValue("[Library]", "sort_order", order ? 0 : 1);
    B.after(200, function() {
        engine.setValue("[Library]", "sort_order", order);
        B.after(200, function() {
            B.reply(m, {sort_column: engine.getValue("[Library]", "sort_column"),
                sort_order: engine.getValue("[Library]", "sort_order")});
        });
    });
};

// Sorts the open view by a column, ascending. sort_column_toggle takes a column that is
// not the current one ascending, and flips the order of the current one, so the order
// is put right after it (LibraryControl::slotSortColumnToggle)
RhinestoneBridge.ops.sort = function(m) {
    var B = RhinestoneBridge;
    engine.setValue("[Library]", "sort_column_toggle", m.column);
    B.after(200, function() {
        if (engine.getValue("[Library]", "sort_order")) {
            engine.setValue("[Library]", "sort_order", 0);
        }
        B.after(200, function() {
            B.reply(m, {sort_column: engine.getValue("[Library]", "sort_column"),
                sort_order: engine.getValue("[Library]", "sort_order")});
        });
    });
};

// Loads the selected row onto a deck or the preview deck. The player is emptied first,
// so a load that fails, as a missing file's does, leaves track_loaded at 0 and not at
// the old track's 1
RhinestoneBridge.ops.load = function(m) {
    var B = RhinestoneBridge;
    var g = m.group;
    if (!B.isPlayer(g)) {
        B.fail(m, "group must be a deck or preview deck, not " + JSON.stringify(g));
        return;
    }
    if (engine.getValue(g, "play")) {
        if (B.isDeck(g)) {
            B.fail(m, g + " is playing");
            return;
        }
        // No one hears the preview deck, and Mixxx will not eject while it plays
        engine.setValue(g, "play", 0);
    }
    var timeout = m.timeout_ms || 5000;
    var load = function() {
        engine.setValue(g, "LoadSelectedTrack", 1);
        B.waitFor(function() {
            return engine.getValue(g, "track_loaded") && engine.getValue(g, "duration") > 0;
        }, timeout, function(loaded) {
            B.after(loaded ? B.SETTLE_MS : 0, function() {
                var p = B.player(g);
                B.reply(m, {loaded: loaded, duration: p.duration, file_bpm: p.file_bpm});
            });
        });
    };
    if (!engine.getValue(g, "track_loaded")) {
        // An eject here would reload the last track ejected
        load();
        return;
    }
    var wait = (B.lastEject[g] || 0) + B.EJECT_GAP_MS - B.now();
    var eject = function() {
        B.lastEject[g] = B.now();
        engine.setValue(g, "eject", 1);
        engine.setValue(g, "eject", 0);
        B.waitFor(function() {
            return !engine.getValue(g, "track_loaded");
        }, timeout, function(empty) {
            if (empty) {
                load();
            } else {
                B.fail(m, g + " did not empty");
            }
        });
    };
    if (wait > 0) {
        B.after(wait, eject);
    } else {
        eject();
    }
};

// ------------------------------------------------------------------- timeline

// Runs a timeline, replacing any already running. Each lane sets one control from the
// play position of its clock deck, in seconds into that deck's track, so it follows
// the music and not the wall clock: a paused deck holds its lanes. A ramp lane is set
// along straight lines between its points, from its first point on, and a set lane
// sets each point's value once as the clock passes it
RhinestoneBridge.ops.run = function(m) {
    var B = RhinestoneBridge;
    var t = m.timeline || {};
    var lanes = t.lanes || [];
    for (var i = 0; i < lanes.length; i++) {
        var l = lanes[i];
        var where = "lane " + i + ": ";
        if (!l.group || !l.key || !B.isDeck(l.clock)) {
            throw where + "needs a group, a key and a deck as its clock";
        }
        if (l.kind !== "ramp" && l.kind !== "set") {
            throw where + "kind must be ramp or set";
        }
        if (!l.points || !l.points.length) {
            throw where + "has no points";
        }
        for (var p = 1; p < l.points.length; p++) {
            if (l.points[p][0] < l.points[p - 1][0]) {
                throw where + "points must be in time order";
            }
        }
    }
    B.clearTimeline();
    var timeline = {id: t.id, clocks: t.clocks || {}, connections: [], warned: {}, lanes: []};
    var clocks = {};
    lanes.forEach(function(l) {
        timeline.lanes.push({group: l.group, key: l.key, clock: l.clock, kind: l.kind,
            points: l.points, fired: 0, last: null, done: false});
        clocks[l.clock] = true;
    });
    B.timeline = timeline;
    Object.keys(clocks).forEach(function(clock) {
        timeline.connections.push(engine.makeConnection(clock, "playposition", function() {
            B.tick(clock);
        }));
    });
    B.reply(m, {timeline: t.id, lanes: timeline.lanes.length});
    Object.keys(clocks).forEach(B.tick);
};

RhinestoneBridge.ops.stop = function(m) {
    var t = RhinestoneBridge.timeline;
    RhinestoneBridge.clearTimeline();
    RhinestoneBridge.reply(m, {stopped: t ? t.id : null});
};

RhinestoneBridge.clearTimeline = function() {
    var t = RhinestoneBridge.timeline;
    if (t) {
        t.connections.forEach(function(c) {
            c.disconnect();
        });
    }
    RhinestoneBridge.timeline = null;
};

RhinestoneBridge.tick = function(clock) {
    var B = RhinestoneBridge;
    var t = B.timeline;
    if (!t || B.ticking) {
        // A jump below moves the clock, which can call back in here; the tick under
        // way reads the clock again after its jumps anyway
        return;
    }
    B.ticking = true;
    try {
        B.tickLanes(t, clock);
    } finally {
        B.ticking = false;
    }
};

RhinestoneBridge.tickLanes = function(t, clock) {
    var B = RhinestoneBridge;
    var duration = engine.getValue(clock, "duration");
    var expected = t.clocks[clock];
    if (expected !== undefined && Math.abs(duration - expected) > B.CLOCK_TOLERANCE) {
        if (!t.warned[clock]) {
            t.warned[clock] = true;
            B.send({event: "clock_changed", session: B.session, timeline: t.id, clock: clock,
                expected: expected, duration: duration});
        }
        return;
    }
    var now = function() {
        return engine.getValue(clock, "playposition") * duration;
    };
    // Lanes that move the clock deck itself go first, and the rest read the clock after
    // them, so a jump to a bar is not followed by lanes judged by where the deck was
    var jumps = function(lane) {
        return lane.group === clock && lane.key === "playposition";
    };
    [true, false].forEach(function(first) {
        var at = now();
        t.lanes.forEach(function(lane) {
            if (lane.clock === clock && !lane.done && jumps(lane) === first) {
                B.advance(lane, at);
            }
        });
    });
    var left = t.lanes.filter(function(lane) {
        return !lane.done;
    }).length;
    if (!left) {
        B.clearTimeline();
        B.send({event: "timeline_done", session: B.session, timeline: t.id});
    }
};

RhinestoneBridge.advance = function(lane, now) {
    var pts = lane.points;
    if (lane.kind === "set") {
        while (lane.fired < pts.length && pts[lane.fired][0] <= now) {
            engine.setValue(lane.group, lane.key, pts[lane.fired][1]);
            lane.fired++;
        }
        lane.done = lane.fired === pts.length;
        return;
    }
    if (now < pts[0][0]) {
        return;
    }
    var last = pts[pts.length - 1];
    var value;
    if (now >= last[0]) {
        value = last[1];
        lane.done = true;
    } else {
        var i = 0;
        while (pts[i + 1][0] <= now) {
            i++;
        }
        var a = pts[i];
        var b = pts[i + 1];
        value = a[1] + (b[1] - a[1]) * (now - a[0]) / (b[0] - a[0]);
    }
    if (value !== lane.last) {
        engine.setValue(lane.group, lane.key, value);
        lane.last = value;
    }
};
