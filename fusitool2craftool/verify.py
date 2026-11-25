"""Double-check a written ``.camtools`` file against the Fusion source.

The file is read back from disk and every tool is compared with the Fusion
entry it came from, recomputing the expected values straight from Fusion's
fields (unit scaling included). This catches anything lost or changed
between reading, converting and writing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .convert import INCH, KINDS, MATERIALS, Conversion, is_flat_bottom, presets_of

TOL = 1e-3  # mm / rpm, after rounding to 6 decimals


def verify_file(path: str | Path, conversion: Conversion) -> list[str]:
    """Return a list of problems; empty means the file matches its source."""
    problems: list[str] = []
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"cannot read back {path}: {e}"]

    if doc.get("format") != "camcraft-tools":
        problems.append(f"format is {doc.get('format')!r}, not 'camcraft-tools'")
    if doc.get("units") != "mm":
        problems.append(f"units are {doc.get('units')!r}, not 'mm'")
    tools = doc.get("tools")
    if not isinstance(tools, list):
        return problems + ["no tools list in the file"]

    expected_count = conversion.input_tools - len(conversion.skipped)
    if len(tools) != expected_count:
        problems.append(
            f"file holds {len(tools)} tools, expected {expected_count} "
            f"({conversion.input_tools} in Fusion - {len(conversion.skipped)} skipped)"
        )

    names = [t.get("name") for t in tools]
    for n in {n for n in names if names.count(n) > 1}:
        problems.append(f"tool name '{n}' appears more than once")

    for t in tools:
        name = t.get("name")
        source = conversion.sources.get(name)
        if source is None:
            problems.append(f"'{name}' has no Fusion source tool")
            continue
        problems.extend(f"{name}: {p}" for p in compare(t.get("definition") or {}, source, conversion.options))
    return problems


def compare(d: dict, entry: dict, options=None) -> list[str]:
    """Differences between one written definition and its Fusion entry.

    With the conversion's ``options``, an rpm or feed Fusion does not state
    must be exactly the default those options give; without them, any.
    """
    out: list[str] = []
    unit = INCH if str(entry.get("unit", "")).lower() in ("inches", "inch", "in") else 1.0
    g = entry.get("geometry") or {}
    ftype = str(entry.get("type", "")).lower()

    def check(field: str, expected, fusion_key: str) -> None:
        if expected is None:
            return
        got = d.get(field)
        if not isinstance(got, (int, float)) or not math.isclose(got, expected, abs_tol=TOL, rel_tol=1e-6):
            out.append(f"{field} is {got}, Fusion {fusion_key} gives {round(expected, 6)}")

    def f(key):
        v = g.get(key)
        return float(v) * unit if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    check("diameter", f("DC"), "DC")
    flute, flute_key = f("LCF"), "LCF"
    if ftype in ("slot mill", "lollipop mill", "dovetail mill"):
        # undercut: the neck is the wider of the shaft / shoulder diameters
        necks = [v for v in (f("SFDM"), f("shoulder-diameter")) if v]
        if necks:
            check("shank_d", max(necks), "max(SFDM, shoulder-diameter)")
        if ftype == "lollipop mill" and necks and flute is not None and f("DC"):
            r, sr = f("DC") / 2, max(necks) / 2
            meet = r + math.sqrt(max(r * r - sr * sr, 0.0))  # where the neck leaves the ball
            if flute > meet:
                flute, flute_key = meet, "LCF capped where the neck leaves the ball"
        ta = g.get("TA")
        if ftype == "dovetail mill" and _is_num(ta):
            check("tip_angle", 90.0 - ta, "90 - TA")  # Fusion: off the axis; CamCraft: off the bottom
            if necks and flute is not None and f("DC") and 0 < ta < 90:
                top = (f("DC") - max(necks)) / 2 / math.tan(math.radians(ta))  # flank meets the neck
                if flute > top:
                    flute, flute_key = top, "LCF capped where the flank meets the neck"
    elif f("SFDM") and not (ftype == "thread mill" and flute is None):
        check("shank_d", f("SFDM"), "SFDM")
    check("flute_len", flute, flute_key)
    body = f("LB") or f("OAL")
    single = ftype == "thread mill" and flute is None and g.get("NT") == 1
    if single:
        out.extend(_single_form_problems(d, g, f, check, body))
    elif body is not None and flute is not None:
        check("shank_len", max(body - flute, 0.0), f"LB - {flute_key}")

    nof = g.get("NOF")
    if isinstance(nof, (int, float)) and float(nof).is_integer() and 1 <= nof <= 32:
        if d.get("flutes") != int(nof):
            out.append(f"flutes is {d.get('flutes')}, Fusion NOF gives {int(nof)}")

    base_kind = KINDS.get(ftype)
    allowed = {base_kind}
    if base_kind in ("end_mill", "ball_nose", "bull_nose"):
        # a corner radius moves an end mill to bull nose; a rounded ball
        # radius a ball to bull nose; a radius of (about) D/2 makes a ball
        allowed |= {"bull_nose", "ball_nose"}
    if base_kind == "t_slot":
        allowed.add("keyseat")  # named keyseat / Woodruff in the description
    if base_kind == "drill" and _is_num(g.get("SIG")) and is_flat_bottom(float(g["SIG"])):
        allowed = {"end_mill"}  # CamCraft drills need a point (#17)
        if d.get("tip_angle") or d.get("corner_r"):
            out.append("a flat bottom drill must be a flat end mill (tip angle and corner radius 0)")
    if ftype == "corner chamfer end mill":
        # an inscribed corner radius, or a sharp corner without chamfer numbers (#18)
        w, a = f("chamfer-width"), g.get("chamfer-angle")
        usable = w is not None and w > 0 and _is_num(a) and 0 < a < 90
        allowed = {"bull_nose"} if usable else {"end_mill"}
        legs = (w * math.tan(math.radians(a)), w / math.tan(math.radians(a))) if usable else (0.0,)
        check("corner_r", min(legs), "chamfer (shorter leg)")
    if single:
        allowed = {"thread_mill_single"}
    if d.get("kind") not in allowed:
        out.append(f"kind is {d.get('kind')!r}, Fusion type '{ftype}' gives {base_kind!r}")

    if ftype == "chamfer mill" and isinstance(g.get("TA"), (int, float)):
        check("tip_angle", 2 * g["TA"], "2 x TA")
    if ftype == "counter sink" and isinstance(g.get("SIG"), (int, float)):
        check("tip_angle", float(g["SIG"]), "SIG")
    if ftype.startswith("tap"):
        check("pitch", f("TP"), "TP")
        hand = "left" if ftype == "tap left hand" else None
        if d.get("thread_hand") != hand:
            out.append(f"thread_hand is {d.get('thread_hand')!r}, Fusion type says {hand or 'right'}")

    out.extend(_holder_problems(d, entry, options))

    number = (entry.get("post-process") or {}).get("number")
    if isinstance(number, (int, float)) and number > 0 and d.get("number") != int(number):
        out.append(f"number is {d.get('number')}, Fusion tool number is {int(number)}")

    if (presets_of(entry) or options is not None) and _matching_preset(entry, d, options) is None:
        out.append(f"rpm {d.get('rpm')} / feed {d.get('feed')} match no Fusion preset or default")

    rows = d.get("cutting_data") or []
    words = [r.get("material") for r in rows]
    for w in sorted({w for w in words if words.count(w) > 1}):
        out.append(f"cutting data names {w} twice")
    for r in rows:
        if r.get("material") not in MATERIALS + ("unknown",):
            out.append(f"cutting data material {r.get('material')!r} is not a CamCraft material")
        elif _matching_preset(entry, {**d, "rpm": r.get("rpm"), "feed": r.get("feed")}, options) is None:
            out.append(f"cutting data for {r['material']}: rpm {r.get('rpm')} / feed {r.get('feed')} "
                       f"match no Fusion preset")
    return out


def _single_neck(g: dict, f, body) -> tuple[float, float] | None:
    """(reach, neck diameter) of a single-form thread mill, or None without a neck (#16)."""
    tooth = f("TPX") or f("TP")
    reach, shank = f("shoulder-length"), f("SFDM") or f("DC")
    if not (tooth and body and reach and shank and f("DC")) or not tooth < reach < body:
        return None
    # no stated neck: the root of an internal thread at the coarsest pitch (5/8 H)
    neck = f("shoulder-diameter") or f("DC") - 2 * 0.5413 * tooth
    return (reach, neck) if 0 < neck < shank else None


def _single_form_problems(d: dict, g: dict, f, check, body) -> list[str]:
    """Tooth, pitch range and neck of a single-form thread mill (#16)."""
    tooth = f("TPX") or f("TP")
    check("flute_len", tooth, "TPX (one pitch)")
    check("pitch", tooth, "TPX")
    check("pitch_min", f("TPN") or tooth, "TPN")
    angle = g.get("thread-profile-angle")
    check("tip_angle", float(angle) if _is_num(angle) and angle else 60.0, "thread-profile-angle")
    neck = _single_neck(g, f, body)
    if neck:
        check("shank_d", neck[1], "shoulder-diameter (or the tooth root)")
        check("shank_len", neck[0] - tooth, "shoulder-length - TPX")
    else:
        check("shank_d", f("SFDM"), "SFDM")
        if body is not None and tooth is not None:
            check("shank_len", max(body - tooth, 0.0), "LB - TPX")
    return []


def _matching_preset(entry: dict, d: dict, options=None) -> dict | None:
    """The preset the definition's speed and feed come from (unit-scaled).

    Recomputed here from Fusion's fields, not through the converter: the
    feed is ``v_f``; for a drill also ``v_f_plunge`` or ``f_n`` x ``n``;
    for a tap ``n`` x pitch (what CamCraft's tapping cycle feeds). A number
    the preset does not state is the default the options give
    (:func:`_expected_default`), or anything when the options are unknown.
    A tool without presets is checked against the defaults alone.
    """
    unit = INCH if str(entry.get("unit", "")).lower() in ("inches", "inch", "in") else 1.0
    g = entry.get("geometry") or {}
    kind, pitch = d.get("kind"), d.get("pitch", 0)
    if KINDS.get(str(entry.get("type", "")).strip().lower()) == "drill":
        kind = "drill"  # a flat bottom drill keeps Fusion's drill speeds (#17)
    rpm, feed = d.get("rpm", -1), d.get("feed", -1)
    if not (_is_num(rpm) and _is_num(feed)):
        return None
    dia = g["DC"] * unit if _is_num(g.get("DC")) else 0.0
    nof = g.get("NOF")
    flutes = int(nof) if _is_num(nof) and float(nof).is_integer() and nof >= 1 else None  # teeth, uncapped
    presets = presets_of(entry) or ([{}] if options is not None else [])
    for p in presets:
        n = p.get("n") if _is_num(p.get("n")) and p.get("n") > 0 else None
        if n is not None:
            if not math.isclose(rpm, n, abs_tol=TOL):
                continue
        elif options is not None and not _close(rpm, _expected_default(options, kind, "rpm", dia, flutes, rpm)):
            continue
        feeds = [p.get("v_f")]
        if kind == "drill":
            feeds.append(p.get("v_f_plunge"))
            if n is not None and _is_num(p.get("f_n")):
                feeds.append(p["f_n"] * n)
        feeds = [f * unit for f in feeds if _is_num(f) and f > 0]
        if kind == "tap":
            feeds.append(rpm * pitch)
        if feeds:
            if any(_close(feed, f) for f in feeds):
                return p
        elif options is None or _close(feed, _expected_default(options, kind, "feed", dia, flutes, rpm)):
            return p
    return None


def _expected_default(options, kind: str, what: str, dia: float, flutes: int | None, rpm: float):
    """The rpm or feed the --default-* options give this tool, or None.

    The kind's own defaults beat the global ones; at each level an explicit
    rpm/feed beats v_c/f_z. rpm = v_c x 1000 / (pi D), capped at --max-rpm;
    feed = rpm x flutes x f_z.
    """
    globals_ = {"rpm": options.default_rpm, "feed": options.default_feed,
                "vc": options.default_vc, "fz": options.default_fz}
    for values in (options.kind_defaults.get(kind, {}), globals_):
        if what == "rpm":
            if values.get("rpm"):
                return values["rpm"]
            if values.get("vc") and dia > 0:
                n = values["vc"] * 1000 / (math.pi * dia)
                return min(n, options.max_rpm) if options.max_rpm else n
        else:
            if values.get("feed"):
                return values["feed"]
            if values.get("fz") and flutes:
                return rpm * flutes * values["fz"]
    return None


def _holder_segments(entry: dict) -> list[tuple[float, float, float]] | None:
    """Fusion's holder segments as (height, lower r, upper r) in mm, tool end first."""
    holder = entry.get("holder")
    segs = holder.get("segments") if isinstance(holder, dict) else None
    if not segs:
        return None
    unit = str(holder.get("unit") or entry.get("unit") or "").lower()
    scale = INCH if unit in ("inches", "inch", "in") else 1.0
    out = []
    for seg in segs:
        vals = [seg.get(k) for k in ("height", "lower-diameter", "upper-diameter")]
        try:
            h, lo, up = (float(v) for v in vals)
        except (TypeError, ValueError):
            return None
        out.append((h * scale, lo * scale / 2, up * scale / 2))
    return out


def _with_shank(segs, entry: dict, options):
    """The holder segments with a single-form thread mill's shank under them.

    Its CamCraft shank is the neck, so the real shank is part of the body
    (#16), also when the holder itself is the default one.
    """
    g = entry.get("geometry") or {}
    unit = INCH if str(entry.get("unit", "")).lower() in ("inches", "inch", "in") else 1.0

    def f(key):
        return float(g[key]) * unit if _is_num(g.get(key)) else None

    if str(entry.get("type", "")).lower() != "thread mill" or f("LCF") or g.get("NT") != 1:
        return segs
    body = f("LB") or f("OAL")
    neck = _single_neck(g, f, body)
    if not neck:
        return segs
    if not segs:
        if options is None:
            return segs
        segs = [(options.holder_len, options.holder_d / 2, options.holder_d / 2)]
    r = (f("SFDM") or f("DC")) / 2
    return [(body - neck[0], r, r)] + segs


def _holder_problems(d: dict, entry: dict, options=None) -> list[str]:
    """The holder cylinder and drawn body (#12) against Fusion's segments.

    The cylinder is the largest diameter x the total height. A drawn body
    must be a CamCraft spun outline (spun.rs rules) seated on the shank's
    top, enclosed by exactly that cylinder, and nowhere thinner than
    Fusion's holder: sampled inside every segment, the outline's radius is
    at least Fusion's (merging points under 0.02 mm may only widen it).
    """
    out: list[str] = []
    segs = _holder_segments(entry)
    segs = _with_shank(segs, entry, options)
    if segs and max(max(lo, up) for _, lo, up in segs) > 0 and sum(h for h, _, _ in segs) > 0:
        for field, want in (("holder_d", 2 * max(max(lo, up) for _, lo, up in segs)),
                            ("holder_len", sum(h for h, _, _ in segs))):
            if not _close(d.get(field), want):
                out.append(f"{field} is {d.get(field)}, Fusion holder segments give {round(want, 6)}")
    body = d.get("body")
    if body is None:
        return out
    if options is not None and not options.holder_bodies:
        return out + ["holder body written although --holder-cylinders was given"]
    if not segs:
        return out + ["holder body written for a tool without Fusion holder segments"]
    pts = body.get("spun") if isinstance(body, dict) else None
    if not (isinstance(pts, list) and 3 <= len(pts) <= 64
            and all(isinstance(q, list) and len(q) == 3 and all(_is_num(v) for v in q) for q in pts)):
        return out + ["holder body has no valid spun outline (3..64 [z, r, arc] points)"]
    if body.get("source") != "drawn profile" or body.get("facets") != []:
        out.append("holder body must be a drawn profile with empty facets")
    if any(q[2] != 0 for q in pts):
        out.append("holder outline has arcs (Fusion segments are straight)")
    if pts[0][1] != 0 or pts[-1][1] != 0 or any(q[1] <= 1e-9 for q in pts[1:-1]):
        out.append("holder outline must close to the axis at both ends only")
    if any(b[0] < a[0] - 1e-9 for a, b in zip(pts, pts[1:])):
        out.append("holder outline runs back down")
    if any(math.hypot(b[0] - a[0], b[1] - a[1]) < 0.01 for a, b in zip(pts, pts[1:])):
        out.append("holder outline has a segment under CamCraft's 0.01 mm")
    base = (d.get("flute_len") or 0) + (d.get("shank_len") or 0)
    if not _close(pts[0][0], base):
        out.append(f"holder outline starts at z {pts[0][0]}, the shank's top is {round(base, 6)}")
    if not _close(pts[-1][0] - pts[0][0], d.get("holder_len")):
        out.append(f"holder outline is {round(pts[-1][0] - pts[0][0], 6)} mm tall, holder_len {d.get('holder_len')}")
    if not _close(2 * max(q[1] for q in pts), d.get("holder_d")):
        out.append(f"holder outline is {round(2 * max(q[1] for q in pts), 6)} mm wide, holder_d {d.get('holder_d')}")

    def radius_at(z: float) -> float:
        reach = 0.0
        for a, b in zip(pts, pts[1:]):
            if a[0] - 1e-9 <= z <= b[0] + 1e-9:
                if b[0] - a[0] < 1e-12:
                    reach = max(reach, a[1], b[1])
                else:
                    reach = max(reach, a[1] + (b[1] - a[1]) * (z - a[0]) / (b[0] - a[0]))
        return reach

    z = pts[0][0]
    for i, (h, lo, up) in enumerate(segs, 1):
        if h >= 0.1:
            for t in (0.25, 0.5, 0.75):
                want = lo + (up - lo) * t
                # 1 um: points are rounded to 6 decimals, and on a steep
                # cone (11 mm of radius over 0.4 mm) that moves r by ~3e-5
                if radius_at(z + h * t) < want - 1e-3:
                    out.append(f"holder outline is thinner than Fusion segment {i} at z {round(z + h * t, 3)}")
                    break
        z += h
    return out


def _close(a, b) -> bool:
    return _is_num(a) and _is_num(b) and math.isclose(a, b, abs_tol=TOL, rel_tol=1e-6)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
