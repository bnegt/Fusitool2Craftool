"""Fusion turning tools as CamCraft lathe tools (#2).

CamCraft (0.26.0) judges a lathe tool by its cutting footprint, not by the
insert's outline: ``turning`` and ``bore`` by the nose circle, ``groove`` by
the blade's width, ``thread`` by the V's width and angle. The shank numbers
only matter for a boring bar, whose body CamCraft checks in the hole.

Fusion keys: ``geometry`` is the insert (``RE`` nose radius, ``INSD`` inscribed
circle, ``tool_grooveWidth`` cutting width, ``tool_insertWidth`` the blade body
behind it, ``TP`` pitch), ``holder`` the holder (``W`` x
``H`` shank, ``LH`` head length, ``OAL``, ``CW`` from the shank's axis to the
tip on a boring bar, ``THSC`` shape, ``HAND``), ``setup`` how it is mounted.
"""

from __future__ import annotations

import math
from collections import Counter

from . import convert as c

LATHE_KINDS = {
    "turning general": "turning",
    "turning boring": "bore",
    "turning grooving": "groove",
    "turning threading": "thread",
}

THREAD_FORM = 60.0
WIDTH_PER_PITCH = 0.866  # CamCraft's V width for the 60 degree form
MAX_VC, MAX_FEED_PER_REV, MAX_RPM_CLAMP = 2000.0, 3.0, 100_000.0  # turning_data.rs
PLACEHOLDER_FEED = 0.8  # mm/rev; Fusion's untouched preset is 1 mm/rev


def category(entry: dict) -> str | None:
    """Name the lathe tool families CamCraft has no tool for."""
    ftype = str(entry.get("type", "")).strip().lower()
    g, h = entry.get("geometry") or {}, entry.get("holder") or {}
    thsc = str(h.get("THSC", "")).strip().lower()
    if ftype == "turning grooving":
        if thsc in ("groove internal", "groove face"):
            return f"{thsc.split()[1]} grooving tool"
        if str(g.get("SC", "")).strip().lower() == "groove round":
            return "round grooving tool"
    if ftype == "turning threading" and g.get("tool_internalThread") is True:
        return "internal threading tool"
    return None


def definition(entry: dict, options: c.Options, warnings: list[str], report: Counter) -> tuple[dict, dict | None]:
    """The CamCraft definition's geometry and speeds, and the preset they are from."""
    ftype = str(entry.get("type", "")).strip().lower()
    kind = LATHE_KINDS[ftype]
    unit = c._unit_scale(entry.get("unit"))
    g, h = entry.get("geometry") or {}, entry.get("holder") or {}

    def length(src: dict, key: str) -> float:
        v = c._num(src.get(key))
        return v * unit if v and v > 0 else 0.0

    family = category(entry)
    if family:
        raise c.ToolRefused(f"CamCraft has no {family}")

    shank = max(length(h, "W"), length(h, "H"))
    if not shank:
        raise c.ToolRefused("no holder shank size (W/H)")
    head = length(h, "LH")
    if not head:
        raise c.ToolRefused("no holder head length (LH)")
    d: dict = {
        "kind": kind, "diameter": shank, "corner_r": 0.0, "tip_angle": 0.0, "pitch": 0.0,
        "nose_radius": 0.0, "width": 0.0, "flute_len": head, "shank_d": shank,
        # the turret clamps the shank behind the head; a bar runs on into the hole
        "shank_len": max(length(h, "OAL") - head, 0.0) if kind == "bore" else 0.0,
        # a turret holds it: no spindle holder stands above a lathe tool
        "holder_d": 0.0, "holder_len": 0.0,
    }

    if kind in ("turning", "bore"):
        d["nose_radius"] = _nose(g, length, warnings)
    if kind == "bore":
        _bar(d, length(h, "CW"), shank)
        warnings.append(f"the bar's stick-out is not in Fusion: its whole {d['flute_len'] + d['shank_len']:g} mm length "
                        f"is written, check how far it really reaches")
    elif kind == "groove":
        _blade(d, g, length, warnings)
    elif kind == "thread":
        _thread(d, g, length, warnings)
    if kind in ("turning", "bore"):
        code = orientation(entry, kind)
        if code:
            d["orientation"] = code
        else:
            warnings.append("touch-off orientation not clear from Fusion (hand, angle or compensation): "
                            f"CamCraft's default {2 if kind == 'turning' else 3} applies, check it")

    rpm, feed, turning, preset = _speeds(entry, options, unit, kind, length(g, "TP"), warnings, report)
    d["rpm"], d["feed"] = rpm, feed
    if turning:
        d["turning"] = turning
    return d, preset


def _nose(g: dict, length, warnings: list[str]) -> float:
    re_, insd = length(g, "RE"), length(g, "INSD")
    if str(g.get("SC", "")).strip().upper() == "R":
        # a round insert is all nose
        if not insd:
            raise c.ToolRefused("round insert without a diameter (INSD)")
        if re_ and not math.isclose(re_, insd / 2, rel_tol=1e-3):
            warnings.append(f"round insert: nose radius {insd / 2:g} mm from its {insd:g} mm diameter, "
                            f"not Fusion's corner radius {re_:g} mm")
        return insd / 2
    if not re_:
        raise c.ToolRefused("no insert nose radius (RE)")
    return re_


def _bar(d: dict, f: float, w: float) -> None:
    """A boring bar's body (CamCraft ``bar_spans``), never smaller than the real one.

    The tip stands ``f`` (Fusion ``CW``) from the axis of a bar of diameter
    ``w``, so the bar reaches ``f + w/2`` from the tip. CamCraft spans a head of
    ``h`` by ``h`` inward of the tip and a shank of ``s`` by the larger of ``s``
    and ``(h + s)/2``: it has no offset of its own. Both as wide as the reach is
    the smallest body that covers the bar. A tip inside the bar's own edge
    (``f`` < w/2) is CamCraft's head on the shank's axis.
    """
    if not f:
        raise c.ToolRefused("no distance from the bar's axis to the tip (CW)")
    if f >= w / 2:
        d["diameter"] = d["shank_d"] = f + w / 2
    else:
        d["diameter"] = 2 * f


def _blade(d: dict, g: dict, length, warnings: list[str]) -> None:
    width, body = length(g, "tool_grooveWidth"), length(g, "tool_insertWidth")
    if not width:
        raise c.ToolRefused("no cutting width (tool_grooveWidth)")
    if body > width * (1 + 1e-6):
        raise c.ToolRefused(f"the blade body ({body:g} mm, tool_insertWidth) is wider than its cutting edge "
                            f"({width:g} mm, tool_grooveWidth): one of them is wrong in Fusion")
    if body and body < 0.75 * width:
        warnings.append(f"the blade body is only {body:g} mm (tool_insertWidth) behind a {width:g} mm edge "
                        f"(tool_grooveWidth): check which one you meant in Fusion")
    d["width"] = width
    corner = length(g, "RE")
    if corner:
        warnings.append(f"blade corner radius {corner:g} mm is not written: CamCraft's blade has sharp corners")


def _thread(d: dict, g: dict, length, warnings: list[str]) -> None:
    pitch = length(g, "TP")
    if not pitch:
        raise c.ToolRefused("no thread pitch (TP)")
    angle = c._num(g.get("thread-profile-angle"))
    if angle is None or not math.isclose(angle, THREAD_FORM, abs_tol=1e-6):
        raise c.ToolRefused(f"thread form {angle:g} degrees: only the 60 degree form has a known V width"
                            if angle else "no thread form angle (thread-profile-angle)")
    # A range of pitches: vendors give it as TPN..TPX with TP the coarsest,
    # Fusion's own fields may leave TP at the finest.
    pitches = [p for k in ("TPN", "TPX", "minimum-thread-pitch", "maximum-thread-pitch") if (p := length(g, k))]
    low, top = min(pitches + [pitch]), max(pitches + [pitch])
    if top > low * (1 + 1e-6):
        warnings.append(f"partial profile, {low:.4g} to {top:.4g} mm pitch: CamCraft's insert has one V, "
                        f"written for {pitch:.4g} mm, check the V and the feed for another pitch")
    d["tip_angle"], d["width"] = THREAD_FORM, WIDTH_PER_PITCH * pitch


def orientation(entry: dict, kind: str) -> int | None:
    """CamCraft's (LinuxCNC) tip code, where Fusion states it, else None.

    A right hand tool cuts toward the chuck, a left hand one away from it:
    codes 2 and 1 outside, 3 for a right hand bar inside. Only for the usual
    mounting (outside 0, bar 90 degrees, clockwise spindle) with Fusion's tip
    tangent point, which is the same point as CamCraft's.
    """
    h, s = entry.get("holder") or {}, entry.get("setup") or {}
    hand, angle = str(h.get("HAND", "")).strip().upper(), c._num(s.get("angle"))
    if s.get("HAND") is not True or str(s.get("compensation", "")).strip().lower() != "tip tangent":
        return None
    if kind == "turning" and angle == 0:
        return {"R": 2, "L": 1}.get(hand)
    if kind == "bore" and angle == 90:
        return {"R": 3}.get(hand)
    return None


def _speeds(entry: dict, options: c.Options, unit: float, kind: str, pitch: float,
            warnings: list[str], report: Counter):
    """rpm, feed (mm/min), CamCraft turning data (constant surface speed) and the preset.

    A lathe preset states a surface speed and a feed per turn, and no rpm: it
    depends on the work. CamCraft wants an rpm too, and posts F as feed / rpm,
    so both are written for one work diameter (``options.turning_d``); the
    turning data carries the real numbers. A thread runs at a constant rpm and
    one pitch per turn.
    """
    p = c._pick_preset(entry, options) or {}
    if options.preset and p and str(p.get("name", "")).strip().lower() != options.preset.strip().lower():
        warnings.append(f"no preset named '{options.preset}': used '{p.get('name')}'")
    n, vc = c._num(p.get("n")) or 0.0, c._num(p.get("v_c")) or 0.0
    vc *= 0.3048 if unit == c.INCH else 1.0  # ft/min in an inch library
    css = vc > 0 and (p.get("use-constant-surface-speed") is True
                      or (p.get("use-constant-surface-speed") is None and n <= 0))
    fn, vf = (c._num(p.get("f_n")) or 0.0) * unit, (c._num(p.get("v_f")) or 0.0) * unit
    per_rev = fn > 0 and (p.get("use-feed-per-revolution") is not False or vf <= 0)

    if n > 0 and (not css or kind == "thread"):
        rpm = n
    elif vc > 0:
        rpm = vc * 1000 / (math.pi * options.turning_d)
        if options.max_rpm:
            rpm = min(rpm, options.max_rpm)
        rpm = max(round(rpm), 1)
        report[f"rpm from Fusion's surface speed on a {options.turning_d:g} mm work diameter (--turning-d)"] += 1
        if kind == "thread":
            warnings.append(f"thread at {rpm:.6g} rpm, from {vc:g} m/min on a {options.turning_d:g} mm work diameter")
    else:
        rpm, how = c.default_rpm(options, kind, options.turning_d)
        if rpm is None:
            raise c.ToolRefused("no spindle or surface speed in Fusion (give --default-rpm or --default-vc)")
        warnings.append(f"no spindle or surface speed in Fusion: {rpm:.6g} rpm from {how}")
        report[f"rpm from {c._how_key(how)}"] += 1

    if kind == "thread":
        return rpm, rpm * pitch, None, p or None
    if per_rev:
        feed = fn * rpm
    elif vf > 0:
        feed = vf
    else:
        feed, how = c.default_feed(options, kind, rpm, None)
        if feed is None:
            raise c.ToolRefused("no cutting feed in Fusion (give --default-feed)")
        warnings.append(f"no cutting feed in Fusion: {feed:.6g} mm/min from {how}")
        report[f"feed from {c._how_key(how)}"] += 1
    turning = None
    if css and per_rev:
        turning = {"vc": vc, "feed_per_rev": fn}
        if options.max_rpm:
            turning["max_rpm"] = options.max_rpm
        if fn >= PLACEHOLDER_FEED:
            warnings.append(f"feed {fn:g} mm per turn looks like Fusion's untouched preset: check it before cutting")
    elif css:
        warnings.append("surface speed without a feed per turn: written as a constant spindle speed")
    return rpm, feed, turning, p or None


def why_not_sane(d: dict) -> str | None:
    """CamCraft's rules for the lathe kinds (tool_sanity.rs, turning_data.rs)."""
    kind, dia, r, w = d["kind"], d["diameter"], d["nose_radius"], d["width"]
    if kind == "turning" and not 0 < r <= dia / 2:
        return f"a turning insert's nose radius {r} mm must be over 0 and at most half the {dia} mm diameter"
    if kind in ("groove", "thread") and not 0 < w < dia:
        return f"a {'grooving blade' if kind == 'groove' else 'threading insert'}'s width {w} mm must be over 0 and under the {dia} mm diameter"
    if kind == "thread" and not 0 < d["tip_angle"] < 180:
        return f"a threading insert's included angle {d['tip_angle']} degrees must be over 0 and under 180"
    t = d.get("turning")
    if t:
        for key, top in (("vc", MAX_VC), ("feed_per_rev", MAX_FEED_PER_REV), ("max_rpm", MAX_RPM_CLAMP)):
            if key in t and not (math.isfinite(t[key]) and 0 < t[key] <= top):
                return f"turning data {key} {t[key]} is outside 0..{top:g}"
        if d["rpm"] > t.get("max_rpm", math.inf):
            return f"spindle speed {d['rpm']} rpm is over the turning max rpm {t['max_rpm']}"
    return None
