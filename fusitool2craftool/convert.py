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
TA        taper angle, half angle (chamfer mills); off the axis (dovetails)
TP        thread pitch (taps, thread mills)
TPN, TPX  finest / coarsest pitch (single-form thread mills)
NT        rows of teeth (thread mills)
shoulder-diameter   neck diameter (undercut cutters, single-form thread mills)
shoulder-length     reach from the tip to the shank (single-form thread mills)
chamfer-width, chamfer-angle   corner chamfer end mills
========  =============================================

A tool CamCraft would refuse is skipped here with the reason, instead of
written: CamCraft refuses a whole library over a single bad tool.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

INCH = 25.4

# CamCraft's workpiece material words (WorkMaterial::word in
# crates/tools/src/cutting.rs). A word CamCraft does not know refuses the
# whole library, so only released words are written by default.
RELEASED_MATERIALS = (  # CamCraft 0.26.0
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
UNRELEASED_MATERIALS: tuple[str, ...] = ()  # words on CamCraft's main only; --unreleased-materials
MATERIALS = RELEASED_MATERIALS + UNRELEASED_MATERIALS

# Fusion preset name -> CamCraft material, for --all-presets. Fusion's own
# preset "material" field is empty in nearly every library, so the name is
# the only lead. A name must match exactly one group; anything harder,
# ambiguous or unknown is left out and reported, never guessed.
PRESET_MATERIALS = (
    ("aluminium_6061", r"alumin|\balu\b|\b(6061|6082|7075|5083|2024)\b"),
    ("stainless_304", r"stainless|\binox\b|\b30[34]\b|\b316l?\b"),
    ("mild_steel", r"\bsteel\b|\bmild\b|low carbon|\b(1018|1020|1045|4140|4340|a36|s235|s355)\b"),
    ("grey_cast_iron", r"cast iron|\bgr[ae]y iron\b"),
    ("brass", r"\bbrass\b"),
    ("plastics_pom", r"\bpom\b|acetal|delrin|\bplastics?\b"),
    ("softwood", r"soft ?woods?\b|\bpine\b"),
    ("hardwood", r"hard ?woods?\b|\boak\b|\bmaple\b"),
    ("mdf", r"\bmdf\b"),
    ("plywood", r"\bply(wood)?\b"),
    ("acrylic", r"acrylic|\bpmma\b|plexi"),
    ("hdpe", r"\bhdpe\b|\buhmw|polyethylene"),
)
# Materials CamCraft has no group for, or hardened grades whose numbers
# would be wrong for the group the name also matches.
PRESET_EXCLUDE = (r"hrc\b|hardened|tool steel|\b(p20|d2|h13)\b|titan|inconel|copper|"
                  r"graphite|cfrp|carbon fib|composite")


def preset_material(name) -> str | None:
    """The CamCraft material a Fusion preset name is for, or None."""
    n = str(name or "").strip().lower()
    if not n or re.search(PRESET_EXCLUDE, n):
        return None
    hits = {m for m, pattern in PRESET_MATERIALS if re.search(pattern, n)}
    if "stainless_304" in hits:
        hits.discard("mild_steel")  # "stainless steel"
    return hits.pop() if len(hits) == 1 else None


# catalog_fields.rs limits.
MAX_SHORT = 80
MAX_NOTES = 500

# .camtools envelope versions (tool_library.rs).
VERSION = 1
LEFT_HAND_VERSION = 2
CUTTING_STEPS_VERSION = 3
TIP_FORM_VERSION = 6  # flat and ball taper tips; on CamCraft's main only (#10)

# Fusion tool type -> CamCraft kind. Types not listed are skipped by name.
KINDS = {
    "flat end mill": "end_mill",
    "face mill": "end_mill",
    "ball end mill": "ball_nose",
    "bull nose end mill": "bull_nose",
    "corner chamfer end mill": "bull_nose",  # inscribed corner radius (#18)
    "chamfer mill": "taper",
    "counter sink": "taper",
    "drill": "drill",
    "spot drill": "drill",
    "center drill": "drill",
    "tap right hand": "tap",
    "tap left hand": "tap",
    "thread mill": "thread_mill",
    "boring bar": "bore",
    # Undercut cutters (CamCraft >= 0.25.0, #1444): a head wider than
    # the neck that carries it. A slitting saw is a slot mill in Fusion.
    "slot mill": "t_slot",
    "lollipop mill": "lollipop",
    "dovetail mill": "dovetail",
    # Lathe tools (lathe.py, #2)
    "turning general": "turning",
    "turning boring": "bore",
    "turning grooving": "groove",
    "turning threading": "thread",
}

UNDERCUT = ("t_slot", "keyseat", "lollipop", "dovetail")

DEFAULT_DRILL_ANGLE = 118.0
BALL_TIP_FIT = 1e-3  # mm; a flat face narrower than this under a tapered mill's radius is a ball tip
BALL_TIP_GROWTH = 0.2  # a tapered ball's radius may grow this much to fit Fusion's cone
MIN_DRILL_ANGLE = 60.0  # CamCraft's drill kind wants a point over this (tool_sanity.rs)

# thread_mill.rs: radial depth of an internal / external 60 degree thread
# per mm of pitch.
INTERNAL_DEPTH_PER_PITCH = 0.5413
EXTERNAL_DEPTH_PER_PITCH = 0.61343


def is_flat_bottom(angle: float | None) -> bool:
    """A drill point angle of 180 degrees: a flat bottom drill (#17)."""
    return angle is not None and math.isclose(angle, 180.0, abs_tol=1e-6)


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
    default_holder: dict | None = None
    """Stand-alone Fusion holder for tools without one (#14); beats holder_d / holder_len."""
    holder_bodies: bool = True
    """Write a Fusion holder's segments as a drawn CamCraft holder body (#12)."""
    default_rpm: float | None = None
    default_feed: float | None = None
    """mm/min, used when a tool has no usable preset."""
    default_vc: float | None = None
    """Cutting speed, m/min: rpm = v_c x 1000 / (pi x D) when Fusion has none."""
    default_fz: float | None = None
    """Chip load, mm per tooth: feed = rpm x flutes x f_z when Fusion has none."""
    turning_d: float = 50.0
    """Work diameter (mm) a lathe tool's rpm is computed for from its surface speed."""
    max_rpm: float | None = None
    """Cap for an rpm computed from --default-vc (the spindle's maximum)."""
    kind_defaults: dict = field(default_factory=dict)
    """CamCraft kind -> {"rpm"|"feed"|"vc"|"fz": value}; beats the global defaults."""
    all_presets: bool = False
    """Also write one cutting data row per preset whose name names a material."""
    unreleased_materials: bool = False
    """Allow the material words CamCraft has after its latest release."""
    unreleased_tapers: bool = False
    """Write tapered mills (#10). CamCraft's latest release refuses the file."""

    def materials(self) -> tuple[str, ...]:
        return MATERIALS if self.unreleased_materials else RELEASED_MATERIALS


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
    holders: int = 0
    """Fusion holder entries (not converted: CamCraft imports no stand-alone holders)."""
    defaults: Counter = field(default_factory=Counter)
    """Tools whose rpm/feed came from a default: description -> tools."""
    options: Options | None = None
    """The options the library was converted with (the output check reads them)."""
    presets: Counter = field(default_factory=Counter)
    """--all-presets: (preset name, outcome) -> tools; outcome is the
    material word, "unmatched", "duplicate <material>" or "no speeds"."""


# Fusion types without a CamCraft kind, as the report names them. With KINDS
# and unsupported_category() this covers all 40 types of Autodesk's schema (#19).
FAMILIES = {
    "reamer": "reamer",  # NeoTech/Camcraft#897
    "counter bore": "counterbore",  # NeoTech/Camcraft#897
    "form mill": "form mill",  # CamCraft's Form kind is a lathe form gauge
    "circle segment barrel": "circle segment mill",
    "circle segment lens": "circle segment mill",
    "circle segment oval": "circle segment mill",
    "circle segment taper": "circle segment mill",
    "probe": "probe",
    "shaft": "holder part",
    "tool block": "holder part",
    "waterjet": "sheet cutting head",
    "laser cutter": "sheet cutting head",
    "plasma cutter": "sheet cutting head",
    "laser powder": "additive head",
    "laser wire": "additive head",
    "electric arc wire": "additive head",
}


def is_left_hand(entry: dict) -> bool:
    """A milling or drilling tool that cuts turning backwards (#38).

    A left-hand tap is one too, but CamCraft knows it (``thread_hand``).
    Lathe tools state their hand elsewhere.
    """
    ftype = str(entry.get("type", "")).strip().lower()
    return ((entry.get("geometry") or {}).get("HAND") is False
            and ftype != "tap left hand" and not ftype.startswith("turning "))


def unsupported_category(entry: dict) -> str | None:
    """Name the known cutter families CamCraft cannot represent."""
    ftype = str(entry.get("type", "")).strip().lower()
    g = entry.get("geometry") or {}
    dc, re_ = _num(g.get("DC")), _num(g.get("RE"))
    if ftype in KINDS and is_left_hand(entry):
        return "left-hand cutter"
    if ftype == "tapered mill":
        return "tapered ball mill" if re_ and re_ > 0 else "tapered mill"
    if (ftype in ("ball end mill", "bull nose end mill", "flat end mill", "radius mill", "form mill")
            and dc and re_ and re_ > dc / 2 * 1.1):
        return "roundover bit"
    if ftype == "radius mill":
        return "roundover bit"
    if ftype == "dovetail mill" and re_ and re_ > 0:
        return "corner radius dovetail mill"
    if ftype.startswith("turning "):
        from . import lathe
        return lathe.category(entry)
    return FAMILIES.get(ftype)


class ToolRefused(ValueError):
    pass


def convert(entries: list[dict], options: Options | None = None) -> Conversion:
    options = options or Options()
    if options.material is not None and options.material not in options.materials():
        if options.material in MATERIALS:
            raise ValueError(
                f"material {options.material!r} is newer than the latest CamCraft release, which refuses a "
                f"library that names it; pass --unreleased-materials for a newer CamCraft build"
            )
        raise ValueError(
            f"unknown material {options.material!r}; CamCraft knows: {', '.join(options.materials())}"
        )
    _check_defaults(options)
    out = Conversion(options=options)
    taken: set[str] = set()
    for entry in entries:
        if entry.get("type") == "holder":
            out.holders += 1
            continue
        out.input_tools += 1
        label = _label(entry)
        try:
            definition, warnings = convert_tool(entry, options, out.presets, out.defaults)
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


def convert_tool(entry: dict, options: Options, presets_report: Counter | None = None,
                 defaults_report: Counter | None = None) -> tuple[dict, list[str]]:
    """One Fusion tool as a CamCraft definition, plus warnings.

    Raises ToolRefused when the tool cannot be represented.
    """
    warnings: list[str] = []
    ftype = str(entry.get("type", "")).strip().lower()
    kind = KINDS.get(ftype)
    if ftype == "tapered mill":
        kind, ftype = _tapered_type(entry, options)
    if kind is None:
        raise ToolRefused(f"Fusion tool type '{ftype or '?'}' has no CamCraft counterpart yet")

    if is_left_hand(entry):
        raise ToolRefused("left-hand cutter (HAND): it needs a reversed spindle, and CamCraft would turn it forward")

    if ftype.startswith("turning "):
        return _convert_lathe(entry, options, warnings, presets_report, defaults_report)

    unit = _unit_scale(entry.get("unit"))
    g = entry.get("geometry") or {}

    def length(key: str, default: float | None = None) -> float | None:
        v = _num(g.get(key))
        return default if v is None else v * unit

    dc = length("DC")
    if dc is None or dc <= 0:
        raise ToolRefused("no cutting diameter (DC)")
    lcf = length("LCF")
    if kind == "thread_mill" and not lcf and _num(g.get("NT")) == 1:
        # One row of teeth, stored without a flute length (#16): the V's own
        # axial size is one pitch, the coarsest it cuts.
        kind = "thread_mill_single"
        lcf = length("TPX") or length("TP")
        if not lcf or lcf <= 0:
            raise ToolRefused("single-form thread mill has no pitch (TPX/TP)")
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
    elif kind in ("bull_nose", "end_mill") and re_ > 0:
        if re_ >= dc / 2 * (1 - 1e-6):
            # A corner radius of D/2 is a ball. Vendors round (a D24.9999
            # R12.5001 bull nose), so up to 10 % over D/2 is still one;
            # further over is a form/roundover cutter.
            if re_ > dc / 2 * 1.1:
                raise ToolRefused(f"corner radius {re_:g} mm exceeds half the {dc:g} mm diameter (form/roundover cutter)")
            d["kind"] = "ball_nose"
            d["corner_r"] = dc / 2
            if abs(re_ - dc / 2) > 1e-9:
                warnings.append(f"corner radius {re_:g} mm on a {dc:g} mm {ftype} imported as a ball nose")
        else:
            d["kind"] = "bull_nose"
            d["corner_r"] = re_
    elif kind == "bull_nose":
        d["corner_r"] = re_

    if ftype == "corner chamfer end mill":
        _corner_chamfer(d, g, length, warnings)

    # the shank under the holder that is wider than CamCraft's one shank
    # cylinder: (height, lower, upper diameter) from the neck up, in mm
    below: list[tuple[float, float, float]] = []
    if kind in UNDERCUT:
        below = _undercut(d, entry, length, body, warnings)

    if ftype == "tapered mill":
        _tapered_mill(d, _num(g["TA"]), length("RE") or 0.0, warnings)
    elif kind == "taper":
        if ftype == "chamfer mill":
            ta = _num(g.get("TA"))
            angle = 2 * ta if ta else None
        else:
            angle = _num(g.get("SIG"))
        if not angle or angle <= 0:
            raise ToolRefused("no cone angle (TA/SIG)")
        d["tip_angle"] = angle
        _warn_flat_tip(length, warnings)
    elif kind == "drill":
        angle = _num(g.get("SIG"))
        if not angle:
            angle = DEFAULT_DRILL_ANGLE
            warnings.append(f"no point angle (SIG): {angle:g} degrees assumed")
        if is_flat_bottom(angle):
            # CamCraft drills must have a point (tip angle under 180), and
            # its drilling cycles take only drill/tap/bore tools. A flat
            # bottom drill is a center-cutting end mill in shape: the
            # flat-floor ops (Pocket, Circular, milled Bore) plunge it.
            d["kind"] = "end_mill"
            warnings.append("flat bottom drill (point angle 180) imported as a center-cutting end mill: "
                            "CamCraft drills need a point, so drilling cycles won't offer it")
        elif round(angle, 6) <= MIN_DRILL_ANGLE:
            # A drill's point must be over 60 degrees; a chamfer mill is the
            # same sharp cone under the same cylinder (#31).
            d["kind"] = "taper"
            d["tip_angle"] = angle
            warnings.append(f"{angle:g} degree point imported as a chamfer mill: CamCraft drills need a point "
                            f"over {MIN_DRILL_ANGLE:g} degrees, so drilling cycles won't offer it")
            _warn_flat_tip(length, warnings)
        else:
            d["tip_angle"] = angle
            _warn_flat_tip(length, warnings)
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
    elif kind == "thread_mill_single":
        d["pitch"] = lcf
        d["pitch_min"] = length("TPN") or lcf
        d["tip_angle"] = _num(g.get("thread-profile-angle")) or 60.0

    if options.default_holder and not _has_holder(entry):
        entry = {**entry, "holder": options.default_holder}
        warnings.append(f"no holder in Fusion: default holder '{holder_name(options.default_holder)}' used")
    d["holder_d"], d["holder_len"] = _holder(entry, options, warnings)
    default_holder: tuple[float, float] | None = None
    if kind == "thread_mill_single":
        below = _single_form_neck(d, length, body, warnings)
    if below:
        # CamCraft stacks one shank cylinder and the holder: the shank is
        # the neck, so the real shank joins the holder above it.
        default_holder = (d["holder_len"], d["holder_d"])
        d["holder_len"] += sum(h for h, _, _ in below)
        d["holder_d"] = max([d["holder_d"]] + [v for _, lo, up in below for v in (lo, up)])

    nof = _num(g.get("NOF"))
    # teeth for --default-fz: Fusion's own count, also past CamCraft's 32-flute
    # field (a 72-tooth slitting saw still takes 72 x f_z per revolution)
    flutes = int(nof) if nof is not None and float(nof).is_integer() and nof >= 1 else None
    # a flat bottom or sharply pointed drill keeps Fusion's drill speeds (its feed is v_f_plunge)
    speed_kind = "drill" if kind == "drill" else d["kind"]
    # a tapered mill's DC is its tip: --default-vc holds at its widest
    speed_d = d["diameter"] if ftype == "tapered mill" else dc
    rpm, feed, preset = _speeds(entry, options, unit, warnings, speed_kind, d["pitch"], speed_d, flutes,
                                defaults_report if defaults_report is not None else Counter())
    d["material_group"] = _material_group(preset, options)
    d["rpm"] = rpm
    d["feed"] = feed

    nof = _num(g.get("NOF"))
    if nof is not None and float(nof).is_integer() and 1 <= nof <= 32:
        d["flutes"] = int(nof)
    if ftype == "face mill":
        d["center_cutting"] = False
    _catalog(d, entry)

    row = _cutting_row(preset, options, rpm, feed, unit)
    rows = [row] if row else []
    if options.all_presets:
        rows += _preset_rows(entry, preset, options, rows, speed_kind, d["pitch"], unit,
                             presets_report if presets_report is not None else Counter())
    if rows:
        d["cutting_data"] = rows

    _round_numbers(d)
    why = why_not_sane(d)
    if why:
        raise ToolRefused(why)
    if options.holder_bodies:
        body = holder_body(entry, d["flute_len"] + d["shank_len"], warnings, below, default_holder)
        if body:
            d["body"] = body
    return d, warnings


def _catalog(d: dict, entry: dict) -> None:
    """Edge material, Fusion tool number, part number, vendor and link."""
    material = {"hss": "hss", "carbide": "carbide"}.get(str(entry.get("BMC", "")).strip().lower())
    if material:
        d["tool_material"] = material
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


def _convert_lathe(entry: dict, options: Options, warnings: list[str], presets_report: Counter | None,
                   defaults_report: Counter | None) -> tuple[dict, list[str]]:
    from . import lathe

    d, preset = lathe.definition(entry, options, warnings, defaults_report if defaults_report is not None else Counter())
    d["material_group"] = _material_group(preset, options)
    _catalog(d, entry)
    if options.all_presets and presets_report is not None:
        # a material row holds an rpm, and a lathe preset has none
        for p in presets_of(entry):
            presets_report[(str(p.get("name", "")).strip() or "(unnamed)", "lathe tool")] += 1
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
    if "tip_form" in d:
        why = _tip_refusal(d)
        if why:
            return why
    if kind in ("thread_mill", "thread_mill_single"):
        if not 0 < a < 180:
            return f"a thread mill's form angle {a} degrees must be over 0 and under 180"
        if p <= 0:
            return "a thread mill's pitch must be over 0"
        # thread_mill::EXTERNAL_DEPTH_PER_PITCH (ISO external depth 0.61343 P)
        if dia / 2 <= EXTERNAL_DEPTH_PER_PITCH * p:
            return f"a thread mill of {dia} mm diameter leaves no core under a {p} mm pitch"
        if kind == "thread_mill" and d["flute_len"] < p:
            return "a thread mill's cutting length is shorter than one pitch"
        if kind == "thread_mill_single" and not 0 < d["pitch_min"] <= p:
            return f"a single-form thread mill's finest pitch {d['pitch_min']} mm must be over 0 and at most its {p} mm coarsest pitch"
    if kind in ("turning", "bore", "groove", "thread"):
        from . import lathe
        why = lathe.why_not_sane(d)
        if why:
            return why
    if kind in UNDERCUT:
        why = _undercut_refusal(d)
        if why:
            return why
    for row in d.get("cutting_data", []):
        for k in ("stepdown", "stepover"):
            if k in row and not (math.isfinite(row[k]) and row[k] > 0):
                return f"cutting data {k} must be over 0"
    return None


def _tip_refusal(d: dict) -> str | None:
    """CamCraft's rules for a flat or ball taper tip (crates/tools/src/taper.rs, on main)."""
    dia, cr, td, a = d["diameter"], d["corner_r"], d.get("tip_diameter", 0.0), d["tip_angle"]
    if d["kind"] != "taper" or d["tip_form"] not in ("flat", "ball"):
        return f"a {d['kind']} cannot have a {d['tip_form']} tip"
    if not 0 < a < 180:
        return f"a chamfer mill's cone angle {a} degrees must be over 0 and under 180"
    half = math.radians(a) / 2
    if d["tip_form"] == "flat":
        if not 0 < td < dia:
            return f"a flat tip's diameter {td} mm must be over 0 and under the {dia} mm diameter"
        flat = td / 2 - cr * (1 - math.sin(half)) / math.cos(half)
        if flat < 0:
            return f"the {cr} mm corner radius does not fit the {td} mm flat tip of a {a} degree cone"
    else:
        flat = 0.0
        if cr <= 0:
            return f"a ball tip's radius {cr} mm must be over 0"
        if td != 0:
            return f"a ball tip's diameter {td} mm must be 0"
    meets = flat + cr * math.cos(half)
    if meets >= dia / 2:
        return f"the {d['tip_form']} tip meets the cone {2 * meets:g} mm across, not under the {dia} mm diameter"
    tall = cr * (1 - math.sin(half))
    if tall >= d["flute_len"]:
        return f"the {d['tip_form']} tip is {tall:g} mm tall, not under the {d['flute_len']} mm flute length"
    return None


def lollipop_neck_meets(diameter: float, neck_d: float) -> float:
    """Height above the tip where a neck of ``neck_d`` leaves the ball."""
    r, sr = diameter / 2, neck_d / 2
    return r + math.sqrt(max(r * r - sr * sr, 0.0))


def _undercut_refusal(d: dict) -> str | None:
    """CamCraft's undercut cutter rules (crates/tools/src/undercut.rs)."""
    kind, dia, sd, fl, sl = d["kind"], d["diameter"], d["shank_d"], d["flute_len"], d["shank_len"]
    if not 0 < sd < dia:
        return f"the neck (shank diameter) {sd} mm must be over 0 and under the {dia} mm head diameter"
    if sl <= 0:
        return f"the neck length (shank length) {sl} mm must be over 0"
    if kind == "dovetail":
        a = d["tip_angle"]
        if not 0 < a < 90:
            return f"a dovetail cutter's flank angle {a} degrees must be over 0 and under 90"
        top = dia / 2 - fl / math.tan(math.radians(a))
        if top < sd / 2:
            return (f"a dovetail cutter's {a:g} degree flank narrows its {dia} mm head to {2 * top:.3f} mm "
                    f"at {fl} mm height, under its {sd} mm neck")
    if kind == "lollipop":
        r, meet = dia / 2, lollipop_neck_meets(dia, sd)
        if not r <= fl <= meet + 1e-9:
            return (f"a lollipop cutter's flute length {fl} mm must be from {r:g} mm (the ball's centre) "
                    f"to {meet:.3f} mm (where the {sd} mm neck leaves the ball)")
        if sl < dia - fl:
            return f"a lollipop cutter's neck length {sl} mm must reach the ball top ({dia - fl:g} mm)"
    return None


# --- helpers ---------------------------------------------------------------


def _warn_flat_tip(length, warnings: list[str]) -> None:
    """CamCraft's cones and drill points are sharp (NeoTech/Camcraft#895, #10)."""
    tip = length("tip-diameter")
    if tip and tip > 0:
        warnings.append(f"flat tip of {tip:g} mm written as a sharp point: near the tip the real cutter is "
                        f"wider than CamCraft's cone, check depths and clearances")


def _tapered_type(entry: dict, options: Options) -> tuple[str, str]:
    """(CamCraft kind, Fusion type to read it as) of a tapered mill (#10).

    Without a taper angle it is a straight cutter (some core box and bowl
    bits are stored that way), unless its name says it has a taper.
    """
    g = entry.get("geometry") or {}
    ta = _num(g.get("TA")) or 0.0
    if ta == 0:
        if "taper" in str(entry.get("description", "")).lower():
            raise ToolRefused("tapered mill without a taper angle (TA 0)")
        return ("bull_nose", "bull nose end mill") if _num(g.get("RE")) else ("end_mill", "flat end mill")
    if not 0 < ta < 90:
        raise ToolRefused(f"tapered mill's angle (TA) {ta:g} must be over 0 and under 90 degrees")
    if not options.unreleased_tapers:
        raise ToolRefused("tapered mill: its tip form needs a CamCraft newer than 0.26.0 (--unreleased-tapers)")
    return "taper", "tapered mill"


def _tapered_mill(d: dict, ta: float, re_: float, warnings: list[str]) -> None:
    """A tapered mill as CamCraft's taper with a flat or ball tip (#10).

    Fusion's ``DC`` is the cone's diameter extended down to the tip and
    ``TA`` its angle off the axis. CamCraft's ``diameter`` is the widest one,
    ``tip_angle`` the included angle, and ``tip_diameter`` Fusion's ``DC``.
    ``RE`` rounds the tip's corner: where it leaves no flat face it is a
    ball, tangent to the cone. Many balls have an ``RE`` of half of ``DC``,
    which leaves a sliver flat in Fusion's drawing. They are the ball that
    fits Fusion's cone: slightly larger than the real one, never thinner.
    """
    half, tip = math.radians(ta), d["diameter"]
    cone = re_ * (1 - math.sin(half)) / math.cos(half)  # the radius at the tip of a cone tangent to that ball
    flat = tip / 2 - cone
    d["tip_angle"] = 2 * ta
    d["corner_r"] = re_
    if re_ <= 0 or flat > BALL_TIP_FIT:
        fitting = tip / 2 * math.cos(half) / (1 - math.sin(half))  # the ball tangent to Fusion's cone
        if abs(re_ - tip / 2) <= 0.02 * tip / 2 and fitting <= re_ * (1 + BALL_TIP_GROWTH):
            d["tip_form"], d["corner_r"] = "ball", fitting
            warnings.append(f"tapered ball of {re_:g} mm radius written as {fitting:.4g} mm, the ball that fits "
                            f"Fusion's cone: the real tip is smaller, so it leaves higher scallops")
        else:
            d["tip_form"], d["tip_diameter"] = "flat", tip
    else:
        d["tip_form"] = "ball"
        if flat < -BALL_TIP_FIT:
            warnings.append(f"corner radius {re_:g} mm is too large for a {tip:g} mm tip: written as a ball, "
                            f"with a cone {-2 * flat:.3g} mm wider than Fusion's")
    d["diameter"] = 2 * (max(tip / 2, cone) + d["flute_len"] * math.tan(half))
    if d["diameter"] > d["shank_d"] + 0.02:
        warnings.append(f"the cone is {d['diameter']:.4g} mm wide at the top of its {d['flute_len']:g} mm flutes, "
                        f"over its {d['shank_d']:g} mm shank: check the flute length in Fusion")


def _corner_chamfer(d: dict, g: dict, length, warnings: list[str]) -> None:
    """A corner chamfer end mill as a bull nose (#18).

    CamCraft has no chamfered corner. The arc of a corner radius no larger
    than either leg of the chamfer lies outside the chamfer face, so the
    written cutter is never smaller than the real one (rest material is
    understated, nothing gouges) and closer to it than a sharp corner.

    Fusion's ``chamfer-width`` is radial. Whether ``chamfer-angle`` is off
    the axis or off the bottom face is not confirmed by a real tool yet, so
    the axial leg takes the smaller of both readings.
    """
    dc, dcx = length("DC"), length("DCX")
    if dcx and dcx > dc * (1 + 1e-6):
        raise ToolRefused(f"corner chamfer end mill cuts wider (DCX {dcx:g} mm) than its DC {dc:g} mm")
    width, angle = length("chamfer-width"), _num(g.get("chamfer-angle"))
    if not width or width <= 0 or not angle or not 0 < angle < 90:
        d["kind"], d["corner_r"] = "end_mill", 0.0
        warnings.append("corner chamfer without a usable chamfer-width/chamfer-angle imported as a sharp end mill")
        return
    if width >= dc / 2:
        raise ToolRefused(f"chamfer width {width:g} mm reaches the axis of the {dc:g} mm cutter (a pointed chamfer mill)")
    tan = math.tan(math.radians(angle))
    d["kind"], d["corner_r"] = "bull_nose", width * min(tan, 1 / tan)
    warnings.append(f"corner chamfer {width:g} mm x {angle:g} degrees imported as a bull nose with corner radius "
                    f"{d['corner_r']:.4g} mm, never smaller than the real cutter: rest material is understated")


def _single_form_neck(d: dict, length, body: float | None, warnings: list[str]) -> list[tuple[float, float, float]]:
    """Narrow ``d``'s shank to the neck of a single-form thread mill (#16).

    The neck runs from the tooth to Fusion's ``shoulder-length`` (from the
    tip). Returns the shank above it as (height, lower, upper diameter), or
    nothing when the tool has no reach below its shank: the shank then
    starts at the tooth.

    Without a ``shoulder-diameter`` the neck is the tooth root at the
    coarsest pitch. It can't be wider: the tool could not cut that thread
    over its reach.
    """
    shank_d, reach = d["shank_d"], length("shoulder-length")
    if body is None or not reach or not d["flute_len"] < reach < body:
        return []
    neck = length("shoulder-diameter")
    stated = bool(neck and neck > 0)
    if not stated:
        neck = d["diameter"] - 2 * INTERNAL_DEPTH_PER_PITCH * d["pitch"]
    if not 0 < neck < shank_d:
        return []
    if not stated:
        warnings.append(f"no neck diameter (shoulder-diameter): {neck:.4g} mm, the tooth root at the coarsest pitch")
    d["shank_d"], d["shank_len"] = neck, reach - d["flute_len"]
    return [(body - reach, shank_d, shank_d)]


def _shaft_above(entry: dict, length, body: float | None) -> list[tuple[float, float, float]]:
    """Fusion's shoulder, shaft segments and shank from the flute top to the body's end.

    As (height, lower, upper diameter) in mm, or nothing if Fusion does not
    state all of it. Vendors that print the neck length in the tool's name
    print the first shaft segment's height (#27).
    """
    lcf, shank = length("LCF"), length("SFDM")
    shoulder, shoulder_d = length("shoulder-length") or 0.0, length("shoulder-diameter")
    if body is None or not lcf or not shank:
        return []
    scale = _unit_scale(entry.get("unit"))
    out, z = [], lcf
    if shoulder > lcf + 1e-6:
        if not shoulder_d:
            return []
        out.append((shoulder - lcf, shoulder_d, shoulder_d))
        z = shoulder
    shaft = entry.get("shaft")
    for seg in (shaft.get("segments") if isinstance(shaft, dict) else None) or []:
        h, lo, up = (_num(seg.get(k)) for k in ("height", "lower-diameter", "upper-diameter"))
        if h is None or h < 0 or not lo or not up or min(lo, up) <= 0:
            return []
        out.append((h * scale, lo * scale, up * scale))
        z += h * scale
    if z > body + 1e-6:
        return []
    if body - z > 1e-6:
        out.append((body - z, shank, shank))
    return out


def _split_neck(shaft: list[tuple[float, float, float]]) -> tuple[float, float, list[tuple[float, float, float]]]:
    """The neck a shaft starts with, as (diameter, length), and the rest above it.

    The neck is a cylinder around the shaft as far as it stays that narrow.
    A shaft that widens right from the head has no neck: its first
    ``MIN_NECK`` is taken for one, at the width it has there.
    """
    neck, z = 0.0, 0.0
    for h, lo, up in shaft:
        if z >= MIN_NECK:
            break
        part = min(h, MIN_NECK - z)
        neck = max(neck, lo, lo + (up - lo) * part / h if h > 0 else up)
        z += h
    reach = 0.0
    for i, (h, lo, up) in enumerate(shaft):
        if max(lo, up) > neck + 1e-9:
            t = max(neck - lo, 0.0) / (up - lo) if lo <= neck + 1e-9 else 0.0
            head = (h * (1 - t), max(lo, neck) if t else lo, up)
            return neck, reach + h * t, [head, *shaft[i + 1:]]
        reach += h
    return neck, reach, []


def _undercut(d: dict, entry: dict, length, body: float | None,
              warnings: list[str]) -> list[tuple[float, float, float]]:
    """Head and neck of a slot mill / slitting saw or a lollipop.

    CamCraft reads ``flute_len`` as the head height (where the neck
    starts), ``shank_d`` as the neck diameter and ``shank_len`` as the neck
    length. So the neck takes the wider of Fusion's shaft and shoulder
    diameters, which can only shorten the reach CamCraft allows, never let
    the body pass where it would hit.

    Where that is as wide as the head, the neck is the narrow start of
    Fusion's shaft (#27). Returns the rest of the shaft, which then has to
    be drawn under the holder: CamCraft judges it there.
    """
    above: list[tuple[float, float, float]] = []
    necks = [v for v in (length("SFDM"), length("shoulder-diameter")) if v and v > 0]
    if not necks:
        raise ToolRefused("no neck diameter (SFDM / shoulder-diameter)")
    d["shank_d"] = max(necks)
    if d["shank_d"] >= d["diameter"]:
        shaft = _shaft_above(entry, length, body)
        neck, reach, rest = _split_neck(shaft)
        if rest and 0 < neck < d["diameter"]:
            d["shank_d"], body, above = neck, d["flute_len"] + reach, rest
    desc = str(entry.get("description", "")).lower()
    if d["kind"] == "t_slot" and ("keyseat" in desc or "woodruff" in desc):
        d["kind"] = "keyseat"
    if d["kind"] == "dovetail":
        # Fusion's dovetail angle is off the tool axis (a "14 degree" router
        # dovetail carries TA 14, a 60 degree machine dovetail TA 30);
        # CamCraft's tip_angle is off the bottom face.
        ta = _num((entry.get("geometry") or {}).get("TA"))
        if ta is None or not 0 < ta < 90:
            raise ToolRefused(f"dovetail taper angle (TA) {ta} must be over 0 and under 90 degrees")
        corner = length("RE") or 0.0
        if corner > 0:
            # Fusion's DC is the widest point, at the top of the radius; the
            # flank starts there. CamCraft's starts at the tip, so all of it
            # would be too thin: CamCraft would not see the real one gouge.
            a = math.radians(ta)
            thin = corner * ((1 + math.sin(a)) / math.cos(a) - 1)
            raise ToolRefused(f"corner radius {corner:.4g} mm: CamCraft's dovetail has a sharp corner, which puts "
                              f"its flank {thin:.3g} mm inside the real one")
        d["tip_angle"] = round(90.0 - ta, 6)
        if d["shank_d"] < d["diameter"]:
            # Fusion gives the shank, not the neck under the head: where the
            # flank narrows to that diameter the head is capped, so CamCraft
            # sees a shallower cutter and a body no narrower than the real one.
            r, sr = round(d["diameter"], 6) / 2, round(d["shank_d"], 6) / 2
            tan = math.tan(math.radians(d["tip_angle"]))
            top = math.floor((r - sr) * tan * 1e6) / 1e6
            if r - top / tan < sr:  # CamCraft's rule has no slack, and the limit itself can miss it by 1e-16
                top = round(top - 1e-6, 6)
            if d["flute_len"] > top:
                warnings.append(f"head height {d['flute_len']:g} mm capped at {top:g} mm, where the "
                                f"{ta:g} degree flank narrows to the {d['shank_d']:g} mm neck")
                d["flute_len"] = top
    if d["kind"] == "lollipop" and d["shank_d"] < d["diameter"]:
        # Above the point where the neck leaves the ball there is only
        # neck: a longer Fusion flute length is capped there, exactly.
        # rounded down: the numbers are written to 6 decimals, and CamCraft
        # allows this limit only 1e-9 of slack
        meet = math.floor(lollipop_neck_meets(d["diameter"], d["shank_d"]) * 1e6) / 1e6
        if d["flute_len"] > meet:
            warnings.append(f"flute length {d['flute_len']:g} mm capped at {meet:.4g} mm, "
                            f"where the {d['shank_d']:g} mm neck leaves the ball")
            d["flute_len"] = meet
    if body is not None:
        d["shank_len"] = max(body - d["flute_len"], 0.0)
        if d["kind"] in ("t_slot", "keyseat") and -1e-6 <= body - d["flute_len"] < MIN_NECK:
            # A bare blade (#31): the arbor is Fusion's holder, right on the
            # blade. CamCraft needs some neck under it.
            d["shank_len"] = MIN_NECK
            warnings.append("no shank above the blade (an arbor-mounted saw): the holder starts on the blade, so it "
                            "must be the arbor, and a screw or flange under the blade is not modelled")
    return above


def _version_for(d: dict) -> int:
    v = VERSION
    if d.get("thread_hand") == "left":
        v = max(v, LEFT_HAND_VERSION)
    if d.get("tip_form"):
        v = max(v, TIP_FORM_VERSION)
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


# CamCraft's drawn-profile rules (crates/tools/src/spun.rs, v0.25.0)
SPUN_MAX_POINTS = 64
SPUN_MIN_SEGMENT = 0.01      # mm; we merge below twice that, rounding-safe
MIN_NECK = 0.001             # mm; CamCraft needs a neck length over 0
SPUN_MAX_HEIGHT = 2000.0     # MAX_BODY_HEIGHT
SPUN_MAX_RADIUS = 500.0      # MAX_BODY_RADIUS
SPUN_SOURCE = "drawn profile"


def holder_outline(entry: dict, base: float, below: list[tuple[float, float, float]] | None = None,
                   default: tuple[float, float] | None = None) -> tuple[list[list[float]] | None, str]:
    """A Fusion holder's segments as CamCraft ``spun`` points ``[z, r, 0]``.

    Fusion lists the segments from the tool end up (height, lower and upper
    diameter). The outline starts on the axis at ``base`` (the shank's top,
    where CamCraft seats the holder), climbs through every segment and
    closes to the axis at the top: all straight lines, so CamCraft reads it
    exactly. Points closer than 0.02 mm merge keeping the larger radius, so
    the drawn holder is never thinner than Fusion's. ``below`` is a shank
    (height, lower, upper diameter) drawn under the holder, and ``default``
    the holder cylinder (height, diameter) above it when Fusion has none,
    all in mm. Returns the points, or None and why they cannot be drawn.
    """
    holder = entry.get("holder")
    segs = holder.get("segments") if isinstance(holder, dict) else None
    scale = _unit_scale(holder.get("unit") or entry.get("unit")) if segs else 1.0
    if not segs and default:
        segs = [_cylinder(*default)]
    if not segs:
        return None, "no holder segments"
    if below:
        segs = [{"height": h / scale, "lower-diameter": lo / scale, "upper-diameter": up / scale}
                for h, lo, up in below] + list(segs)
    z = base
    raw = [(z, 0.0)]
    for i, seg in enumerate(segs, 1):
        h = _num(seg.get("height"))
        lo = _num(seg.get("lower-diameter"))
        up = _num(seg.get("upper-diameter"))
        if h is None or lo is None or up is None or h < 0:
            return None, f"holder segment {i} has no usable height/diameters"
        if lo * scale / 2 < 2 * SPUN_MIN_SEGMENT or up * scale / 2 < 2 * SPUN_MIN_SEGMENT:
            return None, f"holder segment {i} narrows to the axis"
        raw.append((z, lo * scale / 2))
        z += h * scale
        raw.append((z, up * scale / 2))
    top = z
    if top - base <= 2 * SPUN_MIN_SEGMENT:
        return None, "holder has no height"
    if top > SPUN_MAX_HEIGHT or max(r for _, r in raw) > SPUN_MAX_RADIUS:
        return None, "holder is larger than CamCraft draws (2000 mm long, 500 mm radius)"
    mid: list[list[float]] = []
    for pz, pr in raw[1:]:
        if mid and math.hypot(pz - mid[-1][0], pr - mid[-1][1]) < 2 * SPUN_MIN_SEGMENT:
            mid[-1][1] = max(mid[-1][1], pr)
            continue
        if len(mid) >= 2 and _collinear(mid[-2], mid[-1], (pz, pr)):
            mid[-1] = [pz, pr]
            continue
        mid.append([pz, pr])
    if math.hypot(mid[0][0] - base, mid[0][1]) < 2 * SPUN_MIN_SEGMENT or \
            math.hypot(mid[-1][0] - top, mid[-1][1]) < 2 * SPUN_MIN_SEGMENT:
        return None, "holder narrows to the axis"
    points = [[base, 0.0]] + mid + [[top, 0.0]]
    if len(points) > SPUN_MAX_POINTS:
        return None, f"holder has {len(points)} outline points (CamCraft draws at most {SPUN_MAX_POINTS})"
    return [[round(pz, 6), round(pr, 6), 0.0] for pz, pr in points], ""


def _cylinder(height: float, diameter: float) -> dict:
    return {"height": height, "lower-diameter": diameter, "upper-diameter": diameter}


def _collinear(a, b, c) -> bool:
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) < 1e-9 and \
        (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) > 0


def holder_body(entry: dict, base: float, warnings: list[str],
                below: list[tuple[float, float, float]] | None = None,
                default: tuple[float, float] | None = None) -> dict | None:
    """The CamCraft ``body`` for a Fusion holder: a drawn ``spun`` outline.

    ``facets`` stays empty: every CamCraft that reads a .camtools body
    (0.20.0 on) also reads ``spun`` and then draws the body from it, never
    from the facets, so a 32-sided revolve per tool would only bloat the
    file. A holder that cannot be drawn keeps the cylinder (``holder_d`` x
    ``holder_len``), with a warning.
    """
    if not below and not (isinstance(entry.get("holder"), dict) and entry["holder"].get("segments")):
        return None
    points, why = holder_outline(entry, base, below, default)
    if points is None:
        warnings.append(f"holder kept as a cylinder: {why}")
        return None
    return {"source": SPUN_SOURCE, "facets": [], "spun": points}


def _has_holder(entry: dict) -> bool:
    """Does the tool carry a Fusion holder with a size (what _holder reads)?"""
    holder = entry.get("holder")
    segs = holder.get("segments") if isinstance(holder, dict) else None
    return bool(segs) and any((_num(s.get(k)) or 0.0) > 0 for s in segs for k in ("lower-diameter", "upper-diameter")) \
        and sum(_num(s.get("height")) or 0.0 for s in segs) > 0


def holder_name(holder: dict) -> str:
    return _clean_text(holder.get("description"), MAX_SHORT * 2) or _clean_text(holder.get("product-id"), MAX_SHORT) or ""


def find_holder(name: str, entries: list[dict]) -> dict:
    """The stand-alone Fusion holder called ``name`` (description or product id, any case).

    Raises ValueError when there is none, when two of that name differ in
    shape, or when CamCraft could not draw it.
    """
    holders = [e for e in entries if e.get("type") == "holder"]
    want = name.strip().lower()
    hits = [h for h in holders if holder_name(h).lower() == want] or [
        h for h in holders if (_clean_text(h.get("product-id"), MAX_SHORT) or "").lower() == want]
    if not want or not hits:
        names = sorted({holder_name(h) for h in holders} - {""})
        listed = "; holders there: " + ", ".join(f"'{n}'" for n in names) if names else " (no holders there)"
        raise ValueError(f"no stand-alone holder named '{name}'{listed}")
    shapes = [holder_outline({"holder": h}, 0.0) for h in hits]
    for points, why in shapes:
        if points is None:
            raise ValueError(f"holder '{name}' cannot be drawn in CamCraft: {why}")
    if any(points != shapes[0][0] for points, _ in shapes):
        raise ValueError(f"{len(hits)} holders named '{name}' have different shapes: rename one in Fusion, "
                         f"or name the library with --holders")
    # without a unit of its own it would be read in each tool's unit
    return hits[0] if hits[0].get("unit") else {**hits[0], "unit": "millimeters"}


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


def presets_of(entry: dict) -> list[dict]:
    """The tool's feed/speed presets.

    Current Fusion writes ``start-values: {"presets": [...]}``; older
    libraries (format version 1) hold one unnamed set as
    ``start-values: {"*": {...}}``, read here as a preset named "*".
    """
    sv = entry.get("start-values")
    if not isinstance(sv, dict):
        return []
    presets = [p for p in (sv.get("presets") or []) if isinstance(p, dict)]
    if not presets and isinstance(sv.get("*"), dict):
        presets = [{"name": "*", **sv["*"]}]
    return presets


def is_placeholder(preset: dict) -> bool:
    """Speeds a vendor left at 1 (#35): 1 rpm, and no feed other than 1.

    1 rpm with a real feed is not one: a drag engraver does not spin.
    """
    return _num(preset.get("n")) == 1 and all(
        _num(preset.get(k)) in (None, 0, 1) for k in ("v_f", "v_f_plunge"))


def _pick_preset(entry: dict, options: Options) -> dict | None:
    presets = presets_of(entry)
    if not presets:
        return None
    if options.preset:
        want = options.preset.strip().lower()
        for p in presets:
            if str(p.get("name", "")).strip().lower() == want:
                return p
    return next((p for p in presets if not is_placeholder(p)), presets[0])


def preset_feed(preset: dict, kind: str, pitch: float, unit: float) -> tuple[float | None, str]:
    """A preset's feed in mm/min and the Fusion field it came from.

    Mills carry their feed in ``v_f``. Fusion keeps a drill's feed in
    ``v_f_plunge`` or as feed per revolution ``f_n`` (and leaves ``v_f`` at
    0 or out), and a tap feeds one pitch per revolution - the feed
    CamCraft's tapping cycle posts anyway.
    """
    rpm = _num(preset.get("n"))
    vf = _num(preset.get("v_f"))
    if vf and vf > 0:
        return vf * unit, "v_f"
    if kind == "drill":
        plunge = _num(preset.get("v_f_plunge"))
        if plunge and plunge > 0:
            return plunge * unit, "v_f_plunge"
        fn = _num(preset.get("f_n"))
        if fn and fn > 0 and rpm and rpm > 0:
            return fn * rpm * unit, "f_n x n"
    if kind == "tap" and pitch > 0 and rpm and rpm > 0:
        return pitch * rpm, "n x pitch"
    return None, ""


DEFAULT_FIELDS = ("rpm", "feed", "vc", "fz")
DEFAULT_KINDS = tuple(sorted(set(KINDS.values()) | {"bull_nose", "keyseat", "thread_mill_single"}))


def _check_defaults(options: Options) -> None:
    for name in ("default_rpm", "default_feed", "default_vc", "default_fz", "max_rpm", "turning_d"):
        v = getattr(options, name)
        if v is not None and not (math.isfinite(v) and v > 0):
            raise ValueError(f"--{name.replace('_', '-')} must be over 0, not {v:g}")
    for kind, values in options.kind_defaults.items():
        if kind not in DEFAULT_KINDS:
            raise ValueError(f"unknown tool kind {kind!r} in a default; kinds: {', '.join(DEFAULT_KINDS)}")
        for f, v in values.items():
            if f not in DEFAULT_FIELDS or not (math.isfinite(v) and v > 0):
                raise ValueError(f"default {f} for {kind} must be over 0, not {v:g}")


def _default_levels(options: Options, kind: str):
    """(label, values) to try in order: the kind's own defaults, then the global ones."""
    yield f" {kind}=", options.kind_defaults.get(kind, {})
    yield " ", {"rpm": options.default_rpm, "feed": options.default_feed,
                "vc": options.default_vc, "fz": options.default_fz}


def default_rpm(options: Options, kind: str, diameter: float) -> tuple[float | None, str]:
    """The default spindle speed for a tool Fusion gives none, and how it was found."""
    for label, values in _default_levels(options, kind):
        if values.get("rpm"):
            return values["rpm"], f"--default-rpm{label}{values['rpm']:g}"
        if values.get("vc") and diameter > 0:
            rpm = values["vc"] * 1000 / (math.pi * diameter)
            how = f"--default-vc{label}{values['vc']:g} m/min on D{diameter:g}"
            if options.max_rpm and rpm > options.max_rpm:
                rpm, how = options.max_rpm, how + f", capped at --max-rpm {options.max_rpm:g}"
            return rpm, how
    return None, ""


def default_feed(options: Options, kind: str, rpm: float, flutes: int | None) -> tuple[float | None, str]:
    """The default feed (mm/min) for a tool Fusion gives none, and how it was found."""
    for label, values in _default_levels(options, kind):
        if values.get("feed"):
            return values["feed"], f"--default-feed{label}{values['feed']:g}"
        if values.get("fz") and flutes:
            return rpm * flutes * values["fz"], f"--default-fz{label}{values['fz']:g} x {flutes} flutes"
    return None, ""


def _how_key(how: str) -> str:
    """The option part of a default's description, for the run summary."""
    return re.split(r" on D| x \d|, capped", how)[0] + (", capped at --max-rpm" if "capped" in how else "")


def _speeds(entry: dict, options: Options, unit: float, warnings: list[str],
            kind: str = "end_mill", pitch: float = 0.0, diameter: float = 0.0,
            flutes: int | None = None, report: Counter | None = None):
    report = report if report is not None else Counter()
    preset = _pick_preset(entry, options)
    stated = preset is not None and not is_placeholder(preset)
    rpm = _num(preset.get("n")) if stated else None
    feed, source = preset_feed(preset, kind, pitch, unit) if stated else (None, "")
    none = "no" if stated or preset is None else "only a placeholder"
    if source and source != "v_f":
        warnings.append(f"feed {feed:g} mm/min taken from Fusion's {source} (the preset has no v_f)")
    if preset and options.preset and str(preset.get("name", "")).strip().lower() != options.preset.strip().lower():
        warnings.append(f"no preset named '{options.preset}': used '{preset.get('name')}'")
    if not rpm or rpm <= 0:
        rpm, how = default_rpm(options, kind, diameter)
        if rpm is None:
            raise ToolRefused(f"{none} spindle speed in Fusion (give --default-rpm or --default-vc)")
        warnings.append(f"{none} spindle speed in Fusion: {rpm:.6g} rpm from {how}")
        report[f"rpm from {_how_key(how)}"] += 1
    if not feed or feed <= 0:
        if kind == "tap" and pitch > 0:
            feed = rpm * pitch
            warnings.append(f"{none} feed in Fusion: tap feed {feed:g} mm/min = rpm x pitch")
        else:
            feed, how = default_feed(options, kind, rpm, flutes)
            if feed is None:
                why = " and no flute count for --default-fz" if not flutes and (
                    options.default_fz or options.kind_defaults.get(kind, {}).get("fz")) else ""
                raise ToolRefused(f"{none} cutting feed in Fusion{why} (give --default-feed or --default-fz)")
            warnings.append(f"{none} cutting feed in Fusion: {feed:.6g} mm/min from {how}")
            report[f"feed from {_how_key(how)}"] += 1
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


def _steps(preset: dict | None, unit: float) -> dict:
    steps = {}
    if preset:
        for key in ("stepdown", "stepover"):
            v = _num(preset.get(key))
            if preset.get(f"use-{key}") and v and v > 0:
                steps[key] = v * unit
    return steps


def _preset_rows(entry: dict, chosen: dict | None, options: Options, rows: list[dict],
                 kind: str, pitch: float, unit: float, report: Counter) -> list[dict]:
    """One cutting row per preset whose name names a CamCraft material.

    The first preset for a material wins; the tool's chosen preset already
    stands for --material when that is given.
    """
    taken = {r["material"] for r in rows}
    out = []
    for p in presets_of(entry):
        name = str(p.get("name", "")).strip() or "(unnamed)"
        if p is chosen and options.material:
            continue
        material = preset_material(name)
        if material is None:
            report[(name, "unmatched")] += 1
            continue
        if material not in options.materials():
            report[(name, f"{material} needs a CamCraft newer than the latest release (--unreleased-materials)")] += 1
            continue
        if material in taken:
            report[(name, f"duplicate {material}")] += 1
            continue
        rpm = _num(p.get("n"))
        feed, _ = preset_feed(p, kind, pitch, unit)
        if is_placeholder(p) or not (rpm and rpm > 0 and feed and feed > 0):
            report[(name, "no speeds")] += 1
            continue
        taken.add(material)
        report[(name, material)] += 1
        out.append({"material": material, "rpm": rpm, "feed": feed, **_steps(p, unit)})
    return out


def _cutting_row(preset: dict | None, options: Options, rpm: float, feed: float, unit: float) -> dict | None:
    steps = _steps(preset, unit)
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
        elif k == "turning":
            d[k] = {tk: round(tv, 6) for tk, tv in v.items()}
        elif k == "cutting_data":
            for row in v:
                for rk, rv in row.items():
                    if isinstance(rv, float):
                        row[rk] = round(rv, 6)
