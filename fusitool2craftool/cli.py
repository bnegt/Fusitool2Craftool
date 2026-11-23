"""Command line: Fusion 360 tool library -> CamCraft .camtools."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from . import __version__, camtools
from .convert import MATERIALS, RELEASED_MATERIALS, UNRELEASED_MATERIALS, Options, convert
from .fusion import FusionLibraryError, load_library
from .push import MODES, PushError, push
from .verify import verify_file

LIBRARY_SUFFIXES = (".json", ".tools")

# convert_file outcomes: written and verified / nothing to write / error
OK, EMPTY, FAILED = "ok", "empty", "failed"


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
    p.add_argument("input", type=Path,
                   help="Fusion 360 tool library (.tools or .json), or a folder of them "
                   "(e.g. the ExportAllToolLibraries export; searched recursively)")
    p.add_argument("-o", "--output", type=Path,
                   help="output .camtools file (default: next to the input); for a folder "
                   "input, an output folder that gets the same subfolders")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    conv = p.add_argument_group("conversion")
    conv.add_argument("--preset", help="Fusion preset name to take rpm/feed from (default: each tool's first)")
    conv.add_argument("--material", choices=MATERIALS, metavar="WORD",
                      help="CamCraft workpiece material the presets are for "
                      f"({', '.join(RELEASED_MATERIALS)}; with --unreleased-materials also "
                      f"{', '.join(UNRELEASED_MATERIALS)})")
    conv.add_argument("--unreleased-materials", action="store_true",
                      help="allow the wood/plastic material words CamCraft has after 0.25.0 "
                      "(0.25.0 refuses a library that names them)")
    conv.add_argument("--all-presets", action="store_true",
                      help="also write one cutting data row per Fusion preset whose name names a "
                      "CamCraft material (e.g. '6061 aluminum', '4140 steel', 'acetal'); "
                      "the others are listed")
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
        all_presets=args.all_presets,
        unreleased_materials=args.unreleased_materials,
    )
    try:
        convert([], options)  # checks --material against the CamCraft it is for
    except ValueError as e:
        print(red(f"error: {e}"), file=sys.stderr)
        return 1
    if args.input.is_dir():
        return convert_folder(args, options)
    output = args.output or args.input.with_suffix(camtools.EXTENSION)
    return 0 if convert_file(args.input, output, args, options) in (OK, EMPTY) else 1


def library_files(folder: Path) -> list[Path]:
    """Fusion library files under ``folder`` (recursive, sorted, hidden files left out)."""
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in LIBRARY_SUFFIXES and not p.name.startswith(".")
    )


def convert_folder(args, options: Options) -> int:
    if args.push is not None:
        print(red("error: --push takes one library at a time (tools with the same name or "
                  "T number in two libraries would overwrite each other); push the .camtools "
                  "files one by one"), file=sys.stderr)
        return 1
    files = library_files(args.input)
    if not files:
        print(red(f"error: no .json or .tools files in {args.input}"), file=sys.stderr)
        return 1
    out_root = args.output
    counts = Counter()
    for src in files:
        rel = src.relative_to(args.input)
        output = (out_root / rel if out_root else src).with_suffix(camtools.EXTENSION)
        output.parent.mkdir(parents=True, exist_ok=True)
        print(f"== {rel}")
        counts[convert_file(src, output, args, options, quiet_empty=True)] += 1
    summary = (f"{len(files)} {'library' if len(files) == 1 else 'libraries'}: "
               f"{counts[OK]} converted, {counts[EMPTY]} without convertible tools, "
               f"{counts[FAILED]} failed")
    if counts[FAILED]:
        print(red(f"error: {summary}"), file=sys.stderr)
        return 1
    print(green(summary))
    return 0


def print_preset_report(report: Counter, limit: int = 15) -> None:
    """--all-presets: which preset names became which material rows."""
    matched = [(k, n) for k, n in report.items() if k[1] in MATERIALS]
    other = [(k, n) for k, n in report.items() if k[1] not in MATERIALS]
    for title, rows in (("cutting rows from presets", matched),
                        ("presets not written as rows", other)):
        if not rows:
            continue
        print(f"{title}:")
        rows.sort(key=lambda kn: (-kn[1], kn[0]))
        for (name, outcome), n in rows[:limit]:
            print(f"  '{name}' -> {outcome} ({n} {plural('tool', n)})")
        if len(rows) > limit:
            print(f"  ... and {len(rows) - limit} more")


def convert_file(src: Path, output: Path, args, options: Options, quiet_empty: bool = False) -> str:
    """Convert, write, verify (and optionally check/push) one library."""
    try:
        entries = load_library(src)
    except FusionLibraryError as e:
        print(red(f"error: {e}"), file=sys.stderr)
        return FAILED

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
    if options.all_presets:
        print_preset_report(result.presets)
    if result.holders:
        print(f"{result.holders} {plural('holder', result.holders)} not converted "
              f"(CamCraft imports no stand-alone holders; a tool's own holder is kept as its holder size)")
    if args.strict and result.skipped:
        print(red(f"error: {len(result.skipped)} tool(s) could not be converted (--strict)"), file=sys.stderr)
        return FAILED
    if not result.tools:
        if not result.input_tools:
            # a holder library (or an empty one): nothing to convert, not an error
            print(f"no tools in {src.name} - nothing written")
            return EMPTY
        if quiet_empty:
            print(f"no convertible tools in {src.name} (all skipped) - nothing written")
            return EMPTY
        print(red("error: no tools could be converted"), file=sys.stderr)
        return FAILED

    written = camtools.write(result, output)
    print(f"wrote {len(result.tools)} tool(s) to {written}"
          + (f" ({len(result.skipped)} skipped)" if result.skipped else ""))

    problems = verify_file(written, result)
    if problems:
        for p in problems:
            print(red(f"mismatch: {p}"), file=sys.stderr)
        print(red(f"error: {written} does not match the Fusion library "
                  f"({len(problems)} problem(s)) - do not import it"), file=sys.stderr)
        return FAILED
    print(green(f"verified: all {len(result.tools)} tool(s) in {written.name} match the Fusion data"))

    if args.check:
        try:
            summary = push(written, None, mode="add", cli=args.camcraft_cli)
        except PushError as e:
            print(red(f"error: CamCraft check failed: {e}"), file=sys.stderr)
            return FAILED
        print(f"CamCraft accepts the file: {summary}")

    if args.push is not None:
        try:
            summary = push(written, args.push, host=args.host, mode=args.mode, cli=args.camcraft_cli)
        except PushError as e:
            print(red(f"error: {e}"), file=sys.stderr)
            return FAILED
        print(f"imported into CamCraft ({args.mode}): {summary}")
    return OK


if __name__ == "__main__":
    sys.exit(main())
