// A fake Mixxx for the bridge script: controls, connections, timers on a clock the test
// moves, and a MIDI output that keeps what the script sends. Players load and eject the
// way 2.6's do, slowly enough that the script has to wait for them.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import {fileURLToPath} from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const SCRIPT = path.join(here, "../../rhinestone/mixxx/mapping/Rhinestone-Bridge.js");

export const LOAD_MS = 150;

export function encode(message, seq = 0, direction = 0x01, chunk = 960) {
    const text = JSON.stringify(message);
    const parts = Math.max(1, Math.ceil(text.length / chunk));
    const out = [];
    for (let p = 0; p < parts; p++) {
        const body = [...text.slice(p * chunk, (p + 1) * chunk)].map((c) => c.charCodeAt(0));
        out.push([0xF0, 0x7D, 0x52, direction, seq >> 7, seq & 0x7F, p, parts, ...body, 0xF7]);
    }
    return out;
}

export function fakeMixxx({decks = 4} = {}) {
    let now = 0;
    let timers = [];
    let nextTimer = 1;
    const values = new Map();
    const connections = [];
    const sets = [];
    const sent = [];
    // What LoadSelectedTrack loads: a track, null for nothing selected, or "missing"
    const mixxx = {selected: {duration: 200, file_bpm: 126}};

    const key = (g, k) => g + "," + k;
    const get = (g, k) => values.get(key(g, k)) ?? 0;
    const put = (g, k, v) => {
        values.set(key(g, k), v);
        for (const c of connections) {
            if (c.live && c.group === g && c.key === k) {
                c.fn(v, g, k);
            }
        }
    };
    const later = (ms, fn) => timers.push({id: nextTimer++, at: now + ms, fn, oneShot: true, ms});

    const engine = {
        getValue: get,
        setValue(g, k, v) {
            sets.push([g, k, v]);
            put(g, k, v);
            if (k === "LoadSelectedTrack" && v > 0) {
                const t = mixxx.selected;
                if (t && t !== "missing") {
                    later(LOAD_MS, () => {
                        put(g, "duration", t.duration);
                        put(g, "file_bpm", t.file_bpm);
                        put(g, "track_loaded", 1);
                    });
                }
            }
            if (g === "[Library]" && k === "sort_column_toggle") {
                // A new column sorts ascending, the current one flips
                if (get(g, "sort_column") === v) {
                    put(g, "sort_order", get(g, "sort_order") ? 0 : 1);
                } else {
                    put(g, "sort_column", v);
                    put(g, "sort_order", 0);
                }
            }
            if (k === "eject" && v > 0 && get(g, "track_loaded") && !get(g, "play")) {
                later(30, () => {
                    put(g, "track_loaded", 0);
                    put(g, "duration", 0);
                });
            }
        },
        makeConnection(group, k, fn) {
            const c = {group, key: k, fn, live: true, disconnect() {
                c.live = false;
            }};
            connections.push(c);
            return c;
        },
        beginTimer(ms, fn, oneShot) {
            const id = nextTimer++;
            timers.push({id, at: now + ms, fn, oneShot, ms});
            return id;
        },
        stopTimer(id) {
            timers = timers.filter((t) => t.id !== id);
        },
    };
    const midi = {
        sendShortMsg: (status, a, b) => sent.push([status, a, b]),
        sendSysexMsg: () => {
            throw new Error("the script must not send SysEx, which jams Mixxx's output");
        },
    };
    const context = vm.createContext({engine, midi, console, Date, Math, JSON, Object, String});
    vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), context, {filename: SCRIPT});
    const B = context.RhinestoneBridge;
    B.now = () => now;
    values.set(key("[App]", "num_decks"), decks);
    values.set(key("[App]", "num_preview_decks"), 1);

    return {
        B, mixxx, sets, sent, connections, get, put,
        advance(ms) {
            const end = now + ms;
            for (;;) {
                timers.sort((a, b) => a.at - b.at);
                const t = timers[0];
                if (!t || t.at > end) {
                    break;
                }
                now = t.at;
                timers.shift();
                if (!t.oneShot) {
                    timers.push({...t, at: now + t.ms});
                }
                t.fn();
            }
            now = end;
        },
        // The messages the script sent, joined from their short messages, with each
        // end's sequence number and checksum checked
        messages() {
            const out = [];
            let text = null;
            let seq = null;
            for (const [status, a, b] of sent) {
                if (status === 0xB0) {
                    text = "";
                    seq = a;
                } else if (status === 0xB1) {
                    text += String.fromCharCode(a, b);
                } else if (status === 0xB2) {
                    text += String.fromCharCode(a);
                } else if (status === 0xB3) {
                    const sum = [...text].reduce((s, c) => s + c.charCodeAt(0), 0) & 0x7F;
                    if (a !== seq || b !== sum) {
                        throw new Error("a reply's end does not match its start or checksum");
                    }
                    out.push(JSON.parse(text));
                    text = null;
                }
            }
            return out;
        },
        request(message, seq = 0) {
            for (const f of encode(message, seq)) {
                B.incomingData(f, f.length);
            }
        },
        reply(id) {
            return this.messages().find((m) => m.id === id);
        },
    };
}
