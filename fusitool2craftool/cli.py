"""Command line: Fusion 360 tool library -> CamCraft .camtools."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from . import __version__, camtools
from .convert import MATERIALS, Options, convert
from .fusion import FusionLibraryError, load_library
from .push import MODES, PushError, push
from .verify import verify_file


def _color_on(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


def red(text: str) -> str:
    return f"\033[31m{text}\033[0m" if _color_on(sys.stderr) else text


def green(text: str) -> str:
    return f"\033[32m{text}\033[0m" if _color_on(sys.stdout) else text


def plural(word: str, n: int) -> str:
    return word if n == 1 else word + "s"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fusitool2craftool",
        description="Convert a Fusion 360 tool library (.tools/.json) to a CamCraft "
        "tool library (.camtools), and optionally import it into a running CamCraft.",
    )
    p.add_argument("input", type=Path, help="Fusion 360 tool library (.tools or .json)")
    p.add_argument("-o", "--output", type=Path,
                   help="output .camtools file (default: next to the input)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    conv = p.add_argument_group("conversion")
    conv.add_argument("--preset", help="Fusion preset name to take rpm/feed from (default: each tool's first)")
    conv.add_argument("--material", choices=MATERIALS, metavar="WORD",
                      help="CamCraft workpiece material the presets are for "
                      f"({', '.join(MATERIALS)})")
    conv.add_argument("--holder-d", type=float, default=50.0, metavar="MM",
                      help="holder diameter for tools without a Fusion holder (default 50)")
    conv.add_argument("--holder-len", type=float, default=40.0, metavar="MM",
                      help="holder length for tools without a Fusion holder (default 40)")
    conv.add_argument("--default-rpm", type=float, metavar="RPM",
                      help="spindle speed for tools without a preset (otherwise they are skipped)")
    conv.add_argument("--default-feed", type=float, metavar="MM_MIN",
                      help="feed in mm/min for tools without a preset (otherwise they are skipped)")
    conv.add_argument("--strict", action="store_true",
                      help="fail instead of skipping tools that cannot be converted")

    live = p.add_argument_group("import into a running CamCraft (optional)")
    live.add_argument("--push", type=int, metavar="PORT",
                      help="import the result into the CamCraft window started with --mcp-port PORT")
    live.add_argument("--check", action="store_true",
                      help="verify the result by importing it into a headless CamCraft (needs camcraft-cli)")
    live.add_argument("--host", default="127.0.0.1", help="CamCraft host (default 127.0.0.1)")
    live.add_argument("--mode", choices=MODES, default="update",
                      help="import mode (default update: same-named tools are refreshed)")
    live.add_argument("--camcraft-cli", default="camcraft-cli", metavar="PATH",
                      help="path to camcraft-cli (default: from PATH)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = Options(
        preset=args.preset,
        material=args.material,
        holder_d=args.holder_d,
        holder_len=args.holder_len,
        default_rpm=args.default_rpm,
        default_feed=args.default_feed,
    )
    try:
        entries = load_library(args.input)
    except FusionLibraryError as e:
        print(red(f"error: {e}"), file=sys.stderr)
        return 1

    result = convert(entries, options)
    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)
    for s in result.skipped:
        what = f" [{s.category}]" if s.category else ""
        print(red(f"skipped{what}: {s.name}: {s.reason}"), file=sys.stderr)
    families = Counter(s.category for s in result.skipped if s.category)
    if families:
        listed = ", ".join(f"{n} {plural(cat, n)}" for cat, n in sorted(families.items()))
        print(red(f"NOT IMPORTED: {listed} - CamCraft has no tool type for them, "
                  f"so they are skipped"), file=sys.stderr)
    if args.strict and result.skipped:
        print(red(f"error: {len(result.skipped)} tool(s) could not be converted (--strict)"), file=sys.stderr)
        return 1
    if not result.tools:
        print(red("error: no tools could be converted"), file=sys.stderr)
        return 1

    output = args.output or args.input.with_suffix(camtools.EXTENSION)
    written = camtools.write(result, output)
    print(f"wrote {len(result.tools)} tool(s) to {written}"
          + (f" ({len(result.skipped)} skipped)" if result.skipped else ""))

    problems = verify_file(written, result)
    if problems:
        for p in problems:
            print(red(f"mismatch: {p}"), file=sys.stderr)
        print(red(f"error: {written} does not match the Fusion library "
                  f"({len(problems)} problem(s)) - do not import it"), file=sys.stderr)
        return 1
    print(green(f"verified: all {len(result.tools)} tool(s) in {written.name} match the Fusion data"))

    if args.check:
        try:
            summary = push(written, None, mode="add", cli=args.camcraft_cli)
        except PushError as e:
            print(red(f"error: CamCraft check failed: {e}"), file=sys.stderr)
            return 1
        print(f"CamCraft accepts the file: {summary}")

    if args.push is not None:
        try:
            summary = push(written, args.push, host=args.host, mode=args.mode, cli=args.camcraft_cli)
        except PushError as e:
            print(red(f"error: {e}"), file=sys.stderr)
            return 1
        print(f"imported into CamCraft ({args.mode}): {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
