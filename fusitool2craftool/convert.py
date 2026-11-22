"""Map Fusion 360 tool entries to CamCraft tool definitions.

CamCraft's tool shape is the ``definition`` object of a ``.camtools`` file
(``ToolDto`` in CamCraft's ``crates/app/src/project/file.rs``). Every length
is millimetres, feeds are mm/min.

Fusion geometry keys used (ISO 13399 style names Fusion writes):

========  =============================================
DC        cutting diameter
LCF       flute (cutting) length
LB        body length below the holder (stick-out)
OAL       overall length
SFDM      shank diameter
RE        corner radius
NOF       number of flutes
SIG       point angle, included (drills, countersinks)
TA        taper angle, half angle (chamfer mills)
TP        thread pitch (taps, thread mills)
========  =============================================

A tool CamCraft would refuse is skipped here with the reason, instead of
written: CamCraft refuses a whole library over a single bad tool.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

INCH = 25.4

# CamCraft's workpiece material words (WorkMaterial::word in
# crates/tools/src/cutting.rs).
MATERIALS = (
    "aluminium_6061",
    "mild_steel",
    "stainless_304",
    "grey_cast_iron",
    "brass",
    "plastics_pom",
    "softwood",
    "hardwood",
    "mdf",
    "plywood",
    "acrylic",
    "hdpe",
)

# catalog_fields.rs limits.
MAX_SHORT = 80
MAX_NOTES = 500

# .camtools envelope versions (tool_library.rs).
VERSION = 1
LEFT_HAND_VERSION = 2
CUTTING_STEPS_VERSION = 3

# Fusion tool type -> CamCraft kind. Types not listed are skipped by name.
KINDS = {
    "flat end mill": "end_mill",
    "face mill": "end_mill",
    "ball end mill": "ball_nose",
    "bull nose end mill": "bull_nose",
    "chamfer mill": "taper",
    "counter sink": "taper",
    "drill": "drill",
    "spot drill": "drill",
    "center drill": "drill",
    "tap right hand": "tap",
    "tap left hand": "tap",
    "thread mill": "thread_mill",
    "boring bar": "bore",
}

DEFAULT_DRILL_ANGLE = 118.0


@dataclass
class Options:
    preset: str | None = None
    """Name of the Fusion preset to take rpm/feed from (default: the first)."""
    material: str | None = None
    """CamCraft workpiece material word the preset's numbers are for."""
    holder_d: float = 50.0
    """Holder diameter (mm) for tools without a holder in Fusion."""
    holder_len: float = 40.0
    """Holder length (mm) for tools without a holder in Fusion."""
    default_rpm: float | None = None
    default_feed: float | None = None
    """mm/min, used when a tool has no usable preset."""


@dataclass
class Skipped:
    name: str
    reason: str
    category: str | None = None
    """'tapered ball mill', 'roundover bit', ... for known unsupported cutters."""


@dataclass
class Conversion:
    tools: list[dict] = field(default_factory=list)
    """``{"name", "definition"}`` rows, ready for a ``.camtools`` file."""
    skipped: list[Skipped] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    version: int = VERSION
    sources: dict[str, dict] = field(default_factory=dict)
    """Tool name -> the Fusion entry it was converted from."""
    input_tools: int = 0
    """Fusion entries that are tools (holders not counted)."""


def unsupported_category(entry: dict) -> str | None:
    """Name the known cutter families CamCraft cannot represent."""
    ftype = str(entry.get("type", "")).strip().lower()
    g = entry.get("geometry") or {}
    dc, re_ = _num(g.get("DC")), _num(g.get("RE"))
    if ftype == "tapered mill":
        return "tapered ball mill" if re_ and re_ > 0 else "tapered mill"
    if ftype in ("ball end mill", "radius mill", "form mill") and dc and re_ and re_ > dc / 2 * 1.1:
        return "roundover bit"
    if ftype == "radius mill":
        return "roundover bit"
    return None


class ToolRefused(ValueError):
    pass


def convert(entries: list[dict], options: Options | None = None) -> Conversion:
    options = options or Options()
    if options.material is not None and options.material not in MATERIALS:
        raise ValueError(
            f"unknown material {options.material!r}; CamCraft knows: {', '.join(MATERIALS)}"
        )
    out = Conversion()
    taken: set[str] = set()
    for entry in entries:
        if entry.get("type") == "holder":
            continue
        out.input_tools += 1
        label = _label(entry)
        try:
            definition, warnings = convert_tool(entry, options)
        except ToolRefused as e:
            out.skipped.append(Skipped(label, str(e), unsupported_category(entry)))
            continue
        name = _unique_name(label, entry, taken)
        taken.add(name)
        out.sources[name] = entry
        out.warnings.extend(f"{name}: {w}" for w in warnings)
        out.tools.append({"name": name, "definition": definition})
        out.version = max(out.version, _version_for(definition))
    out.tools.sort(key=lambda t: t["name"])
    return out


def convert_tool(entry: dict, options: Options) -> tuple[dict, list[str]]:
    """One Fusion tool as a CamCraft definition, plus warnings.

    Raises ToolRefused when the tool cannot be represented.
    """
    warnings: list[str] = []
    ftype = str(entry.get("type", "")).strip().lower()
    kind = KINDS.get(ftype)
    if kind is None:
        raise ToolRefused(f"Fusion tool type '{ftype or '?'}' has no CamCraft counterpart yet")

    unit = _unit_scale(entry.get("unit"))
    g = entry.get("geometry") or {}

    def length(key: str, default: float | None = None) -> float | None:
        v = _num(g.get(key))
        return default if v is None else v * unit

    dc = length("DC")
    if dc is None or dc <= 0:
        raise ToolRefused("no cutting diameter (DC)")
    lcf = length("LCF")
    if lcf is None or lcf <= 0:
        raise ToolRefused("no flute length (LCF)")
    shank_d = length("SFDM") or dc
    body = length("LB") or length("OAL")
    if body is None:
        shank_len = 0.0
        warnings.append("no body length (LB/OAL): shank length set to 0")
    else:
        shank_len = max(body - lcf, 0.0)

    d: dict = {
        "kind": kind,
        "diameter": dc,
        "corner_r": 0.0,
        "tip_angle": 0.0,
        "pitch": 0.0,
        "nose_radius": 0.0,
        "width": 0.0,
        "flute_len": lcf,
        "shank_d": shank_d,
        "shank_len": shank_len,
    }

    re_ = length("RE", 0.0) or 0.0
    if ftype == "ball end mill":
        # Vendors round the radius of imperial balls (1/8" -> R1.5), so a
        # radius within 10 % of D/2 is still a ball.
        if re_ > 0 and abs(re_ - dc / 2) > 0.1 * dc / 2:
            if re_ < dc / 2:
                d["kind"] = "bull_nose"
                d["corner_r"] = re_
                warnings.append(f"ball end mill with corner radius {re_:g} mm < D/2 imported as bull nose")
            else:
                raise ToolRefused(f"corner radius {re_:g} mm exceeds half the {dc:g} mm diameter (form/roundover cutter)")
        else:
            d["corner_r"] = dc / 2
    elif kind == "bull_nose":
        d["corner_r"] = re_
    elif kind == "end_mill" and re_ > 0:
        d["kind"] = "bull_nose"
        d["corner_r"] = re_

    if kind == "taper":
        if ftype == "chamfer mill":
            ta = _num(g.get("TA"))
            angle = 2 * ta if ta else None
        else:
            angle = _num(g.get("SIG"))
        if not angle or angle <= 0:
            raise ToolRefused("no cone angle (TA/SIG)")
        d["tip_angle"] = angle
    elif kind == "drill":
        angle = _num(g.get("SIG"))
        if not angle:
            angle = DEFAULT_DRILL_ANGLE
            warnings.append(f"no point angle (SIG): {angle:g} degrees assumed")
        d["tip_angle"] = angle
    elif kind == "tap":
        pitch = length("TP")
        if not pitch or pitch <= 0:
            raise ToolRefused("tap has no thread pitch (TP)")
        d["pitch"] = pitch
        if ftype == "tap left hand":
            d["thread_hand"] = "left"
    elif kind == "thread_mill":
        pitch = length("TP")
        angle = _num(g.get("thread-profile-angle")) or 60.0
        if not pitch or pitch <= 0:
            raise ToolRefused("thread mill has no pitch (TP)")
        d["pitch"] = pitch
        d["tip_angle"] = angle

    d["holder_d"], d["holder_len"] = _holder(entry, options, warnings)

    rpm, feed, preset = _speeds(entry, options, unit, warnings)
    d["material_group"] = _material_group(preset, options)
    d["rpm"] = rpm
    d["feed"] = feed

    material = {"hss": "hss", "carbide": "carbide"}.get(str(entry.get("BMC", "")).strip().lower())
    if material:
        d["tool_material"] = material
    nof = _num(g.get("NOF"))
    if nof is not None and float(nof).is_integer() and 1 <= nof <= 32:
        d["flutes"] = int(nof)
    if ftype == "face mill":
        d["center_cutting"] = False

    number = _num((entry.get("post-process") or {}).get("number"))
    if number is not None and float(number).is_integer() and number > 0:
        d["number"] = int(number)

    for key, src, limit in (
        ("part_number", entry.get("product-id"), MAX_SHORT),
        ("vendor", entry.get("vendor"), MAX_SHORT),
        ("notes", entry.get("product-link"), MAX_NOTES),
    ):
        text = _clean_text(src, limit)
        if text:
            d[key] = text

    row = _cutting_row(preset, options, rpm, feed, unit)
    if row:
        d["cutting_data"] = [row]

    _round_numbers(d)
    why = why_not_sane(d)
    if why:
        raise ToolRefused(why)
    return d, warnings


def why_not_sane(d: dict) -> str | None:
    """The CamCraft sanity rules (tool_sanity.rs) for the kinds we write."""
    for k in ("diameter", "corner_r", "tip_angle", "pitch", "flute_len", "shank_d",
              "shank_len", "holder_d", "holder_len", "rpm", "feed"):
        v = d[k]
        if not math.isfinite(v):
            return f"{k} {v} is not a finite number"
    dia = d["diameter"]
    if not 0 < dia <= 500:
        return f"diameter {dia} mm is outside 0..500 mm"
    if d["corner_r"] < 0 or d["corner_r"] > dia / 2 + 1e-9:
        return f"corner radius {d['corner_r']} mm is outside 0..D/2"
    if d["tip_angle"] >= 180:
        return f"tip angle {d['tip_angle']} degrees must be under 180"
    for k in ("pitch", "shank_d", "shank_len", "holder_d", "holder_len"):
        if d[k] < 0:
            return f"{k} {d[k]} mm is negative"
    if d["flute_len"] <= 0:
        return "flute length must be over 0"
    if d["rpm"] <= 0:
        return f"spindle speed {d['rpm']} rpm must be over 0"
    if d["feed"] <= 0:
        return f"feed {d['feed']} mm/min must be over 0"
    kind, a, p = d["kind"], d["tip_angle"], d["pitch"]
    if kind == "drill" and a <= 60:
        return f"a drill's tip angle {a} degrees must be over 60"
    if kind == "tap" and p <= 0:
        return "a tap's thread pitch must be over 0"
    if kind == "taper" and a <= 0:
        return "a chamfer mill's cone angle must be over 0"
    if kind == "thread_mill":
        if not 0 < a < 180:
            return f"a thread mill's form angle {a} degrees must be over 0 and under 180"
        if p <= 0:
            return "a thread mill's pitch must be over 0"
        # thread_mill::EXTERNAL_DEPTH_PER_PITCH (ISO external depth 0.61343 P)
        if dia / 2 <= 0.61343 * p:
            return f"a thread mill of {dia} mm diameter leaves no core under a {p} mm pitch"
        if d["flute_len"] < p:
            return "a thread mill's cutting length is shorter than one pitch"
    for row in d.get("cutting_data", []):
        for k in ("stepdown", "stepover"):
            if k in row and not (math.isfinite(row[k]) and row[k] > 0):
                return f"cutting data {k} must be over 0"
    return None


# --- helpers ---------------------------------------------------------------


def _version_for(d: dict) -> int:
    v = VERSION
    if d.get("thread_hand") == "left":
        v = max(v, LEFT_HAND_VERSION)
    for row in d.get("cutting_data", []):
        if "stepdown" in row or "stepover" in row or (
            row["material"] == "unknown" and (row["rpm"], row["feed"]) != (d["rpm"], d["feed"])
        ):
            v = max(v, CUTTING_STEPS_VERSION)
    return v


def _unit_scale(unit) -> float:
    u = str(unit or "millimeters").strip().lower()
    if u in ("inches", "inch", "in"):
        return INCH
    return 1.0


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _label(entry: dict) -> str:
    desc = _clean_text(entry.get("description"), MAX_SHORT * 2)
    if desc:
        return desc
    pid = _clean_text(entry.get("product-id"), MAX_SHORT * 2)
    if pid:
        return pid
    g = entry.get("geometry") or {}
    dc = _num(g.get("DC"))
    unit = "in" if _unit_scale(entry.get("unit")) == INCH else "mm"
    size = f" D{dc:g}{unit}" if dc else ""
    number = _num((entry.get("post-process") or {}).get("number"))
    prefix = f"T{int(number)} " if number else ""
    return f"{prefix}{entry.get('type', 'tool')}{size}".strip()


def _unique_name(label: str, entry: dict, taken: set[str]) -> str:
    if label not in taken:
        return label
    number = _num((entry.get("post-process") or {}).get("number"))
    if number:
        candidate = f"{label} (T{int(number)})"
        if candidate not in taken:
            return candidate
    i = 2
    while f"{label} #{i}" in taken:
        i += 1
    return f"{label} #{i}"


def _clean_text(v, limit: int) -> str | None:
    if not isinstance(v, str):
        return None
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", v).strip()
    s = re.sub(r"\s{2,}", " ", s)
    if not s:
        return None
    return s[:limit].rstrip()


def _holder(entry: dict, options: Options, warnings: list[str]) -> tuple[float, float]:
    holder = entry.get("holder")
    segs = holder.get("segments") if isinstance(holder, dict) else None
    if segs:
        scale = _unit_scale(holder.get("unit") or entry.get("unit"))
        diameters = [
            _num(s.get(k)) or 0.0 for s in segs for k in ("lower-diameter", "upper-diameter")
        ]
        height = sum(_num(s.get("height")) or 0.0 for s in segs)
        if max(diameters) > 0 and height > 0:
            return max(diameters) * scale, height * scale
    warnings.append(
        f"no holder in Fusion: default holder {options.holder_d:g} x {options.holder_len:g} mm used"
    )
    return options.holder_d, options.holder_len


def _pick_preset(entry: dict, options: Options) -> dict | None:
    presets = [p for p in ((entry.get("start-values") or {}).get("presets") or []) if isinstance(p, dict)]
    if not presets:
        return None
    if options.preset:
        want = options.preset.strip().lower()
        for p in presets:
            if str(p.get("name", "")).strip().lower() == want:
                return p
    return presets[0]


def _speeds(entry: dict, options: Options, unit: float, warnings: list[str]):
    preset = _pick_preset(entry, options)
    rpm = _num(preset.get("n")) if preset else None
    feed = _num(preset.get("v_f")) if preset else None
    feed = feed * unit if feed is not None else None
    if preset and options.preset and str(preset.get("name", "")).strip().lower() != options.preset.strip().lower():
        warnings.append(f"no preset named '{options.preset}': used '{preset.get('name')}'")
    if not rpm or rpm <= 0:
        if options.default_rpm:
            rpm = options.default_rpm
            warnings.append(f"no spindle speed in Fusion: default {rpm:g} rpm used")
        else:
            raise ToolRefused("no spindle speed in Fusion (give --default-rpm)")
    if not feed or feed <= 0:
        if options.default_feed:
            feed = options.default_feed
            warnings.append(f"no cutting feed in Fusion: default {feed:g} mm/min used")
        else:
            raise ToolRefused("no cutting feed in Fusion (give --default-feed)")
    return rpm, feed, preset


def _material_group(preset: dict | None, options: Options) -> str:
    if options.material:
        return options.material
    material = (preset or {}).get("material")
    if isinstance(material, dict):
        query = _clean_text(material.get("query"), MAX_SHORT)
        if query:
            return query
    return ""


def _cutting_row(preset: dict | None, options: Options, rpm: float, feed: float, unit: float) -> dict | None:
    steps = {}
    if preset:
        for key in ("stepdown", "stepover"):
            v = _num(preset.get(key))
            if preset.get(f"use-{key}") and v and v > 0:
                steps[key] = v * unit
    if options.material:
        return {"material": options.material, "rpm": rpm, "feed": feed, **steps}
    if steps:
        return {"material": "unknown", "rpm": rpm, "feed": feed, **steps}
    return None


def _round_numbers(d: dict) -> None:
    """Drop float noise from unit conversion (0.125 in -> 3.175 mm)."""
    for k, v in d.items():
        if isinstance(v, float):
            d[k] = round(v, 6)
        elif k == "cutting_data":
            for row in v:
                for rk, rv in row.items():
                    if isinstance(rv, float):
                        row[rk] = round(rv, 6)
