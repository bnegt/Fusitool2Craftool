"""Import converted libraries into a real, headless CamCraft.

Needs ``camcraft-cli`` (ships with CamCraft): set ``CAMCRAFT_CLI`` to its
path, or have it on ``PATH``. Skipped otherwise. Each file is imported with
``camcraft-cli mcp`` (``push(..., port=None)``) - CamCraft's own reader and
tool laws judge it, no window is touched - and every tool we wrote must come
back as added, none renamed or skipped.

Covers the sample fixture, an edge-case library built here, and every
Fusion library under private/ (see TEST_LIBRARIES.md).
"""

import contextlib
import dataclasses
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from fusitool2craftool import camtools
from fusitool2craftool.cli import library_files
from fusitool2craftool.convert import Options, convert
from fusitool2craftool.fusion import load_library
from fusitool2craftool.push import PROTOCOL_VERSION, PushError, _close, _content_text, _Session, push

from .test_convert import ARBOR, DEFAULT_SPEEDS, FIXTURES, PRIVATE, corner_chamfer, holder, keyseat, multi_form, saw, single_form, tapered, tool
from .test_lathe import TYPES, lathe

CLI = os.environ.get("CAMCRAFT_CLI") or shutil.which("camcraft-cli")


def edge_cases() -> list[dict]:
    """One tool per conversion path that could trip CamCraft's reader."""
    star = tool(description="legacy star preset", unit="inches",
                geometry={"DC": 0.25, "LCF": 0.5, "LB": 1, "SFDM": 0.25})
    star["start-values"] = {"*": {"n": 5000, "v_f": 39.3701}}
    return [
        tool(description="inch ball", unit="inches", type_="ball end mill",
             geometry={"DC": 0.125, "LCF": 0.5, "LB": 0.8, "SFDM": 0.125, "RE": 0.0625},
             presets=[{"name": "Rough", "n": 8000, "v_f": 30, "stepdown": 0.125, "use-stepdown": True,
                       "stepover": 0.03, "use-stepover": True}]),
        tool(description="bull nose", type_="bull nose end mill", geometry={"RE": 1}),
        tool(description="chamfer 90", type_="chamfer mill", geometry={"TA": 45}),
        tool(description="countersink", type_="counter sink", geometry={"SIG": 82}),
        tool(description="drill plunge feed", type_="drill", geometry={"SIG": 118},
             presets=[{"name": "P", "n": 3000, "v_f": 0, "v_f_plunge": 200}]),
        tool(description="drill f_n", type_="drill", geometry={"SIG": 135},
             presets=[{"name": "P", "n": 2000, "f_n": 0.1}]),
        tool(description="flat bottom drill", type_="drill", unit="inches",
             geometry={"DC": 0.625, "LCF": 2.677, "LB": 3.15, "SFDM": 0.625, "SIG": 180},
             presets=[{"name": "P", "n": 2139, "v_f_plunge": 25.67}]),
        tool(description="spot drill 60", type_="spot drill", unit="inches",
             geometry={"DC": 0.125, "LCF": 0.375, "LB": 0.92, "SFDM": 0.125, "SIG": 60},
             presets=[{"name": "P", "n": 4000, "v_f_plunge": 8}]),
        multi_form(description="M5-0.80 THREAD MILL"),
        saw(description="saw without an arbor"),
        saw(description="saw on its arbor", holder=ARBOR),
        tool(description="tap M8 left", type_="tap left hand", geometry={"DC": 8, "TP": 1.25},
             presets=[{"name": "P", "n": 500}]),
        tool(description="thread mill", type_="thread mill", geometry={"DC": 10, "LCF": 15, "TP": 1.5}),
        single_form(description="thread mill single-form"),
        single_form({"DC": 0.7, "OAL": 50, "TPN": 0.25, "TPX": 0.25, "shoulder-length": 3.5}, description="thread mill M1"),
        single_form({"shoulder-diameter": 2.5}, description="thread mill single-form held", unit="millimeters",
                    holder={"unit": "inches", "segments": [{"height": 1, "lower-diameter": 1, "upper-diameter": 2}]}),
        corner_chamfer(description="corner chamfer 45"),
        corner_chamfer({"chamfer-angle": 30}, description="corner chamfer 30"),
        tool(description="face mill", type_="face mill", geometry={"DC": 50, "LCF": 5, "LB": 40, "SFDM": 22}),
        tool(description="boring bar", type_="boring bar", geometry={"DC": 12}),
        tool(description="slitting saw", type_="slot mill", unit="inches",
             geometry={"DC": 0.75, "LCF": 0.002, "SFDM": 0.25, "LB": 0.252, "shoulder-diameter": 0.25}),
        tool(description="woodruff keyseat", type_="slot mill",
             geometry={"DC": 19, "LCF": 4.8, "SFDM": 8, "LB": 30}),
        tool(description="lollipop capped", type_="lollipop mill", unit="inches",
             geometry={"DC": 0.375, "LCF": 0.35, "SFDM": 0.24, "LB": 2.15}),
        tool(description="dovetail 60 capped", type_="dovetail mill", unit="inches",
             geometry={"DC": 1, "LCF": 0.5, "SFDM": 0.5, "LB": 0.75, "TA": 30}),
        tool(description="dovetail 14 router", type_="dovetail mill", unit="inches",
             geometry={"DC": 1, "LCF": 1, "SFDM": 0.5, "LB": 1.5, "TA": 14}),
        keyseat(description="keyseat under a wide shank"),
        keyseat(description="keyseat under a wide shank, held", holder={"unit": "millimeters", "segments": [
            {"height": 10, "lower-diameter": 20, "upper-diameter": 30}]}),
        tool(description="dovetail, cone at the head", type_="dovetail mill", unit="inches",
             geometry={"DC": 0.5, "LCF": 0.218, "LB": 0.61816, "SFDM": 0.5, "TA": 30, "shoulder-length": 0.218,
                       "shoulder-diameter": 0.24827528},
             shaft={"segments": [{"height": 0, "lower-diameter": 0.248, "upper-diameter": 0.248},
                                 {"height": 0.1502, "lower-diameter": 0.248, "upper-diameter": 0.5}]}),
        tool(description="ball under a wide shank", type_="lollipop mill",
             geometry={"DC": 1.8, "LCF": 1.612, "LB": 35, "SFDM": 3, "RE": 0.9, "shoulder-diameter": 1.1,
                       "shoulder-length": 10},
             shaft={"segments": [{"height": 1.6454, "lower-diameter": 1.1, "upper-diameter": 3}]}),
        star,
        tool(description="drawn holder", unit="inches", geometry={"DC": 0.5, "LCF": 1, "LB": 2, "SFDM": 0.5},
             holder={"unit": "inches", "segments": [
                 {"height": 0.5, "lower-diameter": 1.0, "upper-diameter": 1.25},
                 {"height": 1.0, "lower-diameter": 1.25, "upper-diameter": 1.25},
                 {"height": 0.25, "lower-diameter": 1.0, "upper-diameter": 1.0},
                 {"height": 0.5, "lower-diameter": 2.5, "upper-diameter": 2.5}]}),
        *(lathe(t) for t in TYPES),
        tool(description="drawn holder sliver", geometry={"DC": 6, "LCF": 10, "LB": 30},
             holder={"unit": "millimeters", "segments": [
                 {"height": 0.005, "lower-diameter": 20, "upper-diameter": 21},
                 {"height": 20, "lower-diameter": 21, "upper-diameter": 21}]}),
    ]


@unittest.skipUnless(CLI, "camcraft-cli not found (set CAMCRAFT_CLI)")
class CamCraftLiveTests(unittest.TestCase):
    def assert_imports(self, entries, options=None):
        result = convert(entries, options)
        if not result.tools:
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "lib.camtools")
            report = json.loads(push(path, None, mode="add", cli=CLI, timeout=300))
        self.assertEqual(sorted(report["added"]), sorted(t["name"] for t in result.tools))
        self.assertEqual((report["renamed"], report["skipped"]), ([], []))
        # #12: every drawn holder is taken, none left out
        bodies = sum("body" in t["definition"] for t in result.tools)
        self.assertEqual((report.get("holders", 0), report.get("holders_left_out", 0)), (bodies, 0))

    def test_sample_fixture(self):
        self.assert_imports(load_library(FIXTURES / "sample.json"))

    def test_edge_cases(self):
        self.assertEqual(convert(edge_cases()).skipped, [])
        self.assert_imports(edge_cases())

    def test_catalog_without_presets_on_defaults(self):
        # #8: a vendor catalog without presets, on per-diameter / per-kind defaults
        entries = []
        for e in edge_cases():
            e = dict(e)
            e.pop("start-values", None)
            entries.append(e)
        options = Options(default_vc=150, default_fz=0.03, max_rpm=24000,
                          kind_defaults={"drill": {"vc": 60, "feed": 150}, "t_slot": {"vc": 60, "fz": 0.005},
                                         # lathe tools have no teeth for a chip load
                                         "turning": {"feed": 100}, "bore": {"feed": 100}, "groove": {"feed": 50}})
        self.assertEqual(convert(entries, options).skipped, [])
        self.assert_imports(entries, options)

    def test_default_holder(self):
        # #14: one stand-alone holder drawn on every tool without one
        options = Options(default_holder=holder())
        tools = [t["definition"] for t in convert(edge_cases(), options).tools]
        self.assertTrue(all(("body" in d) != (d["holder_d"] == 0) for d in tools))  # lathe tools take none
        self.assert_imports(edge_cases(), options)

    def test_tapered_mills(self):
        # #10: .camtools version 6 is only on CamCraft's main so far
        options = Options(unreleased_tapers=True)
        entries = [tapered(), tapered({"RE": 0.3}, description="rounded"), tapered({"RE": 1}, description="ball by name"),
                   tapered({"DC": 0.02749, "RE": 0.015, "TA": 5, "LCF": 0.3, "LB": 0.48686, "OAL": 3, "SFDM": 0.1875},
                           unit="inches", description="ball"), tapered({"TA": 45, "LCF": 3}, description="steep")]
        try:
            self.assert_imports(entries, options)
        except PushError as e:
            if "version 6 is not supported" in str(e):
                self.skipTest("this CamCraft reads no flat or ball taper tips yet (.camtools version 6)")
            raise
        for src in library_files(PRIVATE) if PRIVATE.is_dir() else []:
            mills = [e for e in load_library(src) if e.get("type") == "tapered mill"]
            if mills:
                with self.subTest(library=src.name):
                    self.assert_imports(mills, dataclasses.replace(DEFAULT_SPEEDS, unreleased_tapers=True))

    def test_duplicate_fusion_numbers_are_fine(self):
        # .camtools numbers are not imported (CamCraft numbers the tools
        # itself), so tools sharing a Fusion T number do not clash.
        self.assert_imports([tool(description="A"), tool(description="B")])

    def test_edge_cases_with_material_rows(self):
        self.assert_imports(edge_cases(), Options(material="aluminium_6061"))

    def test_rows_per_preset(self):
        presets = [{"name": "6061 Aluminum", "n": 18000, "v_f": 2000, "stepdown": 3, "use-stepdown": True},
                   {"name": "4140 Steel", "n": 6000, "v_f": 500},
                   {"name": "303 Stainless", "n": 5000, "v_f": 400, "stepover": 1, "use-stepover": True},
                   {"name": "Gray Iron", "n": 7000, "v_f": 700},
                   {"name": "Brass", "n": 15000, "v_f": 1500},
                   {"name": "Acetal", "n": 16000, "v_f": 2500},
                   {"name": "Softwood", "n": 18000, "v_f": 3500},
                   {"name": "Hardwood", "n": 16000, "v_f": 3000},
                   {"name": "MDF", "n": 17000, "v_f": 3200},
                   {"name": "Plywood", "n": 17500, "v_f": 3300},
                   {"name": "Acrylic", "n": 15500, "v_f": 2200},
                   {"name": "HDPE", "n": 16500, "v_f": 2600}]  # wood/plastic words: CamCraft 0.26.0
        entries = [tool(description=f"multi {i}", presets=presets) for i in range(2)] + edge_cases()
        self.assert_imports(entries, Options(all_presets=True))

    def test_private_libraries(self):
        files = library_files(PRIVATE) if PRIVATE.is_dir() else []
        if not files:
            self.skipTest("no Fusion libraries in private/")
        for src in files:
            for options in (Options(), Options(all_presets=True), DEFAULT_SPEEDS):
                with self.subTest(library=str(src.relative_to(PRIVATE)), all_presets=options.all_presets,
                                  defaults=options is DEFAULT_SPEEDS):
                    self.assert_imports(load_library(src), options)


@contextlib.contextmanager
def camcraft():
    """A headless CamCraft document: ``call(tool, **arguments)`` returns the tool's text."""
    proc = subprocess.Popen([CLI, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")
    timer = threading.Timer(120, proc.kill)
    timer.start()
    try:
        session = _Session(proc)
        session.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                       "clientInfo": {"name": "fusitool2craftool-tests", "version": "0"}})
        session.notify("notifications/initialized")

        def call(name, /, **arguments):
            r = session.request("tools/call", {"name": name, "arguments": arguments})
            if r.get("isError"):
                raise AssertionError(f"{name}: {_content_text(r)}")
            return _content_text(r)

        yield call
    finally:
        timer.cancel()
        _close(proc)


def plate_with_hole(path: Path, hole: float, depth: float, size: float = 30.0, n: int = 96) -> None:
    """An ASCII STL: a square plate, z 0..depth, with a through hole at its centre."""
    def on_square(a):
        k = size / 2 / max(abs(math.cos(a)), abs(math.sin(a)))
        return k * math.cos(a), k * math.sin(a)

    facets = []
    angles = [2 * math.pi * i / n for i in range(n + 1)]  # multiples of 45 degrees hit the corners
    for a, b in zip(angles, angles[1:]):
        ci, cj = ((hole / 2 * math.cos(t), hole / 2 * math.sin(t)) for t in (a, b))
        qi, qj = on_square(a), on_square(b)
        top = [[(*ci, depth), (*qi, depth), (*qj, depth)], [(*ci, depth), (*qj, depth), (*cj, depth)]]
        facets += top
        facets += [[(x, y, 0.0) for x, y, _ in reversed(f)] for f in top]
        facets += [[(*qi, 0.0), (*qj, 0.0), (*qj, depth)], [(*qi, 0.0), (*qj, depth), (*qi, depth)]]
        facets += [[(*cj, 0.0), (*ci, 0.0), (*ci, depth)], [(*cj, 0.0), (*ci, depth), (*cj, depth)]]
    lines = ["solid plate"]
    for f in facets:
        lines += ["facet normal 0 0 0", "outer loop", *("vertex %.6f %.6f %.6f" % v for v in f), "endloop", "endfacet"]
    path.write_text("\n".join(lines + ["endsolid plate", ""]))


@unittest.skipUnless(CLI, "camcraft-cli not found (set CAMCRAFT_CLI)")
class ThreadMillingLiveTests(unittest.TestCase):
    """#16, #37: CamCraft threads an M5 hole with a converted thread mill."""

    def verify_thread(self, depth: float, change=None, mill=None) -> dict:
        """Drill and thread an M5x0.8 hole; CamCraft's verify of the thread."""
        mill = mill or single_form()  # D3.9 under a 4 mm shank, reach 16
        result = convert([
            {**mill, "description": "THREAD MILL"},
            tool("drill", description="D", geometry={"DC": 4.2, "SFDM": 4.2, "LCF": 50, "LB": 60, "SIG": 118})])
        self.assertEqual(result.skipped, [])
        if change:
            change(next(t for t in result.tools if t["name"] == "THREAD MILL")["definition"])
        with tempfile.TemporaryDirectory() as tmp, camcraft() as call:
            library = camtools.write(result, Path(tmp) / "lib.camtools")
            plate_with_hole(Path(tmp) / "part.stl", 4.2, depth)
            call("load_machine", builtin="VMC 3-axis")
            call("import_model", path=str(Path(tmp) / "part.stl"), name="part")
            call("set_stock", box={"dx": 30, "dy": 30, "dz": depth})
            call("import_tool_library", path=str(library), mode="add", format="camtools")
            for kind, name, mill, params in (("drill", "Dr", "D", {}), ("thread", "T", "THREAD MILL", {"pitch": 0.8})):
                call("add_operation", kind=kind, name=name, tool=mill, source="part", params=params)
                self.assertEqual(json.loads(call("detect_holes", name=name))["holes"], 1)
                call("generate", name=name)
            return json.loads(call("verify", name="T"))

    def assert_shank_hit(self, report: dict):
        self.assertFalse(report["ok"])
        self.assertEqual({f["kind"] for f in report["findings"]}, {"shank_hit"})
        self.assertTrue(all(f["blocks_post"] for f in report["findings"]))

    def test_within_the_reach(self):
        report = self.verify_thread(14)
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["ok"], report)

    def test_deeper_than_the_reach_is_held(self):
        self.assert_shank_hit(self.verify_thread(20))

    def test_multi_form_stored_as_a_form_mill(self):
        # 8 mm of teeth on a 6 mm neck, then a cone to a 6 mm shank
        report = self.verify_thread(13, mill=multi_form())
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["ok"], report)
        self.assert_shank_hit(self.verify_thread(20, mill=multi_form()))

    def test_plain_shank_is_held(self):
        # why the neck is written: the 4 mm shank right above the tooth hits
        def plain(d):
            d.pop("body")
            d.update(shank_d=4.0, shank_len=52 - d["flute_len"], holder_d=50.0, holder_len=40.0)
        self.assert_shank_hit(self.verify_thread(14, plain))


def prism(path: Path, outline: list[tuple[float, float]], length: float) -> None:
    """An ASCII STL of a counter-clockwise (y, z) outline drawn out along X."""
    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    left, ears = list(range(len(outline))), []
    while len(left) > 2:  # clip ears
        for k in range(len(left)):
            tri = left[k - 1], left[k], left[(k + 1) % len(left)]
            a, b, c = (outline[i] for i in tri)
            if turn(a, b, c) > 0 and not any(
                    turn(a, b, p) >= 0 and turn(b, c, p) >= 0 and turn(c, a, p) >= 0
                    for p in (outline[i] for i in left if i not in tri)):
                ears.append((a, b, c))
                left.pop(k)
                break
        else:
            raise ValueError("outline is not counter-clockwise")
    x = length / 2
    facets = [f for a, b, c in ears for f in ([(x, *a), (x, *b), (x, *c)], [(-x, *a), (-x, *c), (-x, *b)])]
    for a, b in zip(outline, outline[1:] + outline[:1]):
        facets += [[(-x, *a), (-x, *b), (x, *b)], [(-x, *a), (x, *b), (x, *a)]]
    lines = ["solid prism"]
    for f in facets:
        lines += ["facet normal 0 0 0", "outer loop", *("vertex %.6f %.6f %.6f" % v for v in f), "endloop", "endfacet"]
    path.write_text("\n".join(lines + ["endsolid prism", ""]))


@unittest.skipUnless(CLI, "camcraft-cli not found (set CAMCRAFT_CLI)")
class UndercutLiveTests(unittest.TestCase):
    """#27: CamCraft cuts an undercut slot with a cutter whose shank is as wide as its head."""

    TOP = 50.0

    def findings(self, entry, depth: float, head: list[tuple[float, float]], entry_slot: float, change=None) -> set[str]:
        """What CamCraft finds in a slot ``depth`` deep. ``head`` is the undercut's right
        side as (y, height over its floor); ``entry_slot`` the width of the slot above it."""
        result = convert([{**entry, "description": "UC"},
                          tool(description="EM", geometry={"DC": 6, "SFDM": 6, "LCF": 60, "LB": 70, "OAL": 90})])
        self.assertEqual(result.skipped, [])
        if change:
            change(next(t for t in result.tools if t["name"] == "UC")["definition"])
        floor, half = self.TOP - depth, entry_slot / 2
        right = [(y, floor + h) for y, h in head] + [(half, floor + head[-1][1]), (half, self.TOP)]
        right = [q for i, q in enumerate(right) if q not in right[:i]]  # a dovetail meets the slot above it
        outline = [(-20, 0), (20, 0), (20, self.TOP), *reversed(right), *((-y, z) for y, z in right), (-20, self.TOP)]
        line = {"a": [-30, 0], "b": [30, 0], "clearance_z": self.TOP + 10, "top_z": self.TOP, "stepover": 2}
        with tempfile.TemporaryDirectory() as tmp, camcraft() as call:
            library = camtools.write(result, Path(tmp) / "lib.camtools")
            prism(Path(tmp) / "part.stl", outline, 60)
            call("load_machine", builtin="VMC 3-axis")
            call("import_model", path=str(Path(tmp) / "part.stl"), name="part")
            call("set_stock", box={"dx": 60, "dy": 40, "dz": self.TOP})
            call("import_tool_library", path=str(library), mode="add", format="camtools")
            call("add_operation", kind="Slot", name="S", tool="EM", source="part",
                 params={**line, "width": entry_slot, "bottom_z": floor + head[-1][1], "stepdown": 3})
            call("add_operation", kind="Slot Undercut", name="U", tool="UC", source="part",
                 params={**line, "width": 2 * head[0][0], "neck_slot": entry_slot, "run_in": 25, "bottom_z": floor,
                         "retract_z": self.TOP + 5, "feed": 120, "plunge_feed": 60})
            for name in "SU":
                call("generate", name=name)
            report = json.loads(call("verify", name="U"))
        self.assertEqual(report["operations"][0]["status"], "ok")
        return {f["kind"] for f in report["findings"] if f["blocks_post"]}

    T_SLOT = [(8, 0), (8, 0.093 * 25.4)]  # for the 12.7 mm head; the entry slot lets the neck by, not the shank

    def test_keyseat_within_and_past_its_reach(self):
        reach = (0.093 + 0.75) * 25.4  # 21.4 mm: head and neck
        self.assertEqual(self.findings(keyseat(), reach - 0.2, self.T_SLOT, 10), set())
        # held as soon as the cone to the shank is down in the slot
        self.assertEqual(self.findings(keyseat(), reach + 0.5, self.T_SLOT, 10), {"shank_hit"})
        self.assertEqual(self.findings(keyseat(), 35, self.T_SLOT, 10), {"shank_hit"})

    def test_without_the_drawn_shank_camcraft_would_pass_it(self):
        # why the shank is drawn: a neck alone goes as deep as you like
        def bare(d):
            d.pop("body")
            d.update(holder_d=0.0, holder_len=0.0)
        self.assertEqual(self.findings(keyseat(), 35, self.T_SLOT, 10, bare), set())

    def test_dovetail_within_and_past_its_reach(self):
        # Harvey 865916's shape at twice its size: 60 degrees included
        e = tool("dovetail mill", unit="inches", geometry={
            "DC": 0.5, "LCF": 0.236, "LB": 1.122, "OAL": 3, "SFDM": 0.5, "TA": 30, "RE": 0,
            "shoulder-diameter": 0.2275, "shoulder-length": 0.236},
            shaft={"segments": [{"height": 0.5, "lower-diameter": 0.228, "upper-diameter": 0.228},
                                {"height": 0.136, "lower-diameter": 0.228, "upper-diameter": 0.5}]})
        top = 5.9
        slot = [(8, 0), (8 - top * math.tan(math.radians(30)), top)]
        self.assertEqual(self.findings(e, 15, slot, 2 * slot[-1][0]), set())
        self.assertEqual(self.findings(e, 25, slot, 2 * slot[-1][0]), {"shank_hit"})

    def test_saw_is_held_where_its_arbor_widens(self):
        # #31: 12 mm wide for 20 mm above the 1.6 mm blade, 40 mm wide above that
        blade = [(16, 0), (16, 0.0625 * 25.4)]  # the 31.75 mm saw; the slot above it clears the arbor
        self.assertEqual(self.findings(saw(holder=ARBOR), 21, blade, 14), set())
        self.assertEqual(self.findings(saw(holder=ARBOR), 22, blade, 14), {"shank_hit"})
        # without an arbor the default 50 mm holder stands on the blade
        self.assertEqual(self.findings(saw(), 5, blade, 14), {"shank_hit"})


def revolved(path: Path, profile: list[tuple[float, float]], n: int = 120) -> None:
    """An ASCII STL of a closed (radius, z) profile turned about Z."""
    facets = []
    for (r1, z1), (r2, z2) in zip(profile, profile[1:] + profile[:1]):
        for i in range(n):
            a, b = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            q = [(r * math.cos(t), r * math.sin(t), z) for r, t, z in ((r1, a, z1), (r1, b, z1), (r2, b, z2), (r2, a, z2))]
            facets += [f for f, r in (((q[0], q[1], q[2]), r1), ((q[0], q[2], q[3]), r2)) if r > 1e-9]
    lines = ["solid part"]
    for f in facets:
        lines += ["facet normal 0 0 0", "outer loop", *("vertex %.6f %.6f %.6f" % v for v in f), "endloop", "endfacet"]
    path.write_text("\n".join(lines + ["endsolid part", ""]))


@unittest.skipUnless(CLI, "camcraft-cli not found (set CAMCRAFT_CLI)")
class TurningLiveTests(unittest.TestCase):
    """#2: CamCraft turns a part with each converted lathe tool."""

    def findings(self, entry, profile, operations, stock=None, change=None):
        result = convert([entry])
        self.assertEqual(result.skipped, [])
        if change:
            change(result.tools[0]["definition"])
        out = []
        with tempfile.TemporaryDirectory() as tmp, camcraft() as call:
            library = camtools.write(result, Path(tmp) / "lib.camtools")
            revolved(Path(tmp) / "part.stl", profile)
            call("load_machine", builtin="Lathe")
            call("import_model", path=str(Path(tmp) / "part.stl"), name="part")
            call("set_stock", **({"cylinder": stock} if stock else {"from_model": "part"}))
            call("import_tool_library", path=str(library), mode="add", format="camtools")
            for kind, params in operations:
                call("add_operation", kind=kind, name=kind, tool=result.tools[0]["name"], source="part", params=params)
                call("generate", name=kind)
                report = json.loads(call("verify", name=kind))
                self.assertEqual(report["operations"][0]["status"], "ok")
                out += [f["kind"] for f in report["findings"] if f["blocks_post"]]
        return set(out)

    BAR = [(0, 0), (20, 0), (20, 40), (0, 40)]

    def test_turning_insert(self):
        for hand in "RL":
            self.assertEqual(self.findings(
                lathe("turning general", holder={"HAND": hand}), self.BAR,
                [("facing", {}), ("profile_rough", {}), ("profile_finish", {})],
                stock={"diameter": 44, "height": 41}), set())

    def test_boring_bar_is_held_where_it_does_not_fit(self):
        def bore(hole, change=None):  # the bar reaches 14.5 + 20/2 = 24.5 mm
            return self.findings(lathe("turning boring", holder={"OAL": 80}),
                                 [(hole / 2, 0), (20, 0), (20, 40), (hole / 2, 40)], [("boring_turning", {})], change=change)
        self.assertEqual(bore(26), set())
        self.assertIn("shank_hit", bore(24.2))
        # why not the shank's own 20 mm: CamCraft would pass a hole the bar cannot enter
        self.assertEqual(bore(22, lambda d: d.update(diameter=20.0, shank_d=20.0)), set())

    def test_blade(self):
        groove = [(0, 0), (20, 0), (20, 18), (16, 18), (16, 22), (20, 22), (20, 40), (0, 40)]
        window = {k: {"reference": "bottom_of_model", "offset": z} for k, z in (("bottom_z", 18.0), ("top_z", 22.0))}
        self.assertEqual(self.findings(lathe("turning grooving"), groove, [("groove_finish", window)]), set())

    def test_threading_insert(self):
        depth, half = 1.08, 1.08 * math.tan(math.radians(30))
        valleys = [8.0 + 2 * i for i in range(12)]
        profile = [(0, 0), (20, 0)]
        for z in valleys:
            profile += [(20, z - half), (20 - depth, z), (20, z + half)]
        params = {"pitch": 2.0, "depth": depth, "bottom_z": {"reference": "bottom_of_model", "offset": valleys[0]},
                  "top_z": {"reference": "bottom_of_model", "offset": valleys[-1]}}
        self.assertEqual(self.findings(lathe("turning threading"), profile + [(20, 40), (0, 40)],
                                       [("threading", params)]), set())


if __name__ == "__main__":
    unittest.main()
