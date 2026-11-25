"""Fusion turning tools (#2)."""

import math
import unittest

from fusitool2craftool.convert import Options, convert

from .test_convert import holder as spindle_holder
from .test_convert import one, verify

CSS = {"name": "P", "v_c": 200, "f_n": 0.2, "use-constant-surface-speed": True, "use-feed-per-revolution": True}


def lathe(type_, geometry=None, holder=None, setup=None, presets=None, unit="millimeters", **extra):
    """A Fusion turning tool; the shapes are those of the test libraries."""
    g = {"turning general": {"INSD": 12.7, "RE": 0.8, "SC": "C"},
         "turning boring": {"INSD": 9.525, "RE": 0.4, "SC": "C"},
         "turning grooving": {"LH": 2, "OAL": 5.6, "RE": 0, "SC": "groove square",
                              "tool_grooveWidth": 3.1, "tool_insertWidth": 3},
         "turning threading": {"OAL": 30, "SC": "thread iso double", "TP": 2, "thread-profile-angle": 60,
                               "tool_insertWidth": 4, "tool_internalThread": False}}[type_]
    bar = type_ == "turning boring"
    h = {"HAND": "R", "W": 20, "H": 20, "CW": 14.5 if bar else 25, "LH": 50 if bar else 32, "OAL": 125,
         "THSC": {"turning general": "L", "turning boring": "boring bar l", "turning grooving": "groove external",
                  "turning threading": "thread straight"}[type_]}
    return {"type": type_, "unit": unit, "description": extra.pop("description", type_), "BMC": "carbide",
            "geometry": {**g, **(geometry or {})}, "holder": {**h, **(holder or {})},
            "setup": {"HAND": True, "angle": 90 if bar else 0, "compensation": "tip tangent", **(setup or {})},
            "start-values": {"presets": [CSS] if presets is None else presets},
            "post-process": {"number": 7}, **extra}


def refusal(entry, options=None):
    skipped = convert([entry], options).skipped
    assert skipped, "converted"
    return skipped[0]


TYPES = ("turning general", "turning boring", "turning grooving", "turning threading")


class GeometryTests(unittest.TestCase):
    def test_turning(self):
        d, _ = one(lathe("turning general"))
        self.assertEqual((d["kind"], d["nose_radius"], d["diameter"], d["flute_len"]), ("turning", 0.8, 20, 32))
        # the turret clamps the shank: a 125 mm holder must not trip a machine's tool length limit
        self.assertEqual((d["shank_len"], d["holder_d"], d["holder_len"]), (0, 0, 0))
        self.assertEqual((d["tool_material"], d["number"]), ("carbide", 7))

    def test_round_insert_is_all_nose(self):
        e = lathe("turning general", {"SC": "R", "INSD": 12.7, "RE": 12.7})
        d, result = one(e)
        self.assertEqual(d["nose_radius"], 6.35)
        self.assertTrue(any("round insert" in w for w in result.warnings))
        self.assertEqual(verify([e]), [])

    def test_boring_bar_covers_the_real_bar(self):
        for w, f in ((20, 14.5), (20, 10.5), (4.8, 3), (12, 16), (20, 10), (20, 8)):
            e = lathe("turning boring", {"RE": 0.1}, {"W": w, "H": w, "CW": f})
            d, _ = one(e)
            h, s = d["diameter"], d["shank_d"]  # CamCraft's bar_spans
            self.assertGreaterEqual(max(s, (h + s) / 2) + 1e-9, f + w / 2)
            self.assertGreaterEqual(max(0, (s - h) / 2) + 1e-9, w / 2 - f)
            self.assertEqual(verify([e]), [])
        d, result = one(lathe("turning boring"))
        self.assertEqual((d["diameter"], d["shank_d"], d["flute_len"], d["shank_len"]), (24.5, 24.5, 50, 75))
        self.assertTrue(any("stick-out" in w for w in result.warnings))

    def test_blade(self):
        d, result = one(lathe("turning grooving", {"RE": 0.2}))
        self.assertEqual((d["kind"], d["width"], d["nose_radius"]), ("groove", 3.1, 0))
        self.assertNotIn("orientation", d)  # Fusion's touch-off corner is not confirmed (#20)
        self.assertTrue(any("corner radius" in w for w in result.warnings))
        _, result = one(lathe("turning grooving", {"tool_insertWidth": 1.7}))
        self.assertTrue(any("check which one" in w for w in result.warnings))

    def test_thread(self):
        d, _ = one(lathe("turning threading"))
        self.assertEqual((d["kind"], d["tip_angle"]), ("thread", 60))
        self.assertAlmostEqual(d["width"], 1.732)
        _, result = one(lathe("turning threading", {"maximum-thread-pitch": 3}))
        self.assertTrue(any("partial profile, 2 to 3 mm" in w for w in result.warnings))

    def test_partial_profile_thread(self):
        # Sandvik 266RFGZ2525-16: TP is the coarsest pitch of the range (#20)
        d, result = one(lathe("turning threading", {"TP": 1.25, "TPN": 0.5, "TPX": 1.25}))
        self.assertAlmostEqual(d["width"], 1.0825)
        self.assertTrue(any("partial profile, 0.5 to 1.25 mm" in w and "written for 1.25 mm" in w
                            for w in result.warnings))
        for same in ({"TPN": 2, "TPX": 2}, {"minimum-thread-pitch": 2, "maximum-thread-pitch": 2}, {}):
            _, result = one(lathe("turning threading", {"TP": 2, **same}))
            self.assertFalse(any("partial profile" in w for w in result.warnings))

    def test_orientation(self):
        for type_, holder, setup, code in (
                ("turning general", {"HAND": "R"}, {}, 2), ("turning general", {"HAND": "L"}, {}, 1),
                ("turning general", {"HAND": "N"}, {}, None), ("turning boring", {"HAND": "R"}, {}, 3),
                ("turning boring", {"HAND": "L"}, {}, None), ("turning general", {}, {"angle": 45}, None),
                ("turning general", {}, {"HAND": False}, None),
                ("turning general", {}, {"compensation": "insert center"}, None)):
            with self.subTest(type=type_, holder=holder, setup=setup):
                e = lathe(type_, holder=holder, setup=setup)
                d, result = one(e)
                self.assertEqual(d.get("orientation"), code)
                self.assertEqual(any("orientation" in w for w in result.warnings), code is None)
                self.assertEqual(verify([e]), [])

    def test_inches(self):
        e = lathe("turning boring", {"RE": 1 / 64}, {"W": 1, "H": None, "CW": 0.64, "LH": 1.25, "OAL": 12},
                  presets=[{**CSS, "v_c": 600, "f_n": 0.01}], unit="inches")
        d, _ = one(e)
        self.assertAlmostEqual(d["diameter"], (0.64 + 0.5) * 25.4)
        self.assertAlmostEqual(d["turning"]["vc"], 600 * 0.3048)  # ft/min
        self.assertAlmostEqual(d["turning"]["feed_per_rev"], 0.254)
        self.assertEqual(verify([e]), [])

    def test_refusals(self):
        for e, why, category in (
                (lathe("turning grooving", holder={"THSC": "groove internal"}), "internal grooving", "internal grooving tool"),
                (lathe("turning grooving", holder={"THSC": "groove face"}), "face grooving", "face grooving tool"),
                (lathe("turning grooving", {"SC": "groove round"}), "round grooving", "round grooving tool"),
                (lathe("turning grooving", {"tool_insertWidth": 4}), "wider than its cutting edge", None),
                (lathe("turning grooving", {"tool_grooveWidth": None}), "tool_grooveWidth", None),
                (lathe("turning threading", {"tool_internalThread": True}), "internal threading", "internal threading tool"),
                (lathe("turning threading", {"TP": None}), "no thread pitch", None),
                (lathe("turning threading", {"thread-profile-angle": 29}), "29 degrees", None),
                (lathe("turning threading", {"thread-profile-angle": None}), "no thread form", None),
                (lathe("turning boring", holder={"CW": None}), "CW", None),
                (lathe("turning general", {"RE": 0}), "no insert nose radius", None),
                (lathe("turning general", {"SC": "R", "INSD": None}), "INSD", None),
                (lathe("turning general", holder={"W": None, "H": None}), "shank size", None),
                (lathe("turning general", holder={"LH": 0}), "head length", None),
                (lathe("turning general", {"RE": 11}), "nose radius", None)):
            with self.subTest(why=why):
                s = refusal(e)
                self.assertIn(why, s.reason)
                self.assertEqual(s.category, category)

    def test_no_spindle_holder(self):
        options = Options(default_holder=spindle_holder())
        for type_ in TYPES:
            d, _ = one(lathe(type_), options)
            self.assertEqual((d["holder_d"], d["holder_len"], "body" in d), (0, 0, False))
            self.assertEqual(verify([lathe(type_)], options), [])


class SpeedTests(unittest.TestCase):
    def test_constant_surface_speed(self):
        d, result = one(lathe("turning general"))
        self.assertEqual(d["turning"], {"vc": 200, "feed_per_rev": 0.2})
        self.assertEqual(d["rpm"], round(200000 / (math.pi * 50)))
        self.assertAlmostEqual(d["feed"] / d["rpm"], 0.2)  # CamCraft posts F = feed / rpm
        self.assertEqual(sum(result.defaults.values()), 1)

    def test_work_diameter_and_max_rpm(self):
        options = Options(turning_d=10, max_rpm=3000)
        d, _ = one(lathe("turning general"), options)
        self.assertEqual((d["rpm"], d["turning"]["max_rpm"]), (3000, 3000))
        self.assertAlmostEqual(d["feed"], 600)
        self.assertEqual(verify([lathe("turning general")], options), [])

    def test_constant_rpm(self):
        e = lathe("turning general", presets=[{"n": 900, "v_c": 100, "f_n": 0.1, "use-constant-surface-speed": False}])
        d, _ = one(e)
        self.assertEqual((d["rpm"], d["feed"]), (900, 90))
        self.assertNotIn("turning", d)
        self.assertEqual(verify([e]), [])

    def test_feed_per_minute(self):
        e = lathe("turning general", presets=[{**CSS, "v_f": 150, "use-feed-per-revolution": False}])
        d, result = one(e)
        self.assertEqual(d["feed"], 150)
        self.assertNotIn("turning", d)
        self.assertTrue(any("constant spindle speed" in w for w in result.warnings))
        self.assertEqual(verify([e]), [])

    def test_thread_runs_at_constant_rpm(self):
        for preset, rpm in (({"n": 500}, 500), ({"n": 400, "use-constant-surface-speed": True, "v_c": 100}, 400),
                            ({"n": 0, "v_c": 100, "use-constant-surface-speed": True}, round(100000 / (math.pi * 50)))):
            e = lathe("turning threading", presets=[preset])
            d, _ = one(e)
            self.assertEqual((d["rpm"], d["feed"]), (rpm, 2 * rpm))
            self.assertNotIn("turning", d)
            self.assertEqual(verify([e]), [])

    def test_defaults(self):
        e = lathe("turning general", presets=[])
        self.assertIn("--default-rpm", refusal(e).reason)
        self.assertIn("--default-feed", refusal(e, Options(default_rpm=500)).reason)
        options = Options(default_rpm=500, kind_defaults={"turning": {"feed": 80}})
        d, _ = one(e, options)
        self.assertEqual((d["rpm"], d["feed"]), (500, 80))
        self.assertEqual(verify([e], options), [])
        options = Options(default_vc=150, default_feed=80)
        self.assertAlmostEqual(one(e, options)[0]["rpm"], 150000 / (math.pi * 50), places=5)
        self.assertEqual(verify([e], options), [])

    def test_placeholder_feed_is_warned_about(self):
        _, result = one(lathe("turning general", presets=[{**CSS, "f_n": 1}]))
        self.assertTrue(any("untouched preset" in w for w in result.warnings))

    def test_out_of_range_is_skipped(self):
        for preset, why in (({**CSS, "v_c": 3000}, "vc"), ({**CSS, "f_n": 4}, "feed_per_rev")):
            self.assertIn(why, refusal(lathe("turning general", presets=[preset])).reason)

    def test_no_material_rows(self):
        e = lathe("turning general", presets=[{**CSS, "name": "6061 aluminum"}])
        options = Options(all_presets=True, material="brass")
        d, result = one(e, options)
        self.assertNotIn("cutting_data", d)
        self.assertEqual(d["material_group"], "brass")
        self.assertEqual(list(result.presets), [("6061 aluminum", "lathe tool")])
        self.assertEqual(verify([e], options), [])

    def test_bad_work_diameter(self):
        with self.assertRaisesRegex(ValueError, "turning-d"):
            convert([], Options(turning_d=0))


class OutputCheckTests(unittest.TestCase):
    def test_undamaged(self):
        for type_ in TYPES:
            self.assertEqual(verify([lathe(type_)]), [], type_)

    def test_catches_damage(self):
        for type_, change in (
                ("turning general", {"nose_radius": 0.4}), ("turning general", {"kind": "bore"}),
                ("turning general", {"orientation": 1}), ("turning general", {"rpm": 100}),
                ("turning general", {"feed": 1}), ("turning general", {"turning": {"vc": 60, "feed_per_rev": 0.2}}),
                ("turning general", {"turning": None}), ("turning general", {"shank_len": 93}),
                ("turning general", {"holder_d": 50}), ("turning general", {"number": 8}),
                ("turning general", {"cutting_data": [{"material": "brass", "rpm": 1, "feed": 1}]}),
                ("turning boring", {"diameter": 20, "shank_d": 20}), ("turning boring", {"shank_len": 0}),
                ("turning grooving", {"width": 3.0}), ("turning threading", {"width": 1.0}),
                ("turning threading", {"tip_angle": 55}), ("turning threading", {"feed": 100})):
            with self.subTest(type=type_, change=change):
                self.assertTrue(verify([lathe(type_)], change=lambda d: d.update(change)))


if __name__ == "__main__":
    unittest.main()
