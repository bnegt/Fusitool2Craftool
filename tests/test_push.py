"""The live-import protocol, against a fake camcraft-cli that speaks MCP."""

import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from fusitool2craftool.push import PushError, push

FAKE = textwrap.dedent(
    """\
    #!{python}
    import json, sys
    log = open({log!r}, "a")
    log.write(" ".join(sys.argv[1:]) + "\\n")
    for line in sys.stdin:
        msg = json.loads(line)
        log.write(line)
        log.flush()
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            result = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "fake"}}}}
        else:
            args = msg["params"]["arguments"]
            refuse = {refuse!r}
            result = {{"isError": refuse, "content": [{{"type": "text",
                      "text": "refused: bad tool" if refuse else "added 3 tools, mode " + args["mode"]}}]}}
        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": result}}), flush=True)
    """
)


class PushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.lib = self.dir / "lib.camtools"
        self.lib.write_text("{}")
        self.log = self.dir / "log.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, refuse=False):
        path = self.dir / "camcraft-cli"
        path.write_text(FAKE.format(python=sys.executable, log=str(self.log), refuse=refuse))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_push_to_running_window(self):
        out = push(self.lib, 7777, mode="update", cli=self.fake())
        self.assertEqual(out, "added 3 tools, mode update")
        log = self.log.read_text()
        self.assertIn("mcp --connect 127.0.0.1:7777", log)
        self.assertIn('"name": "import_tool_library"', log)
        self.assertIn(str(self.lib.resolve()), log)
        self.assertIn("notifications/initialized", log)

    def test_headless_check(self):
        push(self.lib, None, mode="add", cli=self.fake())
        self.assertTrue(self.log.read_text().startswith("mcp\n"))

    def test_refusal_is_an_error(self):
        with self.assertRaisesRegex(PushError, "bad tool"):
            push(self.lib, 7777, cli=self.fake(refuse=True))

    def test_missing_cli(self):
        with self.assertRaisesRegex(PushError, "not found"):
            push(self.lib, 7777, cli=str(self.dir / "nope"))


if __name__ == "__main__":
    unittest.main()
