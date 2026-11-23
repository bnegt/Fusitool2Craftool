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

from .test_convert import FIXTURES, PRIVATE, tool

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
        tool(description="tap M8 left", type_="tap left hand", geometry={"DC": 8, "TP": 1.25},
             presets=[{"name": "P", "n": 500}]),
        tool(description="thread mill", type_="thread mill", geometry={"DC": 10, "LCF": 15, "TP": 1.5}),
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

    def test_sample_fixture(self):
        self.assert_imports(load_library(FIXTURES / "sample.json"))

    def test_edge_cases(self):
        self.assertEqual(convert(edge_cases()).skipped, [])
        self.assert_imports(edge_cases())

    def test_duplicate_fusion_numbers_are_fine(self):
        # .camtools numbers are not imported (CamCraft numbers the tools
        # itself), so tools sharing a Fusion T number do not clash.
        self.assert_imports([tool(description="A"), tool(description="B")])

    def test_edge_cases_with_material_rows(self):
        self.assert_imports(edge_cases(), Options(material="aluminium_6061"))

    def test_private_libraries(self):
        files = library_files(PRIVATE) if PRIVATE.is_dir() else []
        if not files:
            self.skipTest("no Fusion libraries in private/")
        for src in files:
            with self.subTest(library=str(src.relative_to(PRIVATE))):
                self.assert_imports(load_library(src))


if __name__ == "__main__":
    unittest.main()
