"""Import converted libraries into a real, headless CamCraft.

Needs ``camcraft-cli`` (ships with CamCraft): set ``CAMCRAFT_CLI`` to its
path, or have it on ``PATH``. Skipped otherwise. Each file is imported with
``camcraft-cli mcp`` (``push(..., port=None)``) - CamCraft's own reader and
tool laws judge it, no window is touched - and every tool we wrote must come
back as added, none renamed or skipped.

Covers the sample fixture, an edge-case library built here, and every
Fusion library under private/ (see TEST_LIBRARIES.md).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fusitool2craftool import camtools
from fusitool2craftool.cli import library_files
from fusitool2craftool.convert import Options, convert
from fusitool2craftool.fusion import load_library
from fusitool2craftool.push import push

from .test_convert import FIXTURES, PRIVATE, corner_chamfer, single_form, tool

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
        star,
        tool(description="drawn holder", unit="inches", geometry={"DC": 0.5, "LCF": 1, "LB": 2, "SFDM": 0.5},
             holder={"unit": "inches", "segments": [
                 {"height": 0.5, "lower-diameter": 1.0, "upper-diameter": 1.25},
                 {"height": 1.0, "lower-diameter": 1.25, "upper-diameter": 1.25},
                 {"height": 0.25, "lower-diameter": 1.0, "upper-diameter": 1.0},
                 {"height": 0.5, "lower-diameter": 2.5, "upper-diameter": 2.5}]}),
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
                          kind_defaults={"drill": {"vc": 60, "feed": 150}, "t_slot": {"vc": 60, "fz": 0.005}})
        self.assertEqual(convert(entries, options).skipped, [])
        self.assert_imports(entries, options)

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
                   {"name": "Hardwood", "n": 16000, "v_f": 3000}]  # unreleased word: left out
        entries = [tool(description=f"multi {i}", presets=presets) for i in range(2)] + edge_cases()
        self.assert_imports(entries, Options(all_presets=True))

    def test_private_libraries(self):
        files = library_files(PRIVATE) if PRIVATE.is_dir() else []
        if not files:
            self.skipTest("no Fusion libraries in private/")
        for src in files:
            for options in (Options(), Options(all_presets=True)):
                with self.subTest(library=str(src.relative_to(PRIVATE)), all_presets=options.all_presets):
                    self.assert_imports(load_library(src), options)


if __name__ == "__main__":
    unittest.main()
