"""Thread mills that vendors store as Fusion form mills (#37).

A ``form mill`` is a drawn outline (``geometry.profile``) without a pitch or a
row count. Harvey and Valor store their thread mills that way. The outline
states what CamCraft needs: rows of equal 60 degree teeth, their pitch, where
they end, and the neck and shank above them. A single tooth (Harvey's
single-form cutters) states a range of pitches by its height and its crest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import convert as c

FLANK = 30.0         # degrees off the radial direction: the 60 degree form
FLANK_TOL = 0.5
SAME = 1e-3          # mm: one crest, one diameter
PITCH_TOL = 1e-3     # relative: outline pitch against a standard one, and row to row
# ISO 261 / 724 metric pitches (mm) and the threads per inch of UN and NPS
METRIC = (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.75, 0.8, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4,
          4.5, 5, 5.5, 6)
PER_INCH = (4, 4.5, 5, 6, 7, 8, 9, 10, 11, 11.5, 12, 13, 14, 16, 18, 20, 24, 27, 28, 32, 36, 40, 44, 48, 56,
            64, 72, 80, 90, 100, 120)

Point = tuple[float, float]  # (z, r), mm


@dataclass
class ThreadForm:
    pitch: float
    pitch_min: float | None
    """The finest pitch of a single tooth, or None for rows at one pitch."""
    flute_len: float
    shaft: list[tuple[float, float, float]]
    """(height, lower, upper diameter) from the teeth to the body's end."""


def is_named(entry: dict) -> bool:
    """A form mill that its name calls a thread mill."""
    desc = str(entry.get("description", "")).lower()
    return (str(entry.get("type", "")).strip().lower() == "form mill"
            and ("thread mill" in desc or "threadmill" in desc))


def read(entry: dict, dc: float, lcf: float | None, body: float | None) -> ThreadForm:
    """The thread mill in a form mill's outline; ToolRefused if it is none we can write."""
    points = outline(entry)
    if len(points) < 4 or not lcf or body is None:
        raise c.ToolRefused("thread mill stored as a form mill without an outline")
    crests = _crests(points, lcf)
    if not crests:
        raise c.ToolRefused("thread mill stored as a form mill without teeth in its outline")
    if any(abs(r - dc / 2) > 2 * SAME for _, _, r in crests):
        raise c.ToolRefused("thread mill stored as a form mill whose teeth are not all on its diameter (a tapered thread?)")
    if len(crests) == 1:
        pitch, pitch_min = _pitch_range(points, crests[0])
    else:
        pitch, pitch_min = _pitch(crests), None
        _check_teeth(points, crests, pitch)
    # the end of the last tooth: the first point above its crest
    end = min(z for z, _ in points if z > crests[-1][1] + 1e-6)
    flute = max(lcf, end)
    shaft = _above(points, flute, body)
    if not shaft:
        raise c.ToolRefused("thread mill stored as a form mill: its outline does not reach its body length (LB)")
    return ThreadForm(pitch, pitch_min, flute, shaft)


def outline(entry: dict) -> list[Point]:
    """The outline from the tip up, as straight lines that are nowhere thinner."""
    scale = c._unit_scale(entry.get("unit"))
    points: list[Point] = []
    for seg in (entry.get("geometry") or {}).get("profile") or []:
        try:
            r, z = (float(v) * scale for v in seg["end"])
            center = tuple(float(v) * scale for v in seg["center"]) if seg.get("arc") else None
        except (KeyError, TypeError, ValueError):
            raise c.ToolRefused("form mill with an unreadable outline") from None
        if points and center:
            _check_arc(points[-1], (z, r), (center[1], center[0]), bool(seg.get("ccw")))
        if points and z < points[-1][0] - 1e-4:
            # the last line only closes the half section, down or along the axis
            if r > 1e-6:
                raise c.ToolRefused("form mill whose outline runs back on itself (check it in Fusion)")
            break
        points.append((z, r))
    return points


def _check_arc(a: Point, b: Point, center: Point, ccw: bool) -> None:
    """Refuse an arc that bulges past its chord: the chord would be thinner."""
    # Fusion draws (r, z): counterclockwise there puts the centre left of the way
    (az, ar), (bz, br) = a, b
    left = (br - ar) * (center[0] - az) - (bz - az) * (center[1] - ar) >= 0
    # left of the way up is toward the axis: a minor arc around a centre there
    # bulges outward, and a clockwise arc around it is the major one
    if left or ccw or bz < az:
        raise c.ToolRefused("form mill with a bulging arc in its outline")


def _crests(points: list[Point], lcf: float) -> list[tuple[float, float, float]]:
    """(start, end, radius) of every peak whose middle is within the flutes."""
    out, i = [], 0
    while i < len(points):
        j = i
        while j + 1 < len(points) and abs(points[j + 1][1] - points[i][1]) < SAME:
            j += 1
        r = points[i][1]
        under = i == 0 or points[i - 1][1] < r - SAME
        over = j + 1 < len(points) and points[j + 1][1] < r - SAME
        if under and over and i > 0 and (points[i][0] + points[j][0]) / 2 <= lcf:
            out.append((points[i][0], points[j][0], r))
        i = j + 1
    return out


def _pitch(crests: list[tuple[float, float, float]]) -> float:
    """The standard pitch the rows are at. CamCraft compares pitches to 1e-6 mm."""
    mid = [(a + b) / 2 for a, b, _ in crests]
    rows = len(mid) - 1
    mean = (mid[-1] - mid[0]) / rows
    # rounded to 1e-6 inch or so in Fusion: 1 um, or 0.1 %
    if any(abs(z1 - z0 - mean) > max(2e-3, mean * PITCH_TOL) for z0, z1 in zip(mid, mid[1:])):
        raise c.ToolRefused("thread mill stored as a form mill whose rows are not evenly spaced")
    best = min((*METRIC, *(c.INCH / n for n in PER_INCH)), key=lambda p: abs(p - mean))
    if abs(best - mean) > mean * PITCH_TOL:
        raise c.ToolRefused(f"thread mill stored as a form mill with a pitch of {mean:.4g} mm, which is no standard one")
    return float(best)


def _pitch_range(points: list[Point], crest: tuple[float, float, float]) -> tuple[float, float]:
    """Coarsest and finest pitch of a single tooth.

    CamCraft takes a tooth of 0.5413 x pitch at the coarsest pitch, and the
    flat crest of a standard thread is pitch / 8: a wider one cuts too wide.
    """
    start, end, r = crest
    below = [q for q in points if q[0] < start - 1e-9 or (q[0] <= start and q[1] < r - SAME)]
    above = [q for q in points if q[0] > end + 1e-9]
    if not below or not above:
        raise c.ToolRefused("thread mill stored as a form mill without a whole tooth")
    (z0, r0), (z1, r1) = below[-1], above[0]
    for dz, dr in ((start - z0, r - r0), (z1 - end, r - r1)):
        if abs(math.degrees(math.atan2(dz, dr)) - FLANK) > FLANK_TOL:
            raise c.ToolRefused("thread mill stored as a form mill whose teeth are not of the 60 degree form")
    height = min(r - r0, r - r1) + 2e-3  # 2 um of rounding, as for rows of teeth
    coarsest = math.floor(height / c.INTERNAL_DEPTH_PER_PITCH * 1e4) / 1e4
    finest = max(math.ceil((end - start) * 8 * 1e4 - 1e-6) / 1e4, METRIC[0])
    if finest > coarsest:
        raise c.ToolRefused(f"thread mill stored as a form mill whose tooth fits no pitch: its crest needs {finest:.4g} mm, "
                            f"its height allows {coarsest:.4g} mm")
    return coarsest, finest


def _check_teeth(points: list[Point], crests: list[tuple[float, float, float]], pitch: float) -> None:
    """60 degree flanks and the full tooth height CamCraft assumes."""
    for (_, lo_end, r0), (hi_start, _, r1) in zip(crests, crests[1:]):
        between = [q for q in points if lo_end - 1e-9 <= q[0] <= hi_start + 1e-9]
        for (z0, ra), (z1, rb) in zip(between, between[1:]):
            if abs(rb - ra) > 0.05 * pitch and abs(math.degrees(math.atan2(z1 - z0, abs(rb - ra))) - FLANK) > FLANK_TOL:
                raise c.ToolRefused("thread mill stored as a form mill whose teeth are not of the 60 degree form")
        root = min(r for _, r in between)
        if min(r0, r1) - root < c.INTERNAL_DEPTH_PER_PITCH * pitch - 2e-3:
            raise c.ToolRefused(f"thread mill stored as a form mill with {min(r0, r1) - root:.3g} mm high teeth: CamCraft "
                                f"takes them for {c.INTERNAL_DEPTH_PER_PITCH * pitch:.3g} mm, and would not see their root rub")


def _above(points: list[Point], start: float, body: float) -> list[tuple[float, float, float]]:
    """The outline from ``start`` to ``body`` as (height, lower, upper diameter)."""
    out: list[tuple[float, float, float]] = []
    for (z0, r0), (z1, r1) in zip(points, points[1:]):
        a, b = max(z0, start), min(z1, body)
        if z1 - z0 < 1e-9 or b - a < 1e-9:
            continue
        at = lambda z: 2 * (r0 + (r1 - r0) * (z - z0) / (z1 - z0))  # noqa: E731
        out.append((b - a, at(a), at(b)))
    return out if abs(sum(h for h, _, _ in out) - (body - start)) < 1e-6 and all(lo > 0 and up > 0 for _, lo, up in out) else []
