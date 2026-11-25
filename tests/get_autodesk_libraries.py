"""Download Autodesk's public vendor tool libraries to test against.

    python3 tests/get_autodesk_libraries.py            # what is missing here
    python3 tests/get_autodesk_libraries.py --update   # all of it again
    python3 tests/get_autodesk_libraries.py --only harvey

Reads the catalogue behind https://cam.autodesk.com/hsmtools and saves each
"For Fusion" download as JSON in private/test-libraries/autodesk/
(git-ignored: the files belong to the vendors and stay out of this repo).
About 530 MB in all. See TEST_LIBRARIES.md.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SITE = "https://cam.autodesk.com/tools/"
OUT = Path(__file__).parent.parent / "private" / "test-libraries" / "autodesk"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as r:
        return r.read()


def file_name(path: str) -> str:
    """'Harvey Tool/End Mills' -> 'Harvey Tool - End Mills.json', never outside OUT."""
    name = " - ".join(part for part in path.replace("\\", "/").split("/") if part not in ("", ".", ".."))
    return "".join("_" if c in '<>:"|?*' or ord(c) < 32 else c for c in name) + ".json"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--update", action="store_true", help="download again what is already here")
    p.add_argument("--only", metavar="TEXT", default="", help="only libraries with TEXT in their name")
    args = p.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    missing = 0
    for entry in json.loads(fetch(SITE + "tools/tools.json").decode("utf-8-sig")):
        path = entry["path"]
        target = OUT / file_name(path)
        if args.only.lower() not in path.lower() or (target.exists() and not args.update):
            continue
        try:
            data = fetch(SITE + "download.php?name=" + urllib.parse.quote(path))
            # the site answers 200 with a one-line message when it has no file
            entries = json.loads(data.decode("utf-8-sig"))["data"]
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError) as e:
            missing += 1
            print(f"not available: {path} ({type(e).__name__})", file=sys.stderr)
        else:
            target.write_bytes(data)
            print(f"ok {target.name}: {len(entries)} entries, {len(data) / 1e6:.1f} MB", flush=True)
        time.sleep(1)  # be gentle with the site
    if missing:
        print(f"{missing} not available", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
