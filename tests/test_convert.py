import dataclasses
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
from fusitool2craftool.convert import (KINDS, RELEASED_MATERIALS, Options, convert, find_holder, preset_material,
                                       unsupported_category, why_not_sane)
from fusitool2craftool.verify import compare, verify_file
from fusitool2craftool.fusion import FusionLibraryError, load_library, parse_library

FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE = Path(__file__).parent.parent / "private"
# Every tool type in Autodesk's schema:
# https://cam.autodesk.com/tools/tools/Tooling%20Schema/ToolLibrary.schema.json
FUSION_TYPES = """
ball end mill, boring bar, bull nose end mill, center drill, chamfer mill, circle segment barrel,
circle segment lens, circle segment oval, circle segment taper, corner chamfer end mill, counter bore,
counter sink, dovetail mill, drill, electric arc wire, face mill, flat end mill, form mill, holder,
laser cutter, laser powder, laser wire, lollipop mill, plasma cutter, probe, radius mill, reamer, shaft,
slot mill, spot drill, tap left hand, tap right hand, tapered mill, thread mill, tool block,
turning boring, turning general, turning grooving, turning threading, waterjet
""".replace("\n", " ").strip().split(", ")


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


def single_form(geometry=None, **extra):
    """A single-form thread mill as Fusion stores it: NT 1, no LCF (#16)."""
    e = tool("thread mill", **extra)
    e["geometry"] = {"DC": 3.9, "NOF": 1, "NT": 1, "OAL": 52, "SFDM": 4, "TPN": 0.5, "TPX": 0.8,
                     "shoulder-length": 16, "thread-profile-angle": 60, "thread-tip-type": "point",
                     **(geometry or {})}
    return e


def corner_chamfer(geometry=None, **extra):
    # No real sample exists yet (#18): built from Autodesk's schema.
    return tool("corner chamfer end mill",
                geometry={"DC": 10, "SFDM": 10, "chamfer-width": 0.5, "chamfer-angle": 45, **(geometry or {})}, **extra)


def verify(entries, options=None, change=None):
    """Write, optionally damage, and check a library; the list of problems."""
    result = convert(entries, options)
    if change:
        for t in result.tools:
            change(t["definition"])
    with tempfile.TemporaryDirectory() as tmp:
        return verify_file(camtools.write(result, Path(tmp) / "lib.camtools"), result)


class SingleFormThreadMillTests(unittest.TestCase):
    def test_pitch_range_and_tooth(self):
        d, result = one(single_form())
        self.assertEqual(d["kind"], "thread_mill_single")
        self.assertEqual((d["pitch_min"], d["pitch"], d["flute_len"], d["tip_angle"]), (0.5, 0.8, 0.8, 60))
        self.assertEqual(result.version, 1)

    def test_neck_is_the_shank_and_the_shank_is_drawn_above_it(self):
        d, result = one(single_form())
        self.assertAlmostEqual(d["shank_d"], 3.9 - 2 * 0.5413 * 0.8, places=6)
        self.assertAlmostEqual(d["shank_len"], 16 - 0.8)
        self.assertEqual((d["holder_d"], d["holder_len"]), (50, 36 + 40))  # encloses shank + default holder
        self.assertEqual(d["body"]["spun"], [[16, 0, 0], [16, 2, 0], [52, 2, 0], [52, 25, 0], [92, 25, 0], [92, 0, 0]])
        self.assertTrue(any("no neck diameter" in w for w in result.warnings))

    def test_stated_neck_and_fusion_holder(self):
        e = single_form({"shoulder-diameter": 2.5},
                        holder={"unit": "inches", "segments": [
                            {"height": 1, "lower-diameter": 1, "upper-diameter": 2}]})
        d, result = one(e)
        self.assertEqual(d["shank_d"], 2.5)
        self.assertFalse(any("no neck diameter" in w for w in result.warnings))
        self.assertEqual(d["body"]["spun"][1:3], [[16, 2, 0], [52, 2, 0]])
        self.assertEqual((d["holder_d"], d["holder_len"]), (50.8, 61.4))
        self.assertEqual(verify([e]), [])

    def test_inches(self):
        e = single_form({"DC": 0.3, "OAL": 2.5, "SFDM": 0.375, "TPN": 1 / 32, "TPX": 1 / 16, "shoulder-length": 1},
                        unit="inches")
        d, _ = one(e)
        self.assertAlmostEqual(d["pitch"], 25.4 / 16, places=6)
        self.assertAlmostEqual(d["flute_len"] + d["shank_len"], 25.4, places=5)
        self.assertEqual(verify([e]), [])

    def test_no_reach_keeps_the_shank(self):
        e = single_form()
        del e["geometry"]["shoulder-length"]
        d, _ = one(e)
        self.assertEqual((d["shank_d"], d["shank_len"]), (4, 51.2))
        self.assertNotIn("body", d)
        self.assertEqual(verify([e]), [])

    def test_holder_cylinders(self):
        options = Options(holder_bodies=False)
        d, _ = one(single_form(), options)
        self.assertNotIn("body", d)
        self.assertEqual((d["holder_d"], d["holder_len"]), (50, 76))
        self.assertEqual(verify([single_form()], options), [])

    def test_one_pitch(self):
        e = single_form({"TPN": 0, "TPX": 0.8})
        d, _ = one(e)
        self.assertEqual((d["pitch_min"], d["pitch"]), (0.8, 0.8))
        self.assertEqual(verify([e]), [])

    def test_refusals(self):
        for geometry, why in (({"TPN": 1.0}, "finest pitch"), ({"DC": 0.9}, "no core"),
                              ({"TPX": None}, "no pitch"), ({"NT": 3}, "no flute length")):
            with self.subTest(geometry=geometry):
                self.assertIn(why, convert([single_form(geometry)]).skipped[0].reason)

    def test_with_flute_length_stays_multi_form(self):
        d, _ = one(tool("thread mill", geometry={"DC": 10, "LCF": 15, "TP": 1.5, "NT": 1}))
        self.assertEqual(d["kind"], "thread_mill")
        self.assertNotIn("pitch_min", d)

    def test_output_check_catches_damage(self):
        self.assertEqual(verify([single_form()]), [])
        for key, value in (("kind", "thread_mill"), ("pitch", 1.0), ("pitch_min", 0.8), ("flute_len", 2.0),
                           ("shank_d", 2.0), ("shank_len", 30.0), ("tip_angle", 55.0), ("holder_len", 40.0)):
            with self.subTest(key=key):
                self.assertTrue(verify([single_form()], change=lambda d: d.update({key: value})))

        def thin(d):  # the drawn shank narrower than Fusion's
            d["body"]["spun"][1][1] = d["body"]["spun"][2][1] = 1.5
        self.assertTrue(any("thinner" in p for p in verify([single_form()], change=thin)))

    def test_default_speeds(self):
        e = single_form(presets=[])
        options = Options(default_vc=60, default_fz=0.02, kind_defaults={"thread_mill_single": {"rpm": 5000}})
        d, _ = one(e, options)
        self.assertEqual((d["rpm"], d["feed"]), (5000, 100))
        self.assertEqual(verify([e], options), [])


def holder(description="ER16", unit="millimeters", segments=((20, 20, 30), (10, 40, 40)), **extra):
    """A stand-alone Fusion holder: (height, lower, upper diameter) from the tool end up."""
    return {"type": "holder", "unit": unit, "description": description, "segments": [
        {"height": h, "lower-diameter": lo, "upper-diameter": up} for h, lo, up in segments], **extra}


class DefaultHolderTests(unittest.TestCase):
    """#14: a stand-alone Fusion holder for tools that have none."""

    def test_seated_at_each_tools_own_stickout(self):
        options = Options(default_holder=holder())
        for lb in (30, 45):
            e = tool(geometry={"LB": lb})
            d, result = one(e, options)
            self.assertEqual((d["holder_d"], d["holder_len"]), (40, 30))
            self.assertEqual(d["body"]["spun"], [[lb, 0, 0], [lb, 10, 0], [lb + 20, 15, 0], [lb + 20, 20, 0],
                                                 [lb + 30, 20, 0], [lb + 30, 0, 0]])
            self.assertTrue(any("default holder 'ER16' used" in w for w in result.warnings))
            self.assertEqual(verify([e], options), [])

    def test_holder_units_are_its_own(self):
        options = Options(default_holder=holder(unit="inches", segments=((1, 1, 2),)))
        d, _ = one(tool(), options)
        self.assertEqual((d["holder_d"], d["holder_len"]), (50.8, 25.4))
        self.assertEqual(verify([tool()], options), [])
        metric = Options(default_holder=holder())
        inch = tool(unit="inches", geometry={"DC": 0.25, "LCF": 0.5, "LB": 1, "SFDM": 0.25})
        self.assertEqual(one(inch, metric)[0]["holder_d"], 40)
        self.assertEqual(verify([inch], metric), [])

    def test_a_tools_own_holder_stays(self):
        e = tool(holder={"unit": "millimeters", "segments": [
            {"height": 25, "lower-diameter": 22, "upper-diameter": 22}]})
        options = Options(default_holder=holder())
        d, result = one(e, options)
        self.assertEqual((d["holder_d"], d["holder_len"]), (22, 25))
        self.assertFalse(any("default holder" in w for w in result.warnings))
        self.assertEqual(verify([e], options), [])

    def test_empty_holder_counts_as_none(self):
        for own in ({"segments": []}, {"segments": [{"height": 0, "lower-diameter": 0, "upper-diameter": 0}]}):
            e = tool(holder=own)
            options = Options(default_holder=holder())
            self.assertEqual(one(e, options)[0]["holder_d"], 40)
            self.assertEqual(verify([e], options), [])

    def test_cylinders_and_single_form(self):
        options = Options(default_holder=holder(), holder_bodies=False)
        d, _ = one(tool(), options)
        self.assertNotIn("body", d)
        self.assertEqual((d["holder_d"], d["holder_len"]), (40, 30))
        self.assertEqual(verify([tool()], options), [])
        # above a single-form thread mill's neck: shank first, then the holder
        options = Options(default_holder=holder())
        d, _ = one(single_form(), options)
        self.assertEqual(d["body"]["spun"][1:4], [[16, 2, 0], [52, 2, 0], [52, 10, 0]])
        self.assertEqual((d["holder_d"], d["holder_len"]), (40, 66))
        self.assertEqual(verify([single_form()], options), [])

    def test_output_check_catches_damage(self):
        options = Options(default_holder=holder())
        self.assertTrue(verify([tool()], options, change=lambda d: d.update(holder_d=50.0)))

        def thin(d):
            d["body"]["spun"][1][1] = 5.0
        self.assertTrue(any("thinner" in p for p in verify([tool()], options, change=thin)))

    def test_find_holder(self):
        er16, er20 = holder(), holder("ER20", **{"product-id": "X-20"})
        entries = [tool(description="ER16"), er16, er20]
        self.assertIs(find_holder(" er16 ", entries), er16)
        self.assertIs(find_holder("x-20", entries), er20)
        self.assertIs(find_holder("ER16", [er16, holder()]), er16)  # the same shape twice is fine
        for name, among, why in (("ER99", entries, "'ER16', 'ER20'"), ("", entries, "no stand-alone holder"),
                                 ("ER16", [tool()], "no holders there"),
                                 ("ER16", [er16, holder(segments=((5, 9, 9),))], "different shapes"),
                                 ("ER16", [holder(segments=((20, 0, 30),))], "cannot be drawn")):
            with self.subTest(name=name, why=why), self.assertRaisesRegex(ValueError, why):
                find_holder(name, among)

    def test_holder_without_unit_is_mm_on_inch_tools_too(self):
        options = Options(default_holder=find_holder("ER16", [holder(unit=None)]))
        e = tool(unit="inches", geometry={"DC": 0.25, "LCF": 0.5, "LB": 1, "SFDM": 0.25})
        self.assertEqual(one(e, options)[0]["holder_d"], 40)
        self.assertEqual(verify([e], options), [])

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(a) for a in argv])
        return code, out.getvalue() + err.getvalue()

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "in").mkdir()
            (tmp / "in" / "tools.json").write_text(json.dumps({"data": [tool()]}))
            (tmp / "in" / "holders.json").write_text(json.dumps({"data": [holder()]}))
            (tmp / "in" / "broken.json").write_text("{")
            src = tmp / "in" / "tools.json"

            def written(path):
                return json.loads(path.read_text())["tools"][0]["definition"]["holder_d"]

            code, text = self.run_cli(src, "-o", tmp / "a.camtools", "--default-holder", "ER16",
                                      "--holders", tmp / "in" / "holders.json")
            self.assertEqual((code, written(tmp / "a.camtools")), (0, 40), text)
            # a folder finds its own holders, whichever library they are in
            (tmp / "in" / "broken.json").unlink()
            code, text = self.run_cli(tmp / "in", "-o", tmp / "out", "--default-holder", "ER16")
            self.assertEqual((code, written(tmp / "out" / "tools.camtools")), (0, 40), text)
            (tmp / "in" / "broken.json").write_text("{")
            code, _ = self.run_cli(src, "-o", tmp / "b.camtools", "--default-holder", "ER16", "--holders", tmp / "in")
            self.assertEqual(code, 0)
            for argv, why in ((("--default-holder", "ER16"), "no stand-alone holder"),
                              (("--default-holder", ""), "no stand-alone holder"),
                              (("--holders", tmp / "in"), "needs --default-holder"),
                              (("--default-holder", "ER16", "--holders", tmp / "gone.json"), "cannot read")):
                code, text = self.run_cli(src, "-o", tmp / "c.camtools", *argv)
                self.assertEqual(code, 1)
                self.assertIn(why, text)
                self.assertFalse((tmp / "c.camtools").exists())


class CornerChamferTests(unittest.TestCase):
    def test_45_degrees(self):
        d, result = one(corner_chamfer())
        self.assertEqual((d["kind"], d["corner_r"], d["diameter"]), ("bull_nose", 0.5, 10))
        self.assertTrue(any("never smaller" in w for w in result.warnings))
        self.assertEqual(verify([corner_chamfer()]), [])

    def test_never_smaller_than_the_chamfer(self):
        # for both readings of the angle the arc stays outside the face
        for angle in (15, 30, 45, 60, 75):
            e = corner_chamfer({"chamfer-angle": angle})
            r, w, R = one(e)[0]["corner_r"], 0.5, 5.0
            self.assertEqual(verify([e]), [])
            for h in (w * math.tan(math.radians(angle)), w / math.tan(math.radians(angle))):
                for i in range(101):
                    z = h * i / 100
                    face = R - w + w * z / h
                    arc = R if z >= r else R - r + math.sqrt(max(r * r - (r - z) ** 2, 0))
                    self.assertGreaterEqual(arc, face - 1e-5)

    def test_inches(self):
        e = corner_chamfer({"DC": 0.5, "LCF": 1, "LB": 2, "SFDM": 0.5, "chamfer-width": 0.02}, unit="inches")
        self.assertAlmostEqual(one(e)[0]["corner_r"], 0.508)
        self.assertEqual(verify([e]), [])

    def test_without_chamfer_numbers_is_a_sharp_end_mill(self):
        for geometry in ({"chamfer-width": 0}, {"chamfer-angle": None}, {"chamfer-angle": 90}):
            e = corner_chamfer({"RE": 1, **geometry})
            d, _ = one(e)
            self.assertEqual((d["kind"], d["corner_r"]), ("end_mill", 0))
            self.assertEqual(verify([e]), [])

    def test_refusals(self):
        self.assertIn("DCX", convert([corner_chamfer({"DCX": 11})]).skipped[0].reason)
        self.assertIn("axis", convert([corner_chamfer({"chamfer-width": 5})]).skipped[0].reason)

    def test_output_check_catches_damage(self):
        self.assertTrue(verify([corner_chamfer()], change=lambda d: d.update(corner_r=1.0)))
        self.assertTrue(verify([corner_chamfer()], change=lambda d: d.update(kind="ball_nose")))


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

    def test_flat_bottom_drill_is_center_cutting_end_mill(self):
        # #17: CamCraft refuses a tip angle of 180, and a drill's must be a point.
        # Fusion keeps the feed in v_f_plunge (no v_f), as for any drill.
        e = tool("drill", unit="inches", description="5/8 FLAT BOTTOM DRILL",
                 geometry={"DC": 0.625, "LCF": 2.677165354330709, "LB": 3.1496062992125986,
                           "SFDM": 0.625, "SIG": 180},
                 presets=[{"name": "Default preset", "n": 2139.04, "v_f_plunge": 25.6685}])
        d, result = one(e)
        self.assertEqual((d["kind"], d["tip_angle"], d["corner_r"]), ("end_mill", 0, 0))
        self.assertNotIn("center_cutting", d)  # CamCraft's default: center cutting
        self.assertAlmostEqual(d["diameter"], 15.875)
        self.assertAlmostEqual(d["feed"], 25.6685 * 25.4, places=4)
        self.assertTrue(any("flat bottom drill" in w for w in result.warnings))
        self.assertIsNone(why_not_sane(d))
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_drill_point_over_180_still_refused(self):
        r = convert([tool("drill", geometry={"SIG": 200})])
        self.assertIn("under 180", r.skipped[0].reason)

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
        # as if "mdf" were on CamCraft's main only
        released = tuple(m for m in RELEASED_MATERIALS if m != "mdf")
        with mock.patch("fusitool2craftool.convert.RELEASED_MATERIALS", released):
            with self.assertRaisesRegex(ValueError, "--unreleased-materials"):
                convert([tool()], Options(material="mdf"))
            d, _ = one(tool(), Options(material="mdf", unreleased_materials=True))
            self.assertEqual(d["cutting_data"][0]["material"], "mdf")
            result = convert([tool(presets=[{"name": "MDF", "n": 16000, "v_f": 3000}])], Options(all_presets=True))
            self.assertIn("newer than the latest release", next(iter(result.presets))[1])

    def test_wood_and_plastic_words_are_released(self):
        # CamCraft 0.26.0
        for word in ("softwood", "hardwood", "mdf", "plywood", "acrylic", "hdpe"):
            d, _ = one(tool(), Options(material=word))
            self.assertEqual(d["cutting_data"][0]["material"], word)

    def test_flat_tip_is_warned_about(self):
        for type_, geometry in (("chamfer mill", {"TA": 45}), ("counter sink", {"SIG": 82})):
            for tip, warned in ((0.5, True), (0, False), (None, False)):
                d, result = one(tool(type_, geometry={**geometry, "tip-diameter": tip}))
                self.assertEqual(d["kind"], "taper")
                self.assertEqual(any("flat tip of 0.5 mm" in w for w in result.warnings), warned)
        for type_ in ("spot drill", "center drill"):
            for tip, warned in ((0.5, True), (0, False)):
                d, result = one(tool(type_, geometry={"SIG": 90, "tip-diameter": tip}))
                self.assertEqual((d["kind"], any("flat tip" in w for w in result.warnings)), ("drill", warned))
        _, result = one(tool("chamfer mill", unit="inches", geometry={"TA": 45, "tip-diameter": 0.01}))
        self.assertTrue(any("flat tip of 0.254 mm" in w for w in result.warnings))

    def test_no_flute_length_is_skipped(self):
        # #7: no other Fusion field says how far up the tool cuts
        e = tool(geometry={"shoulder-length": 20, "LB": 30})
        del e["geometry"]["LCF"]
        self.assertIn("no flute length", convert([e]).skipped[0].reason)

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

    def test_holder_segments_become_a_drawn_body(self):
        # #12: Fusion lists segments from the tool end up; the outline sits on the shank's top
        e = tool(unit="inches", geometry={"DC": 0.5, "LCF": 1, "LB": 2, "SFDM": 0.5},
                 holder={"unit": "inches", "segments": [
                     {"height": 0.5, "lower-diameter": 1.0, "upper-diameter": 1.25},   # nut chamfer
                     {"height": 1.0, "lower-diameter": 1.25, "upper-diameter": 1.25},  # nut
                     {"height": 0.25, "lower-diameter": 1.0, "upper-diameter": 1.0},   # neck: a step in
                     {"height": 0.5, "lower-diameter": 2.5, "upper-diameter": 2.5}]})  # flange: a step out
        d, _ = one(e)
        self.assertEqual((d["holder_d"], d["holder_len"]), (63.5, 57.15))  # the cylinder envelope stays
        body = d["body"]
        self.assertEqual((body["source"], body["facets"]), ("drawn profile", []))
        self.assertEqual(body["spun"], [
            [50.8, 0.0, 0.0], [50.8, 12.7, 0.0], [63.5, 15.875, 0.0], [88.9, 15.875, 0.0],
            [88.9, 12.7, 0.0], [95.25, 12.7, 0.0], [95.25, 31.75, 0.0], [107.95, 31.75, 0.0],
            [107.95, 0.0, 0.0]])

    def test_holder_body_merges_slivers_wider_and_drops_collinear_points(self):
        e = tool(geometry={"DC": 6, "LCF": 10, "LB": 30},
                 holder={"unit": "millimeters", "segments": [
                     {"height": 0.005, "lower-diameter": 20.99, "upper-diameter": 21},  # sliver: merged, wider kept
                     {"height": 10, "lower-diameter": 21, "upper-diameter": 21},
                     {"height": 10, "lower-diameter": 21, "upper-diameter": 21}]})   # same cylinder: one segment
        d, _ = one(e)
        self.assertEqual(d["body"]["spun"], [[30, 0, 0], [30, 10.5, 0], [50.005, 10.5, 0], [50.005, 0, 0]])

    def test_undrawable_holder_keeps_the_cylinder(self):
        for segs, why in (([{"height": 10, "lower-diameter": 20, "upper-diameter": 0},
                            {"height": 10, "lower-diameter": 20, "upper-diameter": 20}], "narrows to the axis"),
                          ([{"height": 10, "lower-diameter": "x", "upper-diameter": 20}], "no usable"),
                          ([{"height": 3000, "lower-diameter": 20, "upper-diameter": 20}], "larger than CamCraft")):
            with self.subTest(why=why):
                d, result = one(tool(holder={"unit": "millimeters", "segments": segs}))
                self.assertNotIn("body", d)
                self.assertTrue(any(why in w for w in result.warnings), result.warnings)

    def test_holder_cylinders_option(self):
        e = tool(holder={"unit": "millimeters", "segments": [
            {"height": 30, "lower-diameter": 40, "upper-diameter": 40}]})
        d, _ = one(e, Options(holder_bodies=False))
        self.assertNotIn("body", d)
        self.assertEqual((d["holder_d"], d["holder_len"]), (40, 30))

    def test_check_catches_a_thin_or_misplaced_holder(self):
        e = tool(geometry={"DC": 6, "LCF": 10, "LB": 30}, holder={"unit": "millimeters", "segments": [
            {"height": 20, "lower-diameter": 30, "upper-diameter": 30},
            {"height": 10, "lower-diameter": 50, "upper-diameter": 50}]})
        result = convert([e])
        for change, problem in (
                (lambda b: b["spun"].__setitem__(2, [50, 12, 0]), "thinner than Fusion segment 1"),
                (lambda b: [q.__setitem__(0, q[0] + 5) for q in b["spun"]], "starts at z 35"),
                (lambda b: b["spun"].insert(1, [30, 0.0, 0]), "close to the axis")):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as tmp:
                path = camtools.write(result, Path(tmp) / "out.camtools")
                self.assertEqual(verify_file(path, result), [])
                doc = json.loads(path.read_text())
                change(doc["tools"][0]["definition"]["body"])
                path.write_text(json.dumps(doc))
                self.assertTrue(any(problem in p for p in verify_file(path, result)), verify_file(path, result))

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


def keyseat(geometry=None, segments=((0.75, 0.25, 0.25), (0.21651, 0.25, 0.5)), **extra):
    """Harvey 921350's shape: a 1/2" head on a 1/4" neck under a 1/2" shank (#27)."""
    return tool("slot mill", unit="inches", description=extra.pop("description", "keyseat cutter"),
                geometry={"DC": 0.5, "LCF": 0.093, "LB": 1.30951, "OAL": 3, "SFDM": 0.5, "NOF": 8,
                          "shoulder-diameter": 0.5, "shoulder-length": 0.093, **(geometry or {})},
                shaft={"type": "shaft", "segments": [
                    {"height": h, "lower-diameter": lo, "upper-diameter": up} for h, lo, up in segments]}, **extra)


ARBOR = {"unit": "millimeters", "segments": [
    {"height": 20, "lower-diameter": 12, "upper-diameter": 12},
    {"height": 30, "lower-diameter": 40, "upper-diameter": 40}]}


def saw(geometry=None, **extra):
    """Harvey SAB0625's shape: a bare 1-1/4" x 1/16" blade with a 3/8" bore (#31)."""
    return tool("slot mill", unit="inches", description=extra.pop("description", "slitting saw"),
                geometry={"DC": 1.25, "LCF": 0.0625, "LB": 0.0625, "OAL": 0.0625, "SFDM": 0.375, "NOF": 24,
                          "shoulder-diameter": 0.375, "shoulder-length": 0.0625, **(geometry or {})}, **extra)


class PlaceholderSpeedTests(unittest.TestCase):
    """#35: a vendor's preset of 1 rpm and feed 1 states no speeds."""

    ONE = {"name": "Default Preset", "n": 1, "v_f": 1, "v_f_plunge": 1, "n_ramp": 1, "f_n": 1, "v_c": 0.008}
    DEFAULTS = Options(default_rpm=9000, default_feed=600)

    def verifies(self, result):
        with tempfile.TemporaryDirectory() as tmp:
            return verify_file(camtools.write(result, Path(tmp) / "out.camtools"), result)

    def test_skipped_without_defaults(self):
        for unit in ("inches", "millimeters"):
            result = convert([tool(unit=unit, presets=[self.ONE])])
            self.assertEqual(result.tools, [])
            self.assertIn("only a placeholder spindle speed", result.skipped[0].reason)

    def test_takes_the_defaults(self):
        d, result = one(tool(unit="inches", presets=[self.ONE]), self.DEFAULTS)
        self.assertEqual((d["rpm"], d["feed"]), (9000, 600))
        self.assertTrue(any("only a placeholder spindle speed" in w for w in result.warnings))
        self.assertEqual(self.verifies(result), [])

    def test_every_kind_takes_the_defaults(self):
        for type_, g, feed in (("drill", {"SIG": 118}, 600), ("spot drill", {"SIG": 60}, 600), ("chamfer mill", {"TA": 45}, 600),
                               ("tap right hand", {"DC": 8, "TP": 1.25}, 9000 * 1.25)):
            for preset in (self.ONE, {**self.ONE, "v_f": None}, {**self.ONE, "v_f": 0}):
                d, result = one(tool(type_, geometry=g, presets=[preset]), self.DEFAULTS)
                self.assertEqual((d["rpm"], d["feed"]), (9000, feed), type_)
                self.assertEqual(self.verifies(result), [])

    def test_a_real_preset_is_preferred(self):
        real = {"name": "Steel", "n": 4000, "v_f": 300}
        d, result = one(tool(presets=[self.ONE, real]))
        self.assertEqual((d["rpm"], d["feed"]), (4000, 300))
        self.assertEqual(self.verifies(result), [])
        d, result = one(tool(presets=[self.ONE, real]), Options(preset="default preset", default_rpm=9000, default_feed=600))
        self.assertEqual((d["rpm"], d["feed"]), (9000, 600))
        self.assertEqual(self.verifies(result), [])

    def test_a_drag_engraver_keeps_its_one_rpm(self):
        d, result = one(tool("chamfer mill", unit="inches", geometry={"TA": 45},
                             presets=[{"name": "Engraving Preset", "n": 1, "v_f": 100, "v_f_plunge": 25}]))
        self.assertEqual((d["rpm"], d["feed"]), (1, 2540))
        self.assertEqual(self.verifies(result), [])

    def test_feed_of_one_at_a_real_speed_is_kept(self):
        d, _ = one(tool(presets=[{"name": "P", "n": 8000, "v_f": 1}]))
        self.assertEqual((d["rpm"], d["feed"]), (8000, 1))

    def test_no_material_row(self):
        result = convert([tool(presets=[{"name": "Steel", "n": 4000, "v_f": 300}, {**self.ONE, "name": "6061 aluminum"}])],
                         Options(all_presets=True))
        rows = result.tools[0]["definition"].get("cutting_data", [])
        self.assertNotIn(1, [r["rpm"] for r in rows])
        self.assertIn(("6061 aluminum", "no speeds"), result.presets)

    def test_output_check_refuses_the_placeholder(self):
        d, result = one(tool(unit="inches", presets=[self.ONE]), self.DEFAULTS)
        d.update(rpm=1.0, feed=25.4)
        self.assertTrue(any("match no Fusion preset" in p for p in self.verifies(result)))


class SharpDrillAndSawTests(unittest.TestCase):
    """#31: points of 60 degrees or less, and saws without a shank."""

    def verifies(self, result):
        with tempfile.TemporaryDirectory() as tmp:
            return verify_file(camtools.write(result, Path(tmp) / "out.camtools"), result)

    def sharp(self, angle, type_="spot drill", **geometry):
        return tool(type_, geometry={"DC": 6, "LCF": 18, "LB": 40, "SFDM": 6, "SIG": angle, **geometry},
                    presets=[{"name": "P", "n": 4000, "v_f_plunge": 200}])

    def test_sharp_point_is_a_chamfer_mill(self):
        for type_ in ("drill", "spot drill", "center drill"):
            d, result = one(self.sharp(60, type_))
            self.assertEqual((d["kind"], d["tip_angle"], d["rpm"], d["feed"]), ("taper", 60, 4000, 200))
            self.assertTrue(any("imported as a chamfer mill" in w for w in result.warnings))
            self.assertEqual(self.verifies(result), [])

    def test_the_limit_is_judged_as_written(self):
        # 60.0000001 is written as 60.0, which CamCraft's drill refuses
        for angle, kind in ((30, "taper"), (60.0000001, "taper"), (60.001, "drill"), (90, "drill")):
            d, result = one(self.sharp(angle))
            self.assertEqual(d["kind"], kind, angle)
            self.assertEqual(self.verifies(result), [])

    def test_sharp_point_takes_the_drill_defaults(self):
        e = tool("spot drill", geometry={"DC": 6, "LCF": 18, "LB": 40, "SFDM": 6, "SIG": 60}, presets=[])
        d, result = one(e, Options(default_rpm=9000, default_feed=900,
                                   kind_defaults={"drill": {"rpm": 1500, "feed": 80}, "taper": {"rpm": 1, "feed": 1}}))
        self.assertEqual((d["kind"], d["rpm"], d["feed"]), ("taper", 1500, 80))
        self.assertEqual(self.verifies(result), [])

    def test_flat_tip_of_a_sharp_point_is_warned_about(self):
        _, result = one(self.sharp(60, **{"tip-diameter": 0.5}))
        self.assertTrue(any("flat tip of 0.5 mm" in w for w in result.warnings))

    def test_output_check_wants_the_cone(self):
        d, result = one(self.sharp(60))
        d["kind"] = "drill"
        self.assertTrue(self.verifies(result))
        d["kind"], d["tip_angle"] = "taper", 90
        self.assertTrue(self.verifies(result))

    def test_saw_has_the_shortest_neck(self):
        d, result = one(saw(holder=ARBOR))
        self.assertEqual((d["kind"], d["shank_len"], d["shank_d"]), ("t_slot", 0.001, 9.525))
        self.assertAlmostEqual(d["body"]["spun"][0][0], 1.5875 + 0.001, places=6)
        self.assertEqual((d["holder_d"], d["holder_len"]), (40, 50))
        self.assertTrue(any("arbor-mounted saw" in w for w in result.warnings))
        self.assertEqual(self.verifies(result), [])

    def test_saw_without_an_arbor_gets_the_default_holder(self):
        d, result = one(saw())
        self.assertEqual((d["holder_d"], d["holder_len"], d["shank_len"]), (50, 40, 0.001))
        self.assertNotIn("body", d)
        self.assertEqual(self.verifies(result), [])

    def test_saw_with_a_shank_is_unchanged(self):
        d, result = one(saw({"LB": 0.5, "OAL": 0.5}))
        self.assertAlmostEqual(d["shank_len"], 0.4375 * 25.4, places=5)
        self.assertFalse(any("arbor-mounted" in w for w in result.warnings))

    def test_holder_past_the_blade_is_still_refused(self):
        # stick-out shorter than the blade: the holder would stand in the cut
        result = convert([saw({"LB": 0.05})])
        self.assertIn("neck length", result.skipped[0].reason)

    def test_other_undercut_cutters_still_need_a_neck(self):
        for type_, g in (("lollipop mill", {"DC": 10, "LCF": 5}), ("dovetail mill", {"DC": 20, "LCF": 4, "TA": 30})):
            result = convert([tool(type_, geometry={"SFDM": 6, "LB": g["LCF"], "OAL": g["LCF"], **g})])
            self.assertEqual(len(result.skipped), 1, type_)

    def test_output_check_wants_the_saws_neck(self):
        d, result = one(saw(holder=ARBOR))
        d["shank_len"] = 5.0
        self.assertTrue(any("shank_len" in p for p in self.verifies(result)))


def tapered(geometry=None, **extra):
    """A 3 degree a side tapered mill with a 2 mm tip (#10)."""
    return tool("tapered mill", description=extra.pop("description", "tapered mill"),
                geometry={"DC": 2, "LCF": 20, "LB": 40, "OAL": 60, "SFDM": 6, "TA": 3, "RE": 0, **(geometry or {})}, **extra)


TAPERS = Options(unreleased_tapers=True)


class TaperedMillTests(unittest.TestCase):
    """#10: tapered mills, for a CamCraft newer than 0.26.0."""

    def verifies(self, result):
        with tempfile.TemporaryDirectory() as tmp:
            return verify_file(camtools.write(result, Path(tmp) / "out.camtools"), result)

    @staticmethod
    def tangent(angle):
        """The tip radius of a cone tangent to a 1 mm ball."""
        return (1 - math.sin(math.radians(angle))) / math.cos(math.radians(angle))

    def test_needs_the_flag(self):
        result = convert([tapered(), tapered({"RE": 1})])
        self.assertEqual([s.category for s in result.skipped], ["tapered mill", "tapered ball mill"])
        self.assertIn("--unreleased-tapers", result.skipped[0].reason)

    def test_flat_tip(self):
        d, result = one(tapered(), TAPERS)
        self.assertEqual((d["kind"], d["tip_form"], d["tip_diameter"], d["corner_r"], d["tip_angle"]),
                         ("taper", "flat", 2, 0, 6))
        self.assertAlmostEqual(d["diameter"], 2 + 40 * math.tan(math.radians(3)), places=5)
        self.assertEqual((d["flute_len"], d["shank_d"], d["shank_len"]), (20, 6, 20))
        self.assertEqual(result.version, 6)
        self.assertEqual(self.verifies(result), [])

    def test_rounded_flat_tip(self):
        d, result = one(tapered({"RE": 0.3}), TAPERS)
        self.assertEqual((d["tip_form"], d["tip_diameter"], d["corner_r"]), ("flat", 2, 0.3))
        self.assertEqual(self.verifies(result), [])

    def test_ball_tip_tangent_to_the_cone(self):
        # Harvey 32630: DC 0.02749" is the cone at the tip of an R 0.015" ball
        d, result = one(tapered({"DC": 0.02749, "RE": 0.015, "TA": 5, "LCF": 0.3, "LB": 0.48686, "OAL": 3,
                                 "SFDM": 0.1875}, unit="inches"), TAPERS)
        self.assertEqual((d["tip_form"], d["corner_r"], d["tip_angle"]), ("ball", 0.381, 10))
        self.assertNotIn("tip_diameter", d)
        self.assertAlmostEqual(d["diameter"], 0.0799831981155544 * 25.4, places=3)  # Fusion's own shoulder-diameter
        self.assertEqual(self.verifies(result), [])

    def test_ball_of_half_the_tip_fits_fusions_cone(self):
        # RE = DC/2: Fusion's cone starts at the full radius, so a true ball would have a thinner flank
        for re_ in (1, 0.992):  # Garr rounds it to 3 decimals of an inch
            d, result = one(tapered({"RE": re_}), TAPERS)
            self.assertEqual(d["tip_form"], "ball")
            self.assertNotIn("tip_diameter", d)
            self.assertAlmostEqual(d["corner_r"], 1 / self.tangent(3), places=5)
            self.assertAlmostEqual(d["diameter"], 2 + 40 * math.tan(math.radians(3)), places=5)
            self.assertTrue(any("tapered ball of" in w for w in result.warnings))
            self.assertEqual(self.verifies(result), [])

    def test_fitted_ball_encloses_the_real_one(self):
        def radius(ball, angle, z):  # of a ball and the cone tangent to it
            a = math.radians(angle)
            if z <= ball * (1 - math.sin(a)):
                return math.sqrt(ball * ball - (ball - z) ** 2)
            return ball * math.tan(math.pi / 4 - a / 2) + z * math.tan(a)
        for angle in (1, 3, 6.28, 7):
            d, _ = one(tapered({"RE": 1, "TA": angle, "LCF": 10}), TAPERS)
            for i in range(1, 400):
                z = 10 * i / 400
                self.assertGreaterEqual(radius(d["corner_r"], angle, z), radius(1, angle, z), (angle, z))

    def test_rounded_tip_that_is_nearly_a_ball_stays_flat(self):
        d, result = one(tapered({"RE": 0.95}), TAPERS)
        self.assertEqual((d["tip_form"], d["tip_diameter"], d["corner_r"]), ("flat", 2, 0.95))
        self.assertEqual(self.verifies(result), [])

    def test_steep_cone_is_no_ball(self):
        # Matsuura: RE = DC/2 at 30 degrees; the ball that fits is 73 % larger
        d, result = one(tapered({"DC": 0.0508, "RE": 0.0254, "TA": 30, "LCF": 6.35, "LB": 31.75}), TAPERS)
        self.assertEqual((d["tip_form"], d["tip_diameter"], d["corner_r"]), ("flat", 0.0508, 0.0254))
        self.assertEqual(self.verifies(result), [])

    def test_ball_larger_than_its_tip(self):
        d, result = one(tapered({"DC": 0.0025, "RE": 0.0254, "TA": 30, "LCF": 6.35}), TAPERS)
        self.assertEqual((d["tip_form"], d["corner_r"]), ("ball", 0.0254))
        self.assertTrue(any("too large" in w for w in result.warnings))
        self.assertEqual(self.verifies(result), [])

    def test_never_thinner_than_fusion(self):
        for geometry in ({}, {"RE": 0.3}, {"RE": 0.9}, {"RE": 1}, {"RE": 1 / self.tangent(3)}, {"TA": 45, "RE": 0.4},
                         {"DC": 2 * self.tangent(20), "TA": 20, "RE": 1}):
            e = tapered(geometry)
            d, _ = one(e, TAPERS)
            g = e["geometry"]
            half = math.radians(d["tip_angle"] / 2)
            cr = d["corner_r"]
            r0 = d["tip_diameter"] / 2 if d["tip_form"] == "flat" else cr * math.tan(math.pi / 4 - half / 2)
            for i in range(1, 200):
                z = 20 * i / 200
                # the flank; a fitted ball's tip is test_fitted_ball_encloses_the_real_one
                self.assertGreaterEqual(min(r0 + z * math.tan(half), d["diameter"] / 2),
                                        g["DC"] / 2 + z * math.tan(math.radians(g["TA"])) - 1e-5, geometry)
            self.assertIsNone(why_not_sane(d))

    def test_camcraft_rules(self):
        d, _ = one(tapered({"RE": 0.3}), TAPERS)
        for change, why in (({"tip_diameter": 0.0}, "flat tip's diameter"), ({"tip_diameter": 9.0}, "flat tip's diameter"),
                            ({"corner_r": 1.2}, "does not fit"), ({"tip_form": "ball", "corner_r": 0.0}, "ball tip's radius"),
                            ({"tip_form": "ball"}, "must be 0"), ({"kind": "end_mill"}, "cannot have"),
                            ({"flute_len": 0.2}, "tall"), ({"tip_diameter": 4.09}, "meets the cone")):
            self.assertIn(why, why_not_sane({**d, **change}) or "", change)

    def test_angle_must_be_usable(self):
        for ta in (-3, 90, 120):
            result = convert([tapered({"TA": ta})], TAPERS)
            self.assertIn("angle (TA)", result.skipped[0].reason)

    def test_without_an_angle_it_is_straight(self):
        # SpeTool's bowl and core box bits; needs no new CamCraft
        for re_, kind in ((0, "end_mill"), (0.5, "bull_nose"), (1, "ball_nose")):
            d, result = one(tapered({"TA": 0, "RE": re_}, description="bowl bit"))
            self.assertEqual((d["kind"], d["corner_r"], d["diameter"]), (kind, re_, 2))
            self.assertEqual(result.version, 1)
            self.assertEqual(self.verifies(result), [])

    def test_taper_by_name_without_an_angle_is_refused(self):
        # Harvey 832860: "TAPERED SQUARE END MILL" with TA 0
        self.assertIn("without a taper angle", convert([tapered({"TA": 0})], TAPERS).skipped[0].reason)

    def test_cone_past_the_shank_is_warned_about(self):
        # BitsBits 4TEB10-020: 5 degrees over 76 mm of flute on a 6.35 mm shank
        _, result = one(tapered({"TA": 5, "LCF": 76}), TAPERS)
        self.assertTrue(any("over its 6 mm shank" in w for w in result.warnings))

    def test_default_surface_speed_holds_at_the_widest(self):
        d, result = one(tapered(presets=[]), Options(unreleased_tapers=True, default_vc=100, default_feed=500))
        self.assertAlmostEqual(d["rpm"], 100000 / (math.pi * d["diameter"]), places=3)
        self.assertEqual(self.verifies(result), [])

    def test_output_check_reads_the_tip(self):
        for e, change in ((tapered(), {"tip_form": "ball"}), (tapered(), {"tip_diameter": 1.5}),
                          (tapered(), {"diameter": 2.0}), (tapered(), {"tip_angle": 3.0}),
                          (tapered({"RE": 1}), {"corner_r": 1.0}), (tapered({"RE": 1}), {"tip_form": "flat", "tip_diameter": 2.0}),
                          (tapered({"RE": 0.3}), {"corner_r": 0.0}), (tapered(), {"kind": "end_mill"}),
                          (tapered({"RE": 1 / self.tangent(3)}), {"tip_form": "flat", "tip_diameter": 2.0})):
            d, result = one(e, TAPERS)
            d.update(change)
            self.assertTrue(self.verifies(result), change)
        d, result = one(tool("chamfer mill", geometry={"TA": 45}))
        d.update(tip_form="flat", tip_diameter=1.0)
        self.assertTrue(any("tip form" in p for p in self.verifies(result)))

    def test_chamfer_mills_keep_their_sharp_point(self):
        # CamCraft's operations refuse a flat tip, so a V-bit with one would be useless
        d, result = one(tool("chamfer mill", geometry={"TA": 45, "tip-diameter": 0.5}), TAPERS)
        self.assertNotIn("tip_form", d)
        self.assertEqual(result.version, 1)


def multi_form(geometry=None, pitch=0.8, rows=10, dc=3.9, height=0.6, flank=30.0, neck=6.0, shape=None, **extra):
    """A multi-form thread mill stored as a form mill (#37): teeth, a neck, a cone to a 6 mm shank."""
    root, rise = dc / 2 - height * pitch, height * pitch * math.tan(math.radians(flank))
    line = [(0, 0), (root, 0)]
    for i in range(rows):
        z = i * pitch
        line += [(dc / 2, z + rise), (dc / 2, z + rise + 0.1 * pitch), (root, z + 2 * rise + 0.1 * pitch), (root, z + pitch)]
    top = rows * pitch
    line += [(root, top + neck), (3, top + neck + 3), (3, 60), (0, 60)]
    if shape:
        line = shape(line)
    return tool("form mill", description=extra.pop("description", "MULTI-FORM M5-0.80 THREAD MILL"),
                geometry={"DC": dc, "LCF": top, "LB": top + neck + 10, "OAL": 60, "SFDM": 6, "NOF": 3, "HAND": True, "TP": 0,
                          "profile": [{"end": list(q)} for q in line], **(geometry or {})}, **extra)


class FormThreadMillTests(unittest.TestCase):
    """#37: multi-form thread mills that vendors store as form mills."""

    def verifies(self, result):
        with tempfile.TemporaryDirectory() as tmp:
            return verify_file(camtools.write(result, Path(tmp) / "out.camtools"), result)

    def reason(self, entry):
        result = convert([entry])
        self.assertEqual(result.tools, [])
        return result.skipped[0].reason

    def test_thread_mill_from_its_outline(self):
        d, result = one(multi_form())
        self.assertEqual((d["kind"], d["diameter"], d["pitch"], d["tip_angle"], d["flute_len"]), ("thread_mill", 3.9, 0.8, 60, 8))
        self.assertAlmostEqual(d["shank_d"], 3.9 - 1.2 * 0.8)
        self.assertAlmostEqual(d["shank_len"], 6)
        outline = [[round(v, 4) for v in q] for q in d["body"]["spun"]]
        # neck end, cone to the 6 mm shank, shank up to LB, default holder (50 x 40)
        self.assertEqual(outline[1:5], [[14, 1.47, 0], [17, 3, 0], [24, 3, 0], [24, 25, 0]])
        self.assertTrue(any("0.8 mm pitch read" in w for w in result.warnings))
        self.assertEqual(self.verifies(result), [])

    def test_pitch_is_the_standard_one(self):
        # CamCraft compares pitches to 1e-6 mm; Fusion's outlines are rounded (0.500126 for 0.5)
        for drawn, pitch in ((0.500126, 0.5), (1.2698, 25.4 / 20), (25.4 / 11.5, 25.4 / 11.5), (0.79375, 25.4 / 32), (0.8001, 0.8)):
            d, result = one(multi_form(pitch=drawn, dc=12))
            self.assertAlmostEqual(d["pitch"], pitch, places=6)
            self.assertEqual(self.verifies(result), [])

    def test_inch_outline(self):
        e = multi_form(pitch=0.05, rows=15, dc=0.195, neck=0.1, unit="inches")
        d, result = one(e)
        self.assertAlmostEqual(d["pitch"], 1.27)
        self.assertAlmostEqual(d["flute_len"], 19.05)
        self.assertEqual(self.verifies(result), [])

    def test_last_tooth_past_the_flutes_is_cut_edge(self):
        d, result = one(multi_form({"LCF": 7.75}))
        self.assertGreater(d["flute_len"], 7.75)
        self.assertEqual(self.verifies(result), [])

    def test_shank_right_above_the_teeth(self):
        d, result = one(multi_form(neck=0.0))
        self.assertLessEqual(d["shank_len"], 0.001)
        self.assertEqual(self.verifies(result), [])

    def test_holder_stands_on_the_shank(self):
        h = {"unit": "millimeters", "segments": [{"height": 20, "lower-diameter": 20, "upper-diameter": 30}]}
        d, result = one(multi_form(holder=h))
        self.assertEqual(d["holder_d"], 30)
        self.assertAlmostEqual(d["holder_len"], 30)  # 10 mm of cone and shank, 20 of holder
        self.assertEqual(self.verifies(result), [])

    def test_cylinders(self):
        d, result = one(multi_form(), Options(holder_bodies=False))
        self.assertNotIn("body", d)
        self.assertEqual(self.verifies(result), [])

    def test_refused(self):
        taper = lambda line: [(r + (0.03 * z if 0 < z < 8 else 0), z) for r, z in line]  # noqa: E731
        uneven = lambda line: [(r, z + (0.05 if 4 < z < 4.7 else 0)) for r, z in line]  # noqa: E731
        back = lambda line: line[:-3] + [(2, 5), (3, 20)] + line[-2:]  # noqa: E731
        for entry, why in ((multi_form({"HAND": False}), "left-hand"), (multi_form(rows=1), "one row"),
                           (multi_form(shape=taper), "not all on its diameter"), (multi_form(shape=uneven), "evenly"),
                           (multi_form(height=0.45), "high teeth"), (multi_form(flank=27.5), "60 degree"),
                           (multi_form(pitch=0.77), "no standard one"), (multi_form(shape=back), "runs back"),
                           (multi_form({"profile": []}), "without an outline"), (multi_form({"LB": 80}), "does not reach")):
            self.assertIn(why, self.reason(entry))

    def test_arcs(self):
        def arc(ccw, center):
            e = multi_form()
            p = e["geometry"]["profile"]
            p[-3].update(arc=True, ccw=ccw, center=center)  # the neck (1.47, 14) to the shank (3, 17)
            return e
        # a fillet around a point outside the cutter lies inside its chord
        d, result = one(arc(False, [1.47 + 3.706, 14]))
        self.assertAlmostEqual(d["shank_len"], 6)
        self.assertEqual(self.verifies(result), [])
        for ccw, center in ((True, [-0.706, 17]), (True, [5.176, 14]), (False, [-0.706, 17])):
            self.assertIn("bulging arc", self.reason(arc(ccw, center)))

    def test_other_form_mills_are_still_skipped(self):
        for name in ("CORNER ROUNDING END MILL", "SPIRAL REAMER"):
            result = convert([multi_form(description=name)])
            self.assertEqual(result.skipped[0].category, "form mill")

    def test_output_check_reads_the_outline(self):
        for change in ({"pitch": 0.75}, {"flute_len": 6.0}, {"shank_d": 2.0}, {"diameter": 3.0}, {"tip_angle": 55.0},
                       {"kind": "thread_mill_single"}, {"shank_len": 9.0}):
            d, result = one(multi_form())
            d.update(change)
            self.assertTrue(self.verifies(result), change)


class UndercutShankTests(unittest.TestCase):
    """#27: undercut cutters whose shank is as wide as the head."""

    def assert_verifies(self, result):
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_neck_is_the_narrow_start_of_the_shaft(self):
        d, result = one(keyseat())
        self.assertEqual(d["kind"], "keyseat")
        for key, inches in (("diameter", 0.5), ("flute_len", 0.093), ("shank_d", 0.25), ("shank_len", 0.75)):
            self.assertAlmostEqual(d[key], inches * 25.4, places=5)
        z = [round((0.093 + h) * 25.4, 4) for h in (0.75, 0.96651, 1.21651)]
        outline = [[round(v, 4) for v in q] for q in d["body"]["spun"]]
        # neck end, cone to the shank, shank up to LB, default holder (50 x 40)
        self.assertEqual(outline, [[z[0], 0, 0], [z[0], 3.175, 0], [z[1], 6.35, 0], [z[2], 6.35, 0],
                                   [z[2], 25, 0], [z[2] + 40, 25, 0], [z[2] + 40, 0, 0]])
        self.assertEqual(d["holder_d"], 50)
        self.assertAlmostEqual(d["holder_len"], (1.30951 - 0.843) * 25.4 + 40, places=4)
        self.assert_verifies(result)

    def test_own_holder_stands_on_the_shank(self):
        d, result = one(keyseat(holder={"unit": "millimeters", "segments": [
            {"height": 10, "lower-diameter": 20, "upper-diameter": 30}]}))
        self.assertEqual(d["body"]["spun"][-1], [round(1.30951 * 25.4 + 10, 6), 0, 0])
        self.assertEqual(d["holder_d"], 30)
        self.assert_verifies(result)

    def test_shoulder_is_the_neck(self):
        # XEBEC-style ball: a shoulder, then a cone to a wider shank
        d, result = one(tool("lollipop mill", description="ball", geometry={
            "DC": 1.8, "LCF": 1.612, "LB": 35, "OAL": 60, "SFDM": 3, "RE": 0.9, "shoulder-diameter": 1.1,
            "shoulder-length": 10}, shaft={"segments": [{"height": 1.6454, "lower-diameter": 1.1, "upper-diameter": 3}]}))
        self.assertEqual((d["shank_d"], d["shank_len"]), (1.1, 8.388))
        self.assertEqual(d["body"]["spun"][1:4], [[10, 0.55, 0], [11.6454, 1.5, 0], [35, 1.5, 0]])
        self.assert_verifies(result)

    def test_cone_right_at_the_head(self):
        # Harvey dovetails without a reach: never thinner than the cone
        d, result = one(tool("dovetail mill", description="dovetail", unit="inches", geometry={
            "DC": 0.5, "LCF": 0.218, "LB": 0.61816, "OAL": 3, "SFDM": 0.5, "TA": 30, "RE": 0,
            "shoulder-diameter": 0.24827528, "shoulder-length": 0.218},
            shaft={"segments": [{"height": 0, "lower-diameter": 0.248, "upper-diameter": 0.248},
                                {"height": 0.1502, "lower-diameter": 0.248, "upper-diameter": 0.5}]}))
        self.assertLess(d["flute_len"] + d["shank_len"] - 0.218 * 25.4, 0.0011)
        self.assertGreater(d["shank_d"], 0.248 * 25.4)
        self.assertEqual(d["body"]["spun"][1][1] * 2, d["shank_d"])
        self.assertGreater(d["flute_len"], 0.218 * 25.4 * 0.99)
        self.assert_verifies(result)

    def test_neck_widest_at_its_start(self):
        # Harvey back chamfer cutters: the neck tapers a little toward the shank
        d, result = one(keyseat(segments=((0.5, 0.162, 0.156), (0.044, 0.156, 0.5))))
        self.assertAlmostEqual(d["shank_d"], 0.162 * 25.4, places=5)
        cone = 0.044 * (0.162 - 0.156) / (0.5 - 0.156)
        self.assertAlmostEqual(d["shank_len"], (0.5 + cone) * 25.4, places=5)
        self.assert_verifies(result)

    def test_dovetail_flank_meeting_the_neck_exactly(self):
        # 45 degrees: the limit itself misses CamCraft's rule by 1e-16
        d, result = one(tool("dovetail mill", unit="inches", geometry={
            "DC": 0.08, "LCF": 0.014, "LB": 0.44532, "SFDM": 0.125, "TA": 45, "RE": 0,
            "shoulder-diameter": 0.052, "shoulder-length": 0.014},
            shaft={"segments": [{"height": 0.375, "lower-diameter": 0.052, "upper-diameter": 0.047},
                                {"height": 0.0365, "lower-diameter": 0.047, "upper-diameter": 0.125}]}))
        self.assertAlmostEqual(d["flute_len"], 0.3556, places=5)
        self.assert_verifies(result)

    def test_not_stated_or_not_narrower_is_skipped(self):
        for e in (keyseat(segments=()), keyseat(segments=((0.75, 0.5, 0.5),)),
                  keyseat(segments=((1.5, 0.25, 0.25),)),  # longer than the body
                  keyseat(segments=((0.75, 0, 0.25),)), keyseat({"shoulder-length": 0.2})):
            r = convert([e])
            self.assertEqual(r.tools, [])
            self.assertIn("neck (shank diameter) 12.7", r.skipped[0].reason)

    def test_narrow_shank_is_unchanged(self):
        # a shank narrower than the head stays the neck over the whole body
        d, _ = one(keyseat({"SFDM": 0.375, "shoulder-diameter": 0.375}))
        self.assertAlmostEqual(d["shank_d"], 0.375 * 25.4)
        self.assertAlmostEqual(d["shank_len"], (1.30951 - 0.093) * 25.4, places=5)
        self.assertNotIn("body", d)

    def test_output_check_catches_a_wrong_neck(self):
        def spun(d, i, z=0.0, scale=1.0):
            q = d["body"]["spun"][i]
            d["body"]["spun"][i] = [q[0] + z, q[1] * scale, 0]
        for change in (lambda d: d.update(shank_d=d["shank_d"] * 0.9), lambda d: d.update(shank_len=d["shank_len"] + 1),
                       lambda d: spun(d, 2, scale=0.7), lambda d: spun(d, 3, scale=0.7), lambda d: d.pop("body")):
            _, result = one(keyseat())
            d = result.tools[0]["definition"]
            change(d)
            if "body" in d:
                self.assertNotEqual(compare(d, keyseat(), Options()), [])
            else:  # --holder-cylinders: the cylinder still has to cover the shank
                self.assertEqual(compare(d, keyseat(), Options(holder_bodies=False)), [])
                d["holder_len"] = 40.0
                self.assertNotEqual(compare(d, keyseat(), Options(holder_bodies=False)), [])

    def test_corner_radius_dovetail_is_skipped(self):
        e = tool("dovetail mill", geometry={"DC": 20, "LCF": 5, "SFDM": 8, "LB": 30, "TA": 45, "RE": 1})
        r = convert([e])
        self.assertIn("flank 1.41 mm inside the real one", r.skipped[0].reason)
        self.assertEqual(r.skipped[0].category, "corner radius dovetail mill")
        d, _ = one({**e, "geometry": {**e["geometry"], "RE": 0}})
        self.assertTrue(any("corner radius" in p for p in compare(d, e)))


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
        self.assertEqual([s.category for s in r.skipped], ["reamer", "counterbore", "form mill", "probe"])

    def test_every_fusion_type_converts_or_is_named(self):
        # the 40 types of Autodesk's ToolLibrary.schema.json (#19)
        families = {t: unsupported_category({"type": t, "geometry": {}}) for t in FUSION_TYPES}
        self.assertEqual(len(families), 40)
        unnamed = {t for t in FUSION_TYPES if t not in KINDS and not families[t]}
        self.assertEqual(unnamed, {"holder"})  # counted, not skipped
        for t in FUSION_TYPES:
            with self.subTest(type=t):
                r = convert([tool(t)])  # milling fields: some types need more, none may crash
                self.assertEqual(len(r.tools) + len(r.skipped) + r.holders, 1)
                if families[t]:
                    self.assertEqual([s.category for s in r.skipped], [families[t]])
                r = convert([{"type": t}])
                self.assertEqual((len(r.tools), len(r.skipped) + r.holders), (0, 1))

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


class DefaultSpeedTests(unittest.TestCase):
    """#8: per-kind and per-diameter defaults for tools Fusion gives no speed."""

    def bare(self, type_="flat end mill", dc=6, nof=2, **geometry):
        return tool(type_, presets=[], geometry={"DC": dc, "NOF": nof, **geometry})

    def test_vc_and_fz(self):
        d, result = one(self.bare(dc=10, nof=3), Options(default_vc=100, default_fz=0.05))
        self.assertAlmostEqual(d["rpm"], 100 * 1000 / (math.pi * 10), places=4)   # 3183.1
        self.assertAlmostEqual(d["feed"], d["rpm"] * 3 * 0.05, places=3)
        self.assertEqual(sorted(result.defaults), ["feed from --default-fz 0.05", "rpm from --default-vc 100 m/min"])

    def test_max_rpm_caps_computed_rpm_only(self):
        d, result = one(self.bare(dc=0.2), Options(default_vc=200, default_fz=0.005, max_rpm=24000))
        self.assertEqual(d["rpm"], 24000)
        self.assertAlmostEqual(d["feed"], 24000 * 2 * 0.005)
        self.assertIn("rpm from --default-vc 200 m/min, capped at --max-rpm", result.defaults)
        d, _ = one(self.bare(), Options(default_rpm=30000, default_feed=500, max_rpm=24000))
        self.assertEqual(d["rpm"], 30000)  # an explicit rpm is what was asked for

    def test_kind_beats_global_and_explicit_beats_formula(self):
        options = Options(default_rpm=18000, default_feed=1000, default_vc=150, default_fz=0.04,
                          kind_defaults={"drill": {"vc": 60, "feed": 150}})
        d, _ = one(self.bare("drill", dc=8, SIG=118), options)
        self.assertAlmostEqual(d["rpm"], 60 * 1000 / (math.pi * 8), places=4)  # kind v_c beats global rpm
        self.assertEqual(d["feed"], 150)
        d, _ = one(self.bare(dc=8), options)
        self.assertEqual((d["rpm"], d["feed"]), (18000, 1000))  # global rpm/feed beat global v_c/f_z

    def test_slitting_saw_uses_all_its_teeth(self):
        e = self.bare("slot mill", dc=19.05, nof=72, LCF=0.5, SFDM=6.35, LB=7)
        d, _ = one(e, Options(default_vc=60, default_fz=0.005))
        self.assertNotIn("flutes", d)  # CamCraft's flute field stops at 32
        self.assertAlmostEqual(d["feed"], d["rpm"] * 72 * 0.005, places=3)

    def test_fz_without_flute_count_is_named(self):
        r = convert([self.bare(nof=None)], Options(default_rpm=18000, default_fz=0.02))
        self.assertIn("no flute count for --default-fz", r.skipped[0].reason)

    def test_tap_still_feeds_pitch(self):
        d, _ = one(self.bare("tap right hand", dc=8, TP=1.25), Options(default_vc=10, default_feed=999))
        self.assertAlmostEqual(d["feed"], d["rpm"] * 1.25, places=3)

    def test_bad_defaults_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown tool kind 'drlil'"):
            convert([], Options(kind_defaults={"drlil": {"rpm": 3000}}))
        with self.assertRaisesRegex(ValueError, "must be over 0"):
            convert([], Options(default_vc=-5))

    def test_check_catches_a_wrong_default(self):
        result = convert([self.bare(dc=10)], Options(default_vc=100, default_fz=0.05))
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])
            doc = json.loads(path.read_text())
            doc["tools"][0]["definition"]["rpm"] = 4000  # not what v_c 100 on D10 gives
            path.write_text(json.dumps(doc))
            self.assertTrue(any("no Fusion preset or default" in p for p in verify_file(path, result)))

    def test_cli_kind_values_and_summary(self):
        lib = {"data": [self.bare("drill", dc=8, SIG=118), self.bare(dc=6)]}
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "lib.json"
            src.write_text(json.dumps(lib))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main([str(src), "--default-rpm", "18000", "--default-rpm", "drill=3000",
                             "--default-feed", "drill=150", "--default-fz", "0.03"])
            self.assertEqual(code, 0)
            doc = json.loads(src.with_suffix(".camtools").read_text())
            drill = next(t["definition"] for t in doc["tools"] if t["definition"]["kind"] == "drill")
            self.assertEqual((drill["rpm"], drill["feed"]), (3000, 150))
            self.assertIn("defaults: 1 tool got rpm from --default-rpm drill=3000", out.getvalue())
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(main([str(src), "--default-vc", "drill=fast"]), 1)
            self.assertIn("expected a number or KIND=number", err.getvalue())


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
            {"name": "Hardwood", "n": 16000, "v_f": 3000},          # CamCraft 0.26.0
            {"name": "Acetal", "n": 12000},                         # no feed: reported
        ]

    def test_rows_per_material(self):
        result = convert([tool(presets=self.presets())], Options(all_presets=True))
        rows = result.tools[0]["definition"]["cutting_data"]
        self.assertEqual([r["material"] for r in rows], ["unknown", "aluminium_6061", "mild_steel", "hardwood"])
        self.assertEqual(rows[1], {"material": "aluminium_6061", "rpm": 18000, "feed": 2000, "stepdown": 3})
        outcomes = {name: outcome for (name, outcome) in result.presets}
        self.assertEqual(outcomes["Aluminum finish"], "duplicate aluminium_6061")
        self.assertEqual(outcomes["Titanium"], "unmatched")
        self.assertEqual(outcomes["Hardwood"], "hardwood")
        self.assertEqual(outcomes["Acetal"], "no speeds")
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])

    def test_material_option_keeps_chosen_preset(self):
        # --material names the chosen (first) preset; a preset mapping to the same word is a duplicate
        result = convert([tool(presets=self.presets())],
                         Options(all_presets=True, material="brass"))
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
DEFAULT_SPEEDS = Options(default_vc=100, default_fz=0.02, default_feed=500, max_rpm=24000)


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
                self.check(result)
                # a catalog without presets converts on defaults only (#16's thread mills)
                self.check(convert(load_library(src), DEFAULT_SPEEDS))
                if any(e.get("type") == "tapered mill" for e in load_library(src)):
                    self.check(convert(load_library(src), dataclasses.replace(DEFAULT_SPEEDS, unreleased_tapers=True)))
                if not src.is_relative_to(THIRD_PARTY):
                    unexplained = [f"{s.name}: {s.reason}" for s in result.skipped if not s.category]
                    self.assertEqual(unexplained, [])

    def check(self, result):
        if not result.tools:
            return  # holder-only / empty library
        for t in result.tools:
            self.assertIsNone(why_not_sane(t["definition"]), t["name"])
        with tempfile.TemporaryDirectory() as tmp:
            path = camtools.write(result, Path(tmp) / "out.camtools")
            self.assertEqual(verify_file(path, result), [])


if __name__ == "__main__":
    unittest.main()
