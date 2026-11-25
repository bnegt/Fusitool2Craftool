"""Watch CamCraft for the tool types this converter is waiting on.

Runs first in the suite (alphabetical order). It asks GitHub whether the
CamCraft issues that block the tool types we skip (tapered, roundover,
reamer, counterbore, corner radius dovetail, left-hand) are still open, and whether
CamCraft's tool kinds changed. When one fails, CamCraft may now support tools
we skip: see
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
    897: "reamers and counterbores (no CamCraft kind)",
    3368: "tapered mills (tip form: sharp, flat, ball)",
    4004: "dovetail mills with a corner radius (our #29)",
    4031: "left-hand drills and thread mills (our #41)",
}

# #14: stand-alone Fusion holders wait for a user holder library in a
# *release* (#3645). The release's reader is the truth (main runs ahead), so
# the watch reads the latest release. .camtools versions 5 (drawn cutters,
# NeoTech/Camcraft#3904) and 6 (taper tip forms, #3368) are on main and are
# not the holder library.
HOLDER_LIBRARY_ISSUE = 3645
HOLDER_TRACKING = "https://github.com/bnegt/Fusitool2Craftool/issues/14"
RELEASED_CAMTOOLS_VERSION = 4  # v0.26.0: VERSION 1 .. HOLDER_REF_VERSION 4

# A closed issue counts once the latest release's source holds its marker.
# For #4004 and #4031 that is a guess at how the fix will look: today undercut.rs
# only writes ``corner_r: 0.0``, and tools.rs has no ``cutter_hand``. If one
# closes and the skip stays, look.
RELEASE_MARKERS = {
    3368: ("crates/app/src/tool_library.rs", "TIP_FORM_VERSION"),
    4004: ("crates/tools/src/undercut.rs", "tool.corner_r"),
    4031: ("crates/tools/src/tools.rs", "cutter_hand"),
}

# CamCraft's ToolKind variants as of v0.26.0.
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
    def test_holder_library_not_released(self):
        release = _fetch(f"https://api.github.com/repos/{REPO}/releases/latest")
        tag = release.get("tag_name")
        src = _fetch(f"https://api.github.com/repos/{REPO}/contents/crates/app/src/tool_library.rs?ref={tag}",
                     raw=True)
        versions = [int(v) for v in re.findall(r"const \w+_VERSION: u\d+ = (\d+);", src)]
        newest = max(versions, default=0)
        if newest > RELEASED_CAMTOOLS_VERSION:
            self.fail(
                f"CamCraft {tag} reads .camtools version {newest} (was {RELEASED_CAMTOOLS_VERSION}): "
                f"5 is drawn cutters (the neck of a single-form thread mill could be one), "
                f"6 is taper tip forms (#10: --unreleased-tapers can become the default), anything newer may be the user holder library "
                f"(#{HOLDER_LIBRARY_ISSUE}, see {HOLDER_TRACKING}). "
                f"Convert what is new, then raise RELEASED_CAMTOOLS_VERSION."
            )
        issue = _fetch(f"https://api.github.com/repos/{REPO}/issues/{HOLDER_LIBRARY_ISSUE}")
        if issue.get("state") != "open":
            self.skipTest(f"CamCraft #{HOLDER_LIBRARY_ISSUE} is closed but not in {tag} yet "
                          f"(still .camtools v{newest}): wait for the release, see {HOLDER_TRACKING}")

    def test_blocking_issues_still_open(self):
        solved, waiting = [], []
        for number, tools in BLOCKING_ISSUES.items():
            issue = _fetch(f"https://api.github.com/repos/{REPO}/issues/{number}")
            if issue.get("state") != "open" and not self._released(number):
                waiting.append(f"CamCraft #{number} ({tools}) is closed but not in the latest release")
            elif issue.get("state") != "open":
                solved.append(
                    f"CamCraft #{number} '{issue.get('title')}' is {issue.get('state')} "
                    f"(closed {issue.get('closed_at')}): {tools} may now be supported"
                )
        if solved:
            self.fail(
                "\n".join(solved)
                + f"\nUpdate the converter, then remove the issue from BLOCKING_ISSUES. See {TRACKING}"
            )
        if waiting:
            self.skipTest("; ".join(waiting) + ": wait for the release")

    def _released(self, number: int) -> bool:
        if number not in RELEASE_MARKERS:
            return True
        path, marker = RELEASE_MARKERS[number]
        tag = _fetch(f"https://api.github.com/repos/{REPO}/releases/latest")["tag_name"]
        return marker in _fetch(f"https://api.github.com/repos/{REPO}/contents/{path}?ref={tag}", raw=True)

    @staticmethod
    def _kinds(ref: str) -> set[str]:
        source = _fetch(f"https://api.github.com/repos/{REPO}/contents/crates/tools/src/tools.rs?ref={ref}", raw=True)
        m = re.search(r"pub enum ToolKind\s*\{(.*?)\n\}", source, re.S)
        return set(re.findall(r"^\s{4}([A-Z]\w*)\s*,", m.group(1), re.M)) if m else set()

    def test_tool_kinds_unchanged(self):
        # The latest release decides; main only gives an early note.
        tag = _fetch(f"https://api.github.com/repos/{REPO}/releases/latest")["tag_name"]
        kinds = self._kinds(tag)
        self.assertTrue(kinds, "ToolKind enum not found in CamCraft's tools.rs")
        added, removed = sorted(kinds - KNOWN_KINDS), sorted(KNOWN_KINDS - kinds)
        if added or removed:
            self.fail(
                f"CamCraft {tag} tool kinds changed - added: {added or 'none'}, removed: {removed or 'none'}.\n"
                f"New kinds may cover skipped Fusion tools; update convert.py and KNOWN_KINDS. See {TRACKING}"
            )
        coming = sorted(self._kinds("main") - KNOWN_KINDS)
        if coming:
            self.skipTest(f"on CamCraft's main but not in {tag}: {', '.join(coming)}")

    def test_released_materials_unchanged(self):
        # The converter writes only material words the latest CamCraft
        # release knows (an unknown word refuses the whole library).
        from fusitool2craftool.convert import RELEASED_MATERIALS, UNRELEASED_MATERIALS

        tag = _fetch(f"https://api.github.com/repos/{REPO}/releases/latest")["tag_name"]
        source = _fetch(
            f"https://api.github.com/repos/{REPO}/contents/crates/tools/src/cutting.rs?ref={tag}", raw=True
        )
        words = set(re.findall(r'WorkMaterial::\w+ => "([a-z0-9_]+)"', source))
        self.assertTrue(words, f"no material words found in CamCraft {tag} cutting.rs")
        if words != set(RELEASED_MATERIALS):
            added = sorted(words - set(RELEASED_MATERIALS))
            removed = sorted(set(RELEASED_MATERIALS) - words)
            self.fail(
                f"CamCraft {tag} material words changed - added: {added or 'none'}, removed: {removed or 'none'}.\n"
                f"Move released words from UNRELEASED_MATERIALS ({', '.join(UNRELEASED_MATERIALS)}) "
                f"to RELEASED_MATERIALS in convert.py. See {TRACKING}"
            )


if __name__ == "__main__":
    unittest.main()
