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

from .convert import INCH, KINDS, Conversion

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
    check("flute_len", f("LCF"), "LCF")
    if f("SFDM"):
        check("shank_d", f("SFDM"), "SFDM")
    body = f("LB") or f("OAL")
    if body is not None and f("LCF") is not None:
        check("shank_len", max(body - f("LCF"), 0.0), "LB - LCF")

    nof = g.get("NOF")
    if isinstance(nof, (int, float)) and float(nof).is_integer() and 1 <= nof <= 32:
        if d.get("flutes") != int(nof):
            out.append(f"flutes is {d.get('flutes')}, Fusion NOF gives {int(nof)}")

    base_kind = KINDS.get(ftype)
    allowed = {base_kind}
    if base_kind in ("end_mill", "ball_nose"):
        allowed.add("bull_nose")  # a corner radius / rounded ball radius moves it
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

    preset = _matching_preset(entry, d)
    if preset is None and (entry.get("start-values") or {}).get("presets"):
        out.append(f"rpm {d.get('rpm')} / feed {d.get('feed')} match no Fusion preset")
    return out


def _matching_preset(entry: dict, d: dict) -> dict | None:
    """The preset whose n / v_f the definition carries (unit-scaled)."""
    unit = INCH if str(entry.get("unit", "")).lower() in ("inches", "inch", "in") else 1.0
    for p in (entry.get("start-values") or {}).get("presets") or []:
        n, vf = p.get("n"), p.get("v_f")
        if not isinstance(n, (int, float)) or not isinstance(vf, (int, float)):
            continue
        if math.isclose(d.get("rpm", -1), n, abs_tol=TOL) and math.isclose(d.get("feed", -1), vf * unit, abs_tol=TOL):
            return p
    return None
