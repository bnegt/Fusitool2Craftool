"""Watch CamCraft for the tool types this converter is waiting on.

Runs first in the suite (alphabetical order). It asks GitHub whether the
CamCraft issues that block tapered ball mills and roundover bits are still
open, and whether CamCraft's tool kinds changed. When one fails, CamCraft
may now support tools we skip: see
https://github.com/bnegt/Fusitool2Craftool/issues/1

Needs network access; skipped when offline, when GitHub rate-limits, or with
F2C_OFFLINE=1. Set GITHUB_TOKEN to raise the rate limit.
"""

import json
import os
import re
import unittest
import urllib.error
import urllib.request

REPO = "NeoTech/Camcraft"
TRACKING = "https://github.com/bnegt/Fusitool2Craftool/issues/1"

# CamCraft issue -> the Fusion tools we skip until it is solved.
BLOCKING_ISSUES = {
    895: "tapered ball mills / tapered mills, and V-bits with a tip diameter",
    896: "roundover bits (and other form cutters)",
}

# CamCraft's ToolKind variants as of main @ 7a626a7f (v0.25.0).
KNOWN_KINDS = {
    "EndMill", "BallNose", "BullNose", "Drill", "Tap", "Bore", "Turning",
    "Groove", "Thread", "Form", "Taper", "Probe", "Torch", "Waterjet",
    "Electrode", "ThreadMill", "ThreadMillSingle", "Wire", "TSlot",
    "Keyseat", "Dovetail", "Lollipop",
}


def _get(url: str, raw: bool = False):
    headers = {
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "User-Agent": "fusitool2craftool-tests",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8")
    return body if raw else json.loads(body)


def _fetch(url: str, raw: bool = False):
    try:
        return _get(url, raw)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise unittest.SkipTest(f"GitHub rate limit ({e.code}); set GITHUB_TOKEN")
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise unittest.SkipTest(f"no network: {e}")


@unittest.skipIf(os.environ.get("F2C_OFFLINE"), "F2C_OFFLINE set")
class CamCraftUpstreamTests(unittest.TestCase):
    def test_blocking_issues_still_open(self):
        solved = []
        for number, tools in BLOCKING_ISSUES.items():
            issue = _fetch(f"https://api.github.com/repos/{REPO}/issues/{number}")
            if issue.get("state") != "open":
                solved.append(
                    f"CamCraft #{number} '{issue.get('title')}' is {issue.get('state')} "
                    f"(closed {issue.get('closed_at')}): {tools} may now be supported"
                )
        if solved:
            self.fail(
                "\n".join(solved)
                + f"\nUpdate the converter, then remove the issue from BLOCKING_ISSUES. See {TRACKING}"
            )

    def test_tool_kinds_unchanged(self):
        source = _fetch(
            f"https://api.github.com/repos/{REPO}/contents/crates/tools/src/tools.rs", raw=True
        )
        m = re.search(r"pub enum ToolKind\s*\{(.*?)\n\}", source, re.S)
        self.assertIsNotNone(m, "ToolKind enum not found in CamCraft's tools.rs")
        kinds = set(re.findall(r"^\s{4}([A-Z]\w*)\s*,", m.group(1), re.M))
        added, removed = sorted(kinds - KNOWN_KINDS), sorted(KNOWN_KINDS - kinds)
        if added or removed:
            self.fail(
                f"CamCraft tool kinds changed - added: {added or 'none'}, removed: {removed or 'none'}.\n"
                f"New kinds may cover skipped Fusion tools; update convert.py and KNOWN_KINDS. See {TRACKING}"
            )


if __name__ == "__main__":
    unittest.main()
