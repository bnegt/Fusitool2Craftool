import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from fusitool2craftool import camtools
from fusitool2craftool.cli import main
from fusitool2craftool.convert import Options, convert, why_not_sane
from fusitool2craftool.verify import verify_file
from fusitool2craftool.fusion import FusionLibraryError, load_library, parse_library

FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE = Path(__file__).parent.parent / "private"


def tool(type_="flat end mill", unit="millimeters", geometry=None, presets=None, **extra):
    entry = {
        "type": type_,
        "unit": unit,
        "description": extra.pop("description", f"{type_} test"),
        "geometry": {"DC": 6, "LCF": 18, "LB": 30, "OAL": 50, "SFDM": 6, "NOF": 2, **(geometry or {})},
        "start-values": {"presets": presets if presets is not None else [{"name": "Default", "n": 12000, "v_f": 1200}]},
        "post-process": {"number": 1},
    }
    entry.update(extra)
    return entry


def one(entry, options=None):
    result = convert([entry], options)
    assert not result.skipped, result.skipped
    return result.tools[0]["definition"], result


class ConvertTests(unittest.TestCase):
    def test_flat_end_mill_metric(self):
        d, _ = one(tool())
        self.assertEqual(d["kind"], "end_mill")
        self.assertEqual(d["diameter"], 6)
        self.assertEqual(d["flute_len"], 18)
        self.assertEqual(d["shank_len"], 12)  # LB - LCF
        self.assertEqual((d["rpm"], d["feed"]), (12000, 1200))
        self.assertEqual(d["flutes"], 2)
        self.assertEqual(d["number"], 1)

    def test_inch_tool_converted_to_mm(self):
        e = tool("ball end mill", unit="inches",
                 geometry={"DC": 0.125, "LCF": 0.5, "LB": 0.8, "SFDM": 0.125, "RE": 0.0625},
                 presets=[{"name": "Rough", "n": 8000, "v_f": 30, "stepdown": 0.125,
                           "use-stepdown": True, "stepover": 0.03, "use-stepover": True}])
        d, result = one(e)
        self.assertEqual(d["kind"], "ball_nose")
        self.assertAlmostEqual(d["diameter"], 3.175)
        self.assertAlmostEqual(d["corner_r"], 1.5875)
        self.assertAlmostEqual(d["feed"], 762.0)
        row = d["cutting_data"][0]
        self.assertEqual(row["material"], "unknown")
        self.assertAlmostEqual(row["stepdown"], 3.175)
        self.assertAlmostEqual(row["stepover"], 0.762)
        self.assertEqual(result.version, 3)

    def test_chamfer_mill_half_angle_doubled(self):
        d, _ = one(tool("chamfer mill", geometry={"TA": 45}))
        self.assertEqual((d["kind"], d["tip_angle"]), ("taper", 90))

    def test_counter_sink_uses_included_angle(self):
        d, _ = one(tool("counter sink", geometry={"SIG": 90}))
        self.assertEqual((d["kind"], d["tip_angle"]), ("taper", 90))

    def test_drill_default_point_angle(self):
        d, result = one(tool("drill"))
        self.assertEqual(d["tip_angle"], 118)
        self.assertTrue(any("118" in w for w in result.warnings))

    def test_left_hand_tap_needs_version_2(self):
        d, result = one(tool("tap left hand", geometry={"TP": 1.0}))
        self.assertEqual((d["kind"], d["pitch"], d["thread_hand"]), ("tap", 1.0, "left"))
        self.assertEqual(result.version, 2)

    def test_bull_nose_and_corner_radius_end_mill(self):
        d, _ = one(tool("bull nose end mill", geometry={"RE": 0.5}))
        self.assertEqual((d["kind"], d["corner_r"]), ("bull_nose", 0.5))
        d, _ = one(tool(geometry={"RE": 1}))
        self.assertEqual((d["kind"], d["corner_r"]), ("bull_nose", 1))

    def test_rounded_ball_radius_stays_ball(self):
        d, result = one(tool("ball end mill", geometry={"DC": 3.175, "RE": 1.5}))
        self.assertEqual((d["kind"], d["corner_r"]), ("ball_nose", 1.5875))
        d, _ = one(tool("ball end mill", geometry={"DC": 6, "RE": 1}))
        self.assertEqual((d["kind"], d["corner_r"]), ("bull_nose", 1))

    def test_roundover_bit_is_skipped(self):
        r = convert([tool("ball end mill", geometry={"DC": 6.35, "RE": 6.35})])
        self.assertEqual(r.tools, [])
        self.assertIn("exceeds half", r.skipped[0].reason)

    def test_unsupported_type_and_holders(self):
        r = convert([tool("tapered mill"), {"type": "holder", "description": "ER32"}, tool()])
        self.assertEqual(len(r.tools), 1)
        self.assertEqual(len(r.skipped), 1)
        self.assertIn("tapered mill", r.skipped[0].reason)

    def test_no_preset_skipped_unless_defaults(self):
        r = convert([tool(presets=[])])
        self.assertIn("spindle speed", r.skipped[0].reason)
        d, _ = one(tool(presets=[]), Options(default_rpm=10000, default_feed=800))
        self.assertEqual((d["rpm"], d["feed"]), (10000, 800))

    def test_preset_by_name_and_material(self):
        presets = [{"name": "Rough", "n": 1000, "v_f": 100}, {"name": "Finish", "n": 2000, "v_f": 200}]
        d, result = one(tool(presets=presets), Options(preset="finish", material="brass"))
        self.assertEqual((d["rpm"], d["feed"]), (2000, 200))
        self.assertEqual(d["material_group"], "brass")
        self.assertEqual(d["cutting_data"], [{"material": "brass", "rpm": 2000, "feed": 200}])
        self.assertEqual(result.version, 1)

    def test_unknown_material_refused(self):
        with self.assertRaises(ValueError):
            convert([tool()], Options(material="unobtainium"))

    def test_holder_segments(self):
        e = tool(holder={"unit": "millimeters", "segments": [
            {"height": 10, "lower-diameter": 30, "upper-diameter": 40},
            {"height": 20, "lower-diameter": 40, "upper-diameter": 40}]})
        d, _ = one(e)
        self.assertEqual((d["holder_d"], d["holder_len"]), (40, 30))

    def test_missing_holder_uses_defaults(self):
        d, result = one(tool(), Options(holder_d=44, holder_len=35))
        self.assertEqual((d["holder_d"], d["holder_len"]), (44, 35))
        self.assertTrue(any("holder" in w for w in result.warnings))

    def test_names_fallback_and_unique(self):
        a = tool(description="")
        b = tool(description="")
        b["post-process"] = {"number": 2}
        r = convert([a, b, tool(description="Same"), tool(description="Same")])
        names = sorted(t["name"] for t in r.tools)
        self.assertEqual(len(set(names)), 4)
        self.assertIn("T1 flat end mill D6mm", names)
        self.assertIn("T2 flat end mill D6mm", names)

    def test_catalog_text_trimmed(self):
        d, _ = one(tool(vendor="  Acme\tTools  ", **{"product-id": "x" * 200}))
        self.assertEqual(d["vendor"], "Acme Tools")
        self.assertEqual(len(d["part_number"]), 80)


class FileTests(unittest.TestCase):
    def test_fixture_library_round_trip(self):
        entries = load_library(FIXTURES / "sample.json")
        result = convert(entries)
        doc = json.loads(camtools.to_json(result))
        self.assertEqual(doc["format"], "camcraft-tools")
        self.assertEqual(doc["units"], "mm")
        self.assertTrue(doc["tools"])
        for t in doc["tools"]:
            self.assertIsNone(why_not_sane(t["definition"]), t["name"])

    def test_zipped_tools_file(self):
        text = (FIXTURES / "sample.json").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lib.tools"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("tools.json", text)
            self.assertEqual(load_library(path), parse_library(text))

    def test_not_a_library(self):
        with self.assertRaises(FusionLibraryError):
            parse_library('{"tools": []}')


class SkipCategoryTests(unittest.TestCase):
    def test_tapered_ball_and_roundover_named(self):
        r = convert([
            tool("tapered mill", geometry={"RE": 0.5, "TA": 5}),
            tool("ball end mill", geometry={"DC": 6.35, "RE": 6.35}),
            tool(),
        ])
        self.assertEqual(sorted(s.category for s in r.skipped), ["roundover bit", "tapered ball mill"])

    def test_cli_prints_red_skip_summary(self):
        lib = {"data": [tool("tapered mill", geometry={"RE": 0.5}), tool()]}
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "lib.json"
            src.write_text(json.dumps(lib))
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}), \
                    redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(src)]), 0)
        text = err.getvalue()
        self.assertIn("\033[31mNOT IMPORTED: 1 tapered ball mill", text)
        self.assertIn("\033[31mskipped [tapered ball mill]", text)


class VerifyTests(unittest.TestCase):
    def write(self, tmp, entries):
        result = convert(entries)
        path = camtools.write(result, Path(tmp) / "out.camtools")
        return result, path

    def test_clean_output_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, path = self.write(tmp, load_library(FIXTURES / "sample.json"))
            self.assertEqual(verify_file(path, result), [])

    def test_changed_value_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, path = self.write(tmp, [tool(unit="inches", geometry={"DC": 0.25, "LCF": 0.5, "LB": 1})])
            doc = json.loads(path.read_text())
            doc["tools"][0]["definition"]["diameter"] = 0.25  # inch value left unconverted
            doc["tools"][0]["definition"]["feed"] = 1
            path.write_text(json.dumps(doc))
            problems = verify_file(path, result)
            self.assertTrue(any("diameter" in p and "6.35" in p for p in problems), problems)
            self.assertTrue(any("match no Fusion preset" in p for p in problems), problems)

    def test_missing_tool_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, path = self.write(tmp, [tool(description="A"), tool(description="B")])
            doc = json.loads(path.read_text())
            doc["tools"].pop()
            path.write_text(json.dumps(doc))
            self.assertTrue(any("holds 1 tools, expected 2" in p for p in verify_file(path, result)))


@unittest.skipUnless((PRIVATE / "1335.tools").exists(), "private library not present")
class PrivateLibraryTests(unittest.TestCase):
    """Sanity check against the owner's own Fusion library (git-ignored)."""

    def test_every_tool_converts(self):
        result = convert(load_library(PRIVATE / "1335.tools"))
        self.assertEqual(result.skipped, [])
        self.assertTrue(result.tools)
        for t in result.tools:
            self.assertIsNone(why_not_sane(t["definition"]), t["name"])
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "1335.camtools")
            self.assertEqual(verify_file(path, result), [])


if __name__ == "__main__":
    unittest.main()
