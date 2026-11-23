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

from .convert import INCH, KINDS, MATERIALS, Conversion, presets_of

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
        problems.extend(f"{name}: {p}" for p in compare(t.get("definition") or {}, source))
    return problems


def compare(d: dict, entry: dict) -> list[str]:
    """Differences between one written definition and its Fusion entry."""
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
    elif f("SFDM"):
        check("shank_d", f("SFDM"), "SFDM")
    check("flute_len", flute, flute_key)
    body = f("LB") or f("OAL")
    if body is not None and flute is not None:
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

    number = (entry.get("post-process") or {}).get("number")
    if isinstance(number, (int, float)) and number > 0 and d.get("number") != int(number):
        out.append(f"number is {d.get('number')}, Fusion tool number is {int(number)}")

    if presets_of(entry) and _matching_preset(entry, d) is None:
        out.append(f"rpm {d.get('rpm')} / feed {d.get('feed')} match no Fusion preset")

    rows = d.get("cutting_data") or []
    words = [r.get("material") for r in rows]
    for w in sorted({w for w in words if words.count(w) > 1}):
        out.append(f"cutting data names {w} twice")
    for r in rows:
        if r.get("material") not in MATERIALS + ("unknown",):
            out.append(f"cutting data material {r.get('material')!r} is not a CamCraft material")
        elif _matching_preset(entry, {**d, "rpm": r.get("rpm"), "feed": r.get("feed")}) is None:
            out.append(f"cutting data for {r['material']}: rpm {r.get('rpm')} / feed {r.get('feed')} "
                       f"match no Fusion preset")
    return out


def _matching_preset(entry: dict, d: dict) -> dict | None:
    """The preset the definition's speed and feed come from (unit-scaled).

    Recomputed here from Fusion's fields, not through the converter: the
    feed is ``v_f``; for a drill also ``v_f_plunge`` or ``f_n`` x ``n``;
    for a tap ``n`` x pitch (what CamCraft's tapping cycle feeds). A
    number the preset does not state may be a --default-rpm/--default-feed
    (or, for a tap, rpm x pitch).
    """
    unit = INCH if str(entry.get("unit", "")).lower() in ("inches", "inch", "in") else 1.0
    kind, pitch = d.get("kind"), d.get("pitch", 0)
    rpm, feed = d.get("rpm", -1), d.get("feed", -1)
    if not (_is_num(rpm) and _is_num(feed)):
        return None
    for p in presets_of(entry):
        n = p.get("n") if _is_num(p.get("n")) and p.get("n") > 0 else None
        if n is not None and not math.isclose(rpm, n, abs_tol=TOL):
            continue
        feeds = [p.get("v_f")]
        if kind == "drill":
            feeds.append(p.get("v_f_plunge"))
            if n is not None and _is_num(p.get("f_n")):
                feeds.append(p["f_n"] * n)
        feeds = [f * unit for f in feeds if _is_num(f) and f > 0]
        if kind == "tap":
            feeds.append(rpm * pitch)
        if not feeds or any(math.isclose(feed, f, abs_tol=TOL, rel_tol=1e-9) for f in feeds):
            return p
    return None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
