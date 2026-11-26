"""Form mills and radius mills as CamCraft form mills (#11).

CamCraft 0.27.0 has a ``form_mill`` kind (NeoTech/Camcraft#3369): a cutter
whose edge is a drawn outline of ``[z, r, arc]`` points from the tip up.
``arc`` is the signed radius of the segment to the next point: positive
bulges away from the axis, negative toward it, 0 is a line. The outline
starts at ``(0, 0)``, never narrows going up (an undercut is
NeoTech/Camcraft#896), and its top and widest point are the tool's
``flute_len`` and ``diameter``.

A Fusion ``form mill`` states its outline as ``geometry.profile``: lines and
arcs as ``{"end": [r, z]}`` with ``"arc": true, "center": [r, z], "ccw"``.
A ``radius mill`` (a corner rounding end mill, a roundover bit) states only
numbers: a pilot of ``DC`` for ``tip-length``, then a concave quarter circle
of ``RE`` out to ``DC + 2 RE``, up to ``LCF``.

The outline up to ``LCF`` is the cutter. Above it the neck, shoulder and
shank go where they go for every other tool: CamCraft's shank cylinder and
the drawn holder body.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import convert as c

MAX_POINTS = 64      # spun.rs: points one outline may carry
MIN_SEGMENT = 1e-5   # mm; a shorter step is a duplicate point (meridian.rs refuses under 1e-6)
NARROWS = 1e-4       # mm; Fusion's rounding: an outline narrowing less than this is held at its width
ARC_STEPS = 16       # samples per arc for the undercut check
SHAFT_ARC_PARTS = 8  # a convex arc above the flutes is drawn as this many cylinders around it
SNAP = 1e-3          # mm; the flute length is cut at a point of the outline this close to it
LINE_ARC = 1e-3      # mm; an arc this short is written as its chord (it sags under 2e-5 mm)


@dataclass
class Segment:
    """One piece of a Fusion outline in (z, r) mm: a line, or an arc around ``center``."""
    a: tuple[float, float]
    b: tuple[float, float]
    center: tuple[float, float] | None = None
    ccw: bool = False

    def at(self, t: float) -> tuple[float, float]:
        """The point ``t`` (0..1) of the way along."""
        if self.center is None:
            return (self.a[0] + (self.b[0] - self.a[0]) * t, self.a[1] + (self.b[1] - self.a[1]) * t)
        th0, sweep, rad = self._polar()
        th = th0 + sweep * t
        # Fusion's plane is (r, z): x = r, y = z
        return (self.center[0] + rad * math.sin(th), self.center[1] + rad * math.cos(th))

    def _polar(self) -> tuple[float, float, float]:
        (az, ar), (bz, br), (cz, cr) = self.a, self.b, self.center
        th0, th1 = math.atan2(az - cz, ar - cr), math.atan2(bz - cz, br - cr)
        sweep = (th1 - th0) % (2 * math.pi) if self.ccw else -((th0 - th1) % (2 * math.pi))
        rad = (math.hypot(az - cz, ar - cr) + math.hypot(bz - cz, br - cr)) / 2
        return th0, sweep, rad

    @property
    def radius(self) -> float:
        return self._polar()[2]

    @property
    def sweep(self) -> float:
        return abs(self._polar()[1]) if self.center else 0.0

    def split(self, z: float) -> tuple[Segment, Segment]:
        """This segment cut where it crosses height ``z`` (it climbs, so once)."""
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if self.at(mid)[0] < z:
                lo = mid
            else:
                hi = mid
        p = (z, self.at(hi)[1])
        return Segment(self.a, p, self.center, self.ccw), Segment(p, self.b, self.center, self.ccw)

    def bulges_out(self) -> bool:
        """Whether the arc runs outside its chord (away from the axis)."""
        (z0, r0), (z1, r1), (zm, rm) = self.a, self.b, self.at(0.5)
        chord = r0 + (r1 - r0) * (zm - z0) / (z1 - z0) if abs(z1 - z0) > 1e-12 else max(r0, r1)
        return rm > chord + 1e-9

    def signed_arc(self) -> float:
        """CamCraft's ``arc``: the radius, positive where the arc bulges away from the axis."""
        if self.center is None:
            return 0.0
        (az, ar), (bz, br), (cz, cr) = self.a, self.b, self.center
        # the left normal of the chord going up points away from the axis:
        # a centre on that side bends the arc toward the axis (concave)
        mz, mr = (az + bz) / 2, (ar + br) / 2
        away = (cz - mz) * -(br - ar) + (cr - mr) * (bz - az)
        return -self.radius if away > 0 else self.radius


@dataclass
class Form:
    points: list[list[float]]
    """CamCraft's ``[z, r, arc]`` outline, from the tip up to the flute length."""
    shaft: list[tuple[float, float, float]] | None
    """(height, lower, upper diameter) from the flute length to the body's end; None for
    a radius mill, whose shoulder and shank are stated like an end mill's."""


def read(entry: dict, length, lcf: float, body: float | None, warnings: list[str]) -> Form:
    """A Fusion form or radius mill as CamCraft's outline; ToolRefused if it has none we can write."""
    if str(entry.get("type", "")).strip().lower() == "radius mill":
        cutter, _ = _cut(_radius_mill(entry, length, lcf, warnings), lcf)
        return Form(_points(cutter), None)
    segments = profile(entry)
    if not segments:
        raise c.ToolRefused("form mill without an outline (no profile in Fusion)")
    if body is None:
        raise c.ToolRefused("form mill without a body length (LB/OAL)")
    for end, what in ((lcf, "flute length (LCF)"), (body, "body length (LB)")):
        if segments[-1].b[0] < end - 1e-6:
            raise c.ToolRefused(f"form mill whose outline ends at {segments[-1].b[0]:.4g} mm, under its {end:.4g} mm {what}")
    cutter, above = _cut(segments, lcf)
    points = _points(cutter)
    return Form(points, _shaft(above, points[-1][0], body))


def profile(entry: dict) -> list[Segment]:
    """Fusion's ``profile`` from the tip up, to where it turns back to close the section."""
    scale = c._unit_scale(entry.get("unit"))
    out: list[Segment] = []
    last: tuple[float, float] | None = None
    for seg in (entry.get("geometry") or {}).get("profile") or []:
        try:
            r, z = (float(v) * scale for v in seg["end"])
            center = None
            if seg.get("arc"):
                cr, cz = (float(v) * scale for v in seg["center"])
                center = (cz, cr)
        except (KeyError, TypeError, ValueError):
            raise c.ToolRefused("form mill with an unreadable outline") from None
        if not all(math.isfinite(v) for v in (r, z, *(center or ()))):
            raise c.ToolRefused("form mill with an unreadable outline")
        if last is None:
            if abs(r) > 1e-6 or abs(z) > 1e-6:
                raise c.ToolRefused("form mill whose outline does not start on the axis at the tip")
            last = (0.0, 0.0)
            continue
        if z < last[0] - 1e-4 or (abs(z - last[0]) <= 1e-4 and r <= 1e-6 and out):
            # the last lines only close the half section: down, or in along the top
            if r > 1e-6:
                raise c.ToolRefused("form mill whose outline runs back down on itself (check it in Fusion)")
            break
        if math.hypot(z - last[0], r - last[1]) < MIN_SEGMENT:
            continue
        out.append(Segment(last, (z, r), center, bool(seg.get("ccw"))))
        last = (z, r)
    return out


def _radius_mill(entry: dict, length, lcf: float, warnings: list[str]) -> list[Segment]:
    """A pilot of DC, a concave quarter circle of RE out to DC + 2 RE, up to LCF."""
    dc, re_ = length("DC"), length("RE") or 0.0
    tip = length("tip-length") or 0.0
    if re_ <= 0:
        raise c.ToolRefused("radius mill without a corner radius (RE)")
    if tip < 0:
        raise c.ToolRefused(f"radius mill's tip length {tip:g} mm is negative")
    if tip <= MIN_SEGMENT:
        tip = 0.0
    if lcf < tip + re_ - 1e-6:
        raise c.ToolRefused(f"radius mill's flute length {lcf:.4g} mm ends inside its {re_:.4g} mm radius "
                            f"(tip length {tip:.4g} mm)")
    r0, top = dc / 2, dc / 2 + re_
    shoulder = length("shoulder-diameter")
    if shoulder and abs(shoulder / 2 - top) > 1e-3 and shoulder / 2 < top:
        warnings.append(f"radius mill: its {2 * top:.4g} mm head (DC + 2 x RE) is wider than its {shoulder:.4g} mm "
                        f"shoulder, as Fusion draws it: check DC and RE")
    pts = [(0.0, 0.0), (0.0, r0)]
    if tip:
        pts.append((tip, r0))
    out = [Segment(p, q) for p, q in zip(pts, pts[1:])]
    out.append(Segment((tip, r0), (tip + re_, top), center=(tip, top), ccw=False))
    if lcf > tip + re_ + MIN_SEGMENT:
        out.append(Segment((tip + re_, top), (lcf, top)))
    return out


def _cut(segments: list[Segment], lcf: float) -> tuple[list[Segment], list[Segment]]:
    """The outline split at the flute length: (the cutter, what is above it)."""
    cutter: list[Segment] = []
    above: list[Segment] = []
    for s in segments:
        if above:
            above.append(s)
        elif s.b[0] <= lcf + SNAP:
            if s.b[0] >= lcf - SNAP and s.b[1] < s.a[1] and abs(s.b[0] - s.a[0]) < 1e-9 and cutter:
                above.append(s)  # a step in at the flute length: the neck starts
            else:
                cutter.append(s)
        elif s.a[0] >= lcf - SNAP and cutter:
            above.append(s)
        else:
            lo, hi = s.split(lcf)
            cutter.append(lo)
            above.append(hi)
    return cutter, above


def _points(cutter: list[Segment]) -> list[list[float]]:
    """CamCraft's outline of the cutter; ToolRefused where it narrows going up."""
    if not cutter:
        raise c.ToolRefused("form mill without a cutting outline under its flute length")
    pts: list[list[float]] = [[0.0, 0.0, 0.0]]
    for s in cutter:
        if s.center is not None:
            if s.sweep > math.pi / 2 + 1e-6:
                raise c.ToolRefused("form mill with an arc of over 90 degrees in its outline (an undercut form, "
                                    "NeoTech/Camcraft#896)")
            samples = [s.at(i / ARC_STEPS) for i in range(ARC_STEPS + 1)]
        else:
            samples = [s.a, s.b]
        for (z0, r0), (z1, r1) in zip(samples, samples[1:]):
            if z1 < z0 - 1e-6:
                raise c.ToolRefused("form mill whose outline runs back down on itself (check it in Fusion)")
            if r1 < r0 - NARROWS:
                raise c.ToolRefused(f"form mill whose outline narrows going up at {z0:.4g} mm: an undercut form, "
                                    f"which CamCraft cannot write yet (NeoTech/Camcraft#896)")
        z, r = round(s.b[0], 6), round(max(s.b[1], pts[-1][1]), 6)
        if math.hypot(z - pts[-1][0], r - pts[-1][1]) < MIN_SEGMENT:
            continue
        if math.hypot(z - pts[-1][0], r - pts[-1][1]) >= LINE_ARC:
            pts[-1][2] = round(s.signed_arc(), 6)
        pts.append([z, r, 0.0])
    if len(pts) > MAX_POINTS:
        raise c.ToolRefused(f"form mill with {len(pts)} points in its outline, over CamCraft's {MAX_POINTS}")
    if len(pts) < 2:
        raise c.ToolRefused("form mill without a cutting outline under its flute length")
    why = refusal(pts)
    if why:
        raise c.ToolRefused(f"form mill: {why}")
    return pts


def _shaft(above: list[Segment], start: float, body: float) -> list[tuple[float, float, float]]:
    """The outline above the flutes up to the body's end, nowhere thinner than Fusion's."""
    out: list[tuple[float, float, float]] = []
    z = start
    for s in above:
        if z >= body - 1e-9:
            break
        if s.center is not None and s.bulges_out():
            # a convex arc: cylinders around it, each as wide as its part gets
            pts = [s.at(i / SHAFT_ARC_PARTS) for i in range(SHAFT_ARC_PARTS + 1)]
            pieces = [(p, q, max(p[1], q[1], *(s.at((i + t) / SHAFT_ARC_PARTS)[1] for t in (0.25, 0.5, 0.75))))
                      for i, (p, q) in enumerate(zip(pts, pts[1:]))]
            pieces = [(p, q, w, w) for p, q, w in pieces]
        else:
            pieces = [(s.a, s.b, s.a[1], s.b[1])]  # a line, or a concave arc inside its chord
        for (z0, _), (z1, _), r0, r1 in pieces:
            lo, hi = max(z0, z), min(z1, body)
            if hi - lo < 1e-9:
                continue
            at = (lambda q: r0 + (r1 - r0) * (q - z0) / (z1 - z0)) if z1 - z0 > 1e-12 else (lambda q: max(r0, r1))
            out.append((hi - lo, 2 * at(lo), 2 * at(hi)))
            z = hi
    if abs(z - body) > 1e-6 or not out or any(min(lo, up) <= 0 for _, lo, up in out):
        raise c.ToolRefused("form mill whose outline above the flutes does not reach its body length (LB)")
    return out


# --- CamCraft's rules (crates/tools/src/form_mill.rs, meridian.rs) ----------

def refusal(points: list[list[float]]) -> str | None:
    """Why CamCraft 0.27.0 would refuse this outline, or None."""
    if len(points) < 2:
        return "a form outline needs at least 2 points"
    if len(points) > MAX_POINTS:
        return f"a form outline has at most {MAX_POINTS} points, not {len(points)}"
    if points[0][0] != 0 or points[0][1] != 0:
        return "a form outline starts on the axis at the tip, at z 0 and r 0"
    for i, (a, b) in enumerate(zip(points, points[1:]), 1):
        if not all(math.isfinite(v) for v in (*a, *b)):
            return f"form outline point {i} is not a finite number"
        if a[1] < 0 or b[1] < 0:
            return f"form outline point {i} has a negative radius"
        dz, dr = b[0] - a[0], b[1] - a[1]
        chord = math.hypot(dz, dr)
        if chord < 1e-6:
            return f"the form outline segment from point {i} is shorter than 1e-06 mm"
        if dz < -1e-9:
            return f"the form outline segment from point {i} runs back down the tool axis"
        if a[2] and abs(a[2]) < chord / 2 - 1e-9:
            return f"the form outline arc from point {i} has a radius smaller than half its chord"
        if not _rises(a, b):
            return f"the form outline narrows upward from point {i}: an undercut form is not supported yet (#896)"
    return None


def arc_center(a: list[float], b: list[float]) -> tuple[float, float]:
    """The centre (z, r) of the minor arc from ``a`` with radius ``a[2]`` to ``b`` (the spun profile's law)."""
    dz, dr = b[0] - a[0], b[1] - a[1]
    chord = math.hypot(dz, dr)
    rad = abs(a[2])
    back = math.sqrt(max(rad * rad - chord * chord / 4, 0.0))
    s = math.copysign(1.0, a[2])
    return a[0] + dz / 2 + s * back * dr / chord, a[1] + dr / 2 - s * back * dz / chord


def _rises(a: list[float], b: list[float]) -> bool:
    """form_mill.rs ``rises``: the radius does not fall from ``a`` to ``b``."""
    if a[2] == 0:
        return b[1] >= a[1] - 1e-9
    cz, _ = arc_center(a, b)
    tol = 1e-7 * max(abs(a[2]), 1.0)
    return b[0] <= cz + tol if a[2] > 0 else a[0] >= cz - tol
