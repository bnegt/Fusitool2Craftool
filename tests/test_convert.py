import io
import math
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
from fusitool2craftool.convert import Options, convert, preset_material, why_not_sane
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

    def test_legacy_star_start_values(self):
        # format-version-1 libraries: one unnamed set under "*", no presets list
        e = tool(unit="inches", geometry={"DC": 0.25, "LCF": 0.5, "LB": 1})
        e["start-values"] = {"*": {"n": 5000, "v_f": 39.3701, "f_z": 0.0039}}
        d, result = one(e)
        self.assertEqual(d["rpm"], 5000)
        self.assertAlmostEqual(d["feed"], 1000.0, places=2)
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_drill_feed_from_plunge_or_feed_per_rev(self):
        # Fusion leaves a drill's v_f at 0 and keeps its feed in v_f_plunge or f_n
        d, _ = one(tool("drill", geometry={"SIG": 118},
                        presets=[{"name": "P", "n": 3000, "v_f": 0, "v_f_plunge": 20}]))
        self.assertEqual(d["feed"], 20)
        d, result = one(tool("drill", unit="inches", geometry={"DC": 0.25, "LCF": 1, "LB": 2, "SIG": 118},
                             presets=[{"name": "P", "n": 2000, "f_n": 0.003}]))
        self.assertAlmostEqual(d["feed"], 0.003 * 2000 * 25.4)
        self.assertTrue(any("f_n x n" in w for w in result.warnings))
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_tap_feed_is_rpm_times_pitch(self):
        e = tool("tap right hand", geometry={"TP": 1.25, "DC": 8},
                 presets=[{"name": "P", "n": 500, "v_f": 0, "v_f_plunge": 0}])
        d, result = one(e)
        self.assertEqual(d["feed"], 625)
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_mill_without_v_f_still_skipped(self):
        r = convert([tool(presets=[{"name": "P", "n": 3000, "v_f": 0, "v_f_plunge": 300}])])
        self.assertIn("no cutting feed", r.skipped[0].reason)

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

    def test_unreleased_material_needs_flag(self):
        # CamCraft 0.25.0 refuses a library naming "mdf"
        with self.assertRaisesRegex(ValueError, "--unreleased-materials"):
            convert([tool()], Options(material="mdf"))
        d, _ = one(tool(), Options(material="mdf", unreleased_materials=True))
        self.assertEqual(d["cutting_data"][0]["material"], "mdf")

    def test_default_rpm_for_preset_without_speed_verifies(self):
        result = convert([tool(presets=[{"name": "Wood", "v_f": 900}])], Options(default_rpm=18000))
        self.assertEqual(result.skipped, [])
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_near_ball_bull_nose_is_ball(self):
        # RobbJack lists D24.9999 R12.5001 bull noses: a ball, vendor-rounded
        d, result = one(tool("bull nose end mill", geometry={"DC": 24.9999, "RE": 12.5001, "LCF": 30, "LB": 60}))
        self.assertEqual((d["kind"], d["corner_r"]), ("ball_nose", 12.49995))
        self.assertTrue(any("imported as a ball nose" in w for w in result.warnings))
        d, result = one(tool("bull nose end mill", geometry={"DC": 10, "RE": 5}))
        self.assertEqual((d["kind"], result.warnings[-1:] and "ball" in result.warnings[-1]), ("ball_nose", False))
        d, _ = one(tool("bull nose end mill", geometry={"DC": 10, "RE": 4.5}))
        self.assertEqual((d["kind"], d["corner_r"]), ("bull_nose", 4.5))  # a real near-ball bull nose
        r = convert([tool("bull nose end mill", geometry={"DC": 10, "RE": 6})])
        self.assertEqual(r.skipped[0].category, "roundover bit")
        with tempfile.TemporaryDirectory() as tmp:
            res = convert([tool("bull nose end mill", geometry={"DC": 24.9999, "RE": 12.5001, "LCF": 30, "LB": 60})])
            path = camtools.write(res, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, res), [])

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

    def test_slitting_saw_is_t_slot(self):
        # RobbJack-style saw: 0.75" head, 0.002" thick, on a 0.25" arbor stub
        e = tool("slot mill", unit="inches", description="saw 0.002",
                 geometry={"DC": 0.75, "LCF": 0.002, "SFDM": 0.25, "LB": 0.252, "OAL": 0.252,
                           "shoulder-diameter": 0.25, "NOF": 10})
        d, result = one(e)
        self.assertEqual(d["kind"], "t_slot")
        self.assertAlmostEqual(d["diameter"], 19.05)
        self.assertAlmostEqual(d["flute_len"], 0.0508)
        self.assertAlmostEqual(d["shank_d"], 6.35)
        self.assertAlmostEqual(d["shank_len"], 6.35)
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_keyseat_by_name_and_neck_is_the_wider(self):
        d, _ = one(tool("slot mill", description="Woodruff keyseat #606",
                        geometry={"DC": 19, "LCF": 4.8, "SFDM": 8, "shoulder-diameter": 6, "LB": 30}))
        self.assertEqual(d["kind"], "keyseat")
        self.assertEqual(d["shank_d"], 8)  # wider of shaft and shoulder: never under-reports the body

    def test_undercut_neck_not_under_head_skipped(self):
        r = convert([tool("slot mill", geometry={"DC": 2, "LCF": 1, "SFDM": 6, "LB": 20})])
        self.assertIn("neck (shank diameter) 6", r.skipped[0].reason)

    def test_lollipop_flute_capped_where_neck_leaves_ball(self):
        e = tool("lollipop mill", unit="inches", description="3/8 lollipop",
                 geometry={"DC": 0.375, "LCF": 0.35, "SFDM": 0.24, "LB": 2.15, "OAL": 5})
        d, result = one(e)
        self.assertEqual(d["kind"], "lollipop")
        self.assertLess(d["flute_len"], 0.35 * 25.4)
        self.assertIsNone(why_not_sane(d))
        self.assertTrue(any("capped" in w for w in result.warnings))
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_lollipop_flute_under_ball_centre_skipped(self):
        r = convert([tool("lollipop mill", geometry={"DC": 6, "LCF": 2, "SFDM": 4, "LB": 30})])
        self.assertIn("from 3 mm (the ball's centre)", r.skipped[0].reason)

    def test_dovetail_angle_is_off_the_axis(self):
        # Thermwood "14 Deg x 3/4 Dia x 3/4" router dovetail on a 1/2" shank
        e = tool("dovetail mill", unit="inches", description="Dovetail 14 Deg x 3/4 Dia",
                 geometry={"DC": 0.75, "LCF": 0.75, "SFDM": 0.5, "LB": 1.6, "TA": 14})
        d, result = one(e)
        self.assertEqual(d["kind"], "dovetail")
        self.assertEqual(d["tip_angle"], 76)  # CamCraft: off the bottom face
        # the flank reaches the 1/2" shank diameter at 0.125"/tan(14) = 0.501"
        self.assertAlmostEqual(d["flute_len"], 0.125 / math.tan(math.radians(14)) * 25.4, places=4)
        self.assertTrue(any("capped" in w for w in result.warnings))
        self.assertIsNone(why_not_sane(d))
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_dovetail_uncapped_and_bad_angle(self):
        d, _ = one(tool("dovetail mill", geometry={"DC": 25.4, "LCF": 25.4, "SFDM": 12.7, "LB": 38, "TA": 14}))
        self.assertEqual(d["flute_len"], 25.4)  # flank stays outside the neck: no cap
        r = convert([tool("dovetail mill", geometry={"DC": 20, "SFDM": 10, "TA": 0})])
        self.assertIn("taper angle (TA)", r.skipped[0].reason)

    def test_reamer_counterbore_form_mill_named(self):
        r = convert([tool("reamer"), tool("counter bore"), tool("form mill"), tool("probe")])
        self.assertEqual([s.category for s in r.skipped], ["reamer", "counterbore", "form mill", None])

    def test_holder_only_library_is_not_an_error(self):
        lib = {"data": [{"type": "holder", "description": "BT30 ER32"}, {"type": "holder"}]}
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "holders.json"
            src.write_text(json.dumps(lib))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(src)]), 0)
            self.assertIn("2 holders not converted", out.getvalue())
            self.assertFalse(src.with_suffix(".camtools").exists())

    def test_all_tools_skipped_is_still_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "lib.json"
            src.write_text(json.dumps({"data": [tool("reamer")]}))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(src)]), 1)

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


class PresetRowTests(unittest.TestCase):
    """--all-presets: one cutting data row per preset naming a material."""

    def test_preset_names(self):
        cases = {
            "6061(T4-T6) Aluminum (RobbJack tested)": "aluminium_6061",
            "4140 Steel (RobbJack tested)": "mild_steel",
            "Stainless Steel - Drilling": "stainless_304",
            "Gray Iron (RobbJack tested)": "grey_cast_iron",
            "Free-Cutting Brass": "brass",
            "Acetal": "plastics_pom",
            "MDF/Laminate": "mdf",
            "P20 (30-40hrc) Tool Steel": None,   # hardened: not mild steel
            "440C (30-40hrc) Stainless": None,
            "6AL-4V Titanium": None,             # no CamCraft group
            "Aluminum/Copper/Brass": None,       # ambiguous
            "Default preset": None,
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(preset_material(name), want)

    def presets(self):
        return [
            {"name": "6061 Aluminum", "n": 18000, "v_f": 2000, "stepdown": 3, "use-stepdown": True},
            {"name": "4140 Steel", "n": 6000, "v_f": 500},
            {"name": "Aluminum finish", "n": 20000, "v_f": 1500},   # second aluminium: reported
            {"name": "Titanium", "n": 3000, "v_f": 200},            # no group: reported
            {"name": "Hardwood", "n": 16000, "v_f": 3000},          # unreleased word: reported
            {"name": "Acetal", "n": 12000},                         # no feed: reported
        ]

    def test_rows_per_material(self):
        result = convert([tool(presets=self.presets())], Options(all_presets=True))
        rows = result.tools[0]["definition"]["cutting_data"]
        self.assertEqual([r["material"] for r in rows], ["unknown", "aluminium_6061", "mild_steel"])
        self.assertEqual(rows[1], {"material": "aluminium_6061", "rpm": 18000, "feed": 2000, "stepdown": 3})
        outcomes = {name: outcome for (name, outcome) in result.presets}
        self.assertEqual(outcomes["Aluminum finish"], "duplicate aluminium_6061")
        self.assertEqual(outcomes["Titanium"], "unmatched")
        self.assertIn("newer than 0.25.0", outcomes["Hardwood"])
        self.assertEqual(outcomes["Acetal"], "no speeds")
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_material_option_keeps_chosen_preset(self):
        # --material names the chosen (first) preset; a preset mapping to the same word is a duplicate
        result = convert([tool(presets=self.presets())],
                         Options(all_presets=True, material="brass", unreleased_materials=True))
        rows = result.tools[0]["definition"]["cutting_data"]
        self.assertEqual([r["material"] for r in rows], ["brass", "mild_steel", "aluminium_6061", "hardwood"])
        self.assertEqual(rows[0]["rpm"], 18000)

    def test_without_flag_no_extra_rows(self):
        d, _ = one(tool(presets=self.presets()))
        self.assertEqual([r["material"] for r in d["cutting_data"]], ["unknown"])

    def test_cli_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "lib.json"
            src.write_text(json.dumps({"data": [tool(presets=self.presets())]}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(src), "--all-presets"]), 0)
        text = out.getvalue()
        self.assertIn("'4140 Steel' -> mild_steel (1 tool)", text)
        self.assertIn("'Titanium' -> unmatched (1 tool)", text)


class FolderTests(unittest.TestCase):
    """A folder of libraries, as ExportAllToolLibraries writes it."""

    def make_export(self, tmp):
        root = Path(tmp) / "export"
        (root / "Local").mkdir(parents=True)
        (root / "Cloud" / "Shop").mkdir(parents=True)
        (root / "Local" / "Mills.json").write_text(json.dumps({"data": [tool(description="A")]}))
        (root / "Cloud" / "Shop" / "Drills.json").write_text(
            json.dumps({"data": [tool("drill", description="D", geometry={"SIG": 118})]}))
        (root / "Cloud" / "Holders.json").write_text(json.dumps({"data": [{"type": "holder"}]}))
        (root / "export-log.txt").write_text("exported 3 libraries, 0 failed\n")
        return root

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_converts_every_library_next_to_its_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_export(tmp)
            code, out, err = self.run_main([str(root)])
            self.assertEqual(code, 0, err)
            self.assertTrue((root / "Local" / "Mills.camtools").exists())
            self.assertTrue((root / "Cloud" / "Shop" / "Drills.camtools").exists())
            self.assertFalse((root / "Cloud" / "Holders.camtools").exists())
            self.assertIn("3 libraries: 2 converted, 1 without convertible tools, 0 failed", out)
            # a second run does not pick up its own .camtools output
            self.assertEqual(self.run_main([str(root)])[0], 0)

    def test_output_folder_mirrors_subfolders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_export(tmp)
            dest = Path(tmp) / "camtools"
            code, _, err = self.run_main([str(root), "-o", str(dest)])
            self.assertEqual(code, 0, err)
            self.assertEqual(
                sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*.camtools")),
                ["Cloud/Shop/Drills.camtools", "Local/Mills.camtools"])
            self.assertFalse(list(root.rglob("*.camtools")))

    def test_bad_library_fails_the_run_but_others_convert(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_export(tmp)
            (root / "Local" / "Broken.json").write_text("not json")
            code, out, err = self.run_main([str(root)])
            self.assertEqual(code, 1)
            self.assertIn("1 failed", err)
            self.assertTrue((root / "Local" / "Mills.camtools").exists())

    def test_push_refused_for_a_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.run_main([str(self.make_export(tmp)), "--push", "7777"])
            self.assertEqual(code, 1)
            self.assertIn("one library at a time", err)

    def test_empty_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.run_main([tmp])
            self.assertEqual(code, 1)
            self.assertIn("no .json or .tools files", err)


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


def _private_libraries() -> list[Path]:
    from fusitool2craftool.cli import library_files
    return library_files(PRIVATE) if PRIVATE.is_dir() else []


# Third-party libraries (see TEST_LIBRARIES.md): they may hold
# tool types we do not map yet, so only conversion and verification count.
THIRD_PARTY = PRIVATE / "test-libraries"


@unittest.skipUnless(_private_libraries(), "no Fusion libraries in private/")
class PrivateLibraryTests(unittest.TestCase):
    """Every Fusion library under private/ (git-ignored): each converts,
    passes CamCraft's sanity rules and verifies. In the owner's own
    libraries (anything outside test-libraries/) a skipped tool must also
    be a named unsupported family."""

    def test_every_library_converts_and_verifies(self):
        for src in _private_libraries():
            with self.subTest(library=str(src.relative_to(PRIVATE))):
                result = convert(load_library(src))
                if not src.is_relative_to(THIRD_PARTY):
                    unexplained = [f"{s.name}: {s.reason}" for s in result.skipped if not s.category]
                    self.assertEqual(unexplained, [])
                if not result.tools:
                    continue  # holder-only / empty library
                for t in result.tools:
                    self.assertIsNone(why_not_sane(t["definition"]), t["name"])
                with tempfile.TemporaryDirectory() as tmp:
                    path = camtools.write(result, Path(tmp) / "out.camtools")
                    self.assertEqual(verify_file(path, result), [])


if __name__ == "__main__":
    unittest.main()
