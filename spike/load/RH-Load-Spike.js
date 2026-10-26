// Can a controller script load chosen playlist rows onto decks in stock Mixxx 2.6?
//
// A script cannot name a track. It can only move the library's selection and load
// whatever is selected. Selection moves are relative and wrap at both ends
// (WTrackTableView::moveSelection), so a known starting row plus the row count is
// enough to reach any row: move by (target - current) mod ROWS.
//
// 2.6 has no engine.getPlayer(), so a script cannot read a loaded track's title.
// Each load is logged with its length and BPM instead, to compare by eye with the
// playlist's Duration and BPM columns. In Rhinestone Heart the server would compare
// the length with Mixxx's database.
//
// Before enabling: open a playlist of ROWS tracks with no song in it twice, and click
// its first row. Every step is logged with the prefix "RH spike".

var RHLoadSpike = {};

// Rows in the test playlist
RHLoadSpike.ROWS = 12;

// Rows to load, from 0, in order. Row 11 is added to the database while the playlist
// is open, so it only exists for the script if the refresh brings it in
RHLoadSpike.TARGETS = [11, 3, 9, 0, 10];

// Seconds before the first step, to put another app in front if testing without focus
RHLoadSpike.START_DELAY = 10;

// Seconds between steps. A load is asynchronous, and duration reads 0 until it lands
RHLoadSpike.STEP_DELAY = 3;

RHLoadSpike.log = function(text) {
    console.log("RH spike: " + text);
};

RHLoadSpike.init = function() {
    RHLoadSpike.current = 0;
    RHLoadSpike.step = 0;
    RHLoadSpike.log("loaded, first step in " + RHLoadSpike.START_DELAY + " s, assuming row 0 is selected");
    engine.beginTimer(RHLoadSpike.START_DELAY * 1000, RHLoadSpike.refresh, true);
};

// An open playlist does not see rows written to the database by another program
// until it queries again, which sorting does. Flipping the sort order and back
// re-sorts twice and ends where it started. The view keeps its selection across a
// sort, by playlist position. sort_column is left alone: writing it also flips the
// order when the column is unchanged (LibraryControl::slotSortColumnToggle)
RHLoadSpike.refresh = function() {
    RHLoadSpike.log("sort_column " + engine.getValue("[Library]", "sort_column") +
        " (23 is #), sort_order " + engine.getValue("[Library]", "sort_order") + " (0 ascending)");
    var order = engine.getValue("[Library]", "sort_order");
    engine.setValue("[Library]", "sort_order", order ? 0 : 1);
    engine.beginTimer(1000, function() {
        engine.setValue("[Library]", "sort_order", order);
        RHLoadSpike.log("refreshed");
        engine.beginTimer(1000, RHLoadSpike.preview, true);
    }, true);
};

// The anchor for the real server: load the selected row into the preview deck, which
// no one hears, and read its length to learn which row is selected. An empty
// selection loads nothing, and is the one state a move lands on predictably: the
// first step down from it selects row 0
RHLoadSpike.preview = function() {
    RHLoadSpike.log("focused_widget " + engine.getValue("[Library]", "focused_widget") +
        " (0 none, 1 search, 2 sidebar, 3 tracks)");
    engine.setValue("[PreviewDeck1]", "LoadSelectedTrack", 1);
    engine.beginTimer(RHLoadSpike.STEP_DELAY * 1000, function() {
        if (engine.getValue("[PreviewDeck1]", "track_loaded")) {
            RHLoadSpike.report("[PreviewDeck1]", "selected");
            RHLoadSpike.log("check that length against the playlist; this run assumes it is row 0");
        } else {
            RHLoadSpike.log("nothing selected, so one step down selects row 0");
            engine.setValue("[Playlist]", "SelectTrackKnob", 1);
        }
        RHLoadSpike.next();
    }, true);
};

RHLoadSpike.next = function() {
    if (RHLoadSpike.step >= RHLoadSpike.TARGETS.length) {
        RHLoadSpike.log("done");
        return;
    }
    var target = RHLoadSpike.TARGETS[RHLoadSpike.step];
    var deck = "[Channel" + (RHLoadSpike.step % 2 + 1) + "]";
    var rows = RHLoadSpike.ROWS;
    var delta = ((target - RHLoadSpike.current) % rows + rows) % rows;
    if (delta !== 0) {
        engine.setValue("[Playlist]", "SelectTrackKnob", delta);
    }
    RHLoadSpike.current = target;
    engine.setValue(deck, "LoadSelectedTrack", 1);
    RHLoadSpike.step += 1;
    engine.beginTimer(RHLoadSpike.STEP_DELAY * 1000, function() {
        RHLoadSpike.report(deck, target);
        RHLoadSpike.next();
    }, true);
};

RHLoadSpike.report = function(group, row) {
    var seconds = engine.getValue(group, "duration");
    var minutes = Math.floor(seconds / 60);
    var rest = Math.round(seconds - minutes * 60);
    RHLoadSpike.log("row " + row + " into " + group + ": " + minutes + ":" +
        (rest < 10 ? "0" : "") + rest + ", " + engine.getValue(group, "file_bpm").toFixed(2) +
        " BPM, track_loaded " + engine.getValue(group, "track_loaded"));
};

RHLoadSpike.shutdown = function() {
    RHLoadSpike.log("shut down");
};
