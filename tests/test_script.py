"""The controller script, run in Node against the fake Mixxx in tests/js. Nothing here
replaces trying it in Mixxx; these skip without node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rhinestone.mixxx import frames

HERE = Path(__file__).parent
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="needs node")


def test_script_tests_pass():
    result = subprocess.run([node, "--test", str(HERE / "js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# Feeds frames from argv to the script and prints every frame it sends back
RUNNER = """
import {fakeMixxx} from "./fake_mixxx.mjs";
const m = fakeMixxx();
m.B.init();
for (const f of JSON.parse(process.argv[2])) m.B.incomingData(f, f.length);
console.log(JSON.stringify(m.sent));
"""


def test_python_and_the_script_read_each_others_frames(tmp_path):
    runner = HERE / "js" / "_runner.mjs"
    runner.write_text(RUNNER)
    try:
        long_error_op = "x" * 2500 + " é"
        sent = [f for i, op in enumerate(["hello", long_error_op])
                for f in frames.encode({"id": i + 1, "op": op}, 4000 + i)]
        out = subprocess.run([node, str(runner), json.dumps(sent)], capture_output=True, text=True,
                             check=True).stdout
    finally:
        runner.unlink()
    r = frames.ReplyReader()
    got = [m for f in json.loads(out) if (m := r.feed(f)) is not None]
    hello, first, second = got
    assert hello["event"] == "hello"
    assert first == {"id": 1, "session": hello["session"], "ok": True, "version": 1, "decks": 4,
                     "preview_decks": 1}
    assert second["ok"] is False and long_error_op in second["error"]
