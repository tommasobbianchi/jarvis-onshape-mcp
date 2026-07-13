"""Stepped-extrude builder for Onshape: counterbore / countersink style holes.

Ported from clarsbyte/onshape-mcp, rebuilt on Jarvis's SketchBuilder +
ExtrudeBuilder so it inherits unit parsing and the REMOVE/oppositeDirection
handling used everywhere else in this server, instead of clarsbyte's
hand-rolled BTMFeature dicts plus a string-substitution hack for chaining
sketch -> extrude feature ids.

Each step removes material to a given depth using a given circle radius;
radii are sorted largest-to-smallest so the resulting hole naturally reads
top (wide) -> bottom (narrow), i.e. a counterbore/countersink profile.

Feature-id sequencing: Onshape only assigns a sketch its featureId after
the server accepts it, so a stepped hole can't be built in one call. This
builder's `steps()` yields one `SketchBuilder` per step (already sorted);
the caller (server.py handler) POSTs each sketch, reads back the real
featureId, and only THEN constructs the matching `ExtrudeBuilder` for that
step. No client-side placeholder substitution — see create_stepped_extrude
in server.py for the two-pass loop.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ._units import parse_length
from .sketch import SketchBuilder, SketchPlane, LengthLike


@dataclass
class SteppedHoleStep:
    """One step of a stepped hole: a circle radius cut to a given depth."""

    index: int
    radius: LengthLike
    depth: LengthLike
    sketch: SketchBuilder


class SteppedExtrudeBuilder:
    """Builder for stepped (counterbore/countersink) holes with multiple diameters."""

    def __init__(
        self,
        name_prefix: str = "Counterbore",
        center: Tuple[LengthLike, LengthLike] = (0, 0),
        radii: Optional[List[LengthLike]] = None,
        depths: Optional[List[LengthLike]] = None,
        plane: SketchPlane = SketchPlane.TOP,
        plane_id: Optional[str] = None,
    ):
        """Initialize a stepped-hole builder.

        Args:
            name_prefix: Prefix for each step's sketch/extrude feature name.
            center: Center point `(x, y)` shared by every step's circle.
                Number = mm, or a string with explicit unit ("0.5 in").
            radii: Radius per step, any order (sorted internally, largest
                first). Needs >= 2 entries.
            depths: Cumulative cut depth per step, same length/order as
                `radii` (paired by index BEFORE sorting).
            plane: Sketch plane for every step's circle.
            plane_id: Deterministic plane ID (from PartStudioManager.get_plane_id).
        """
        self.name_prefix = name_prefix
        self.center = center
        self.radii = list(radii or [])
        self.depths = list(depths or [])
        self.plane = plane
        self.plane_id = plane_id

    def add_step(self, radius: LengthLike, depth: LengthLike) -> "SteppedExtrudeBuilder":
        """Append a step. Returns self for chaining."""
        self.radii.append(radius)
        self.depths.append(depth)
        return self

    def steps(self) -> List[SteppedHoleStep]:
        """Validate and return steps sorted largest-radius-first.

        Each step carries its own ready-to-POST `SketchBuilder` (single
        circle, using this builder's `plane`/`plane_id`/`center`). Raises
        `ValueError` on mismatched lengths or fewer than 2 steps.
        """
        if len(self.radii) != len(self.depths):
            raise ValueError(
                f"radii ({len(self.radii)}) and depths ({len(self.depths)}) "
                "must have the same length"
            )
        if len(self.radii) < 2:
            raise ValueError("at least 2 steps are required for a stepped hole")

        paired = sorted(
            zip(self.radii, self.depths),
            key=lambda rd: -parse_length(rd[0]).meters,
        )

        out: List[SteppedHoleStep] = []
        for i, (radius, depth) in enumerate(paired):
            sketch = SketchBuilder(
                name=f"{self.name_prefix} Sketch {i + 1}",
                plane=self.plane,
                plane_id=self.plane_id,
            )
            sketch.add_circle(center=self.center, radius=radius, is_construction=False)
            out.append(SteppedHoleStep(index=i, radius=radius, depth=depth, sketch=sketch))
        return out
