"""Unit tests for SteppedExtrudeBuilder (counterbore/countersink holes)."""

import pytest

from onshape_mcp.builders.sketch import SketchPlane
from onshape_mcp.builders.stepped_extrude import SteppedExtrudeBuilder, SteppedHoleStep


class TestSteppedExtrudeBuilder:
    """Test SteppedExtrudeBuilder validation, sorting, and sketch generation."""

    def test_initialization_with_defaults(self):
        """Test creating a builder with minimum parameters."""
        builder = SteppedExtrudeBuilder()

        assert builder.name_prefix == "Counterbore"
        assert builder.center == (0, 0)
        assert builder.radii == []
        assert builder.depths == []
        assert builder.plane == SketchPlane.TOP

    def test_add_step_chaining(self):
        """Test that add_step appends and returns self for chaining."""
        builder = SteppedExtrudeBuilder()
        result = builder.add_step(radius=5, depth=10).add_step(radius=3, depth=20)

        assert result is builder
        assert builder.radii == [5, 3]
        assert builder.depths == [10, 20]

    def test_steps_requires_matching_lengths(self):
        """Test that mismatched radii/depths lengths raise ValueError."""
        builder = SteppedExtrudeBuilder(radii=[5, 3], depths=[10])

        with pytest.raises(ValueError, match="same length"):
            builder.steps()

    def test_steps_requires_at_least_two(self):
        """Test that fewer than 2 steps raises ValueError."""
        builder = SteppedExtrudeBuilder(radii=[5], depths=[10])

        with pytest.raises(ValueError, match="at least 2 steps"):
            builder.steps()

    def test_steps_sorted_largest_radius_first(self):
        """Test that steps() sorts by radius descending regardless of input order."""
        builder = SteppedExtrudeBuilder(radii=[3, 8, 5], depths=[20, 5, 12])

        steps = builder.steps()

        assert [s.radius for s in steps] == [8, 5, 3]
        assert [s.depth for s in steps] == [5, 12, 20]

    def test_steps_returns_stepped_hole_step_objects(self):
        """Test that steps() returns SteppedHoleStep instances with sketches."""
        builder = SteppedExtrudeBuilder(
            name_prefix="Bolt Bore", center=(10, 10), radii=[5, 3], depths=[8, 20]
        )

        steps = builder.steps()

        assert len(steps) == 2
        for i, step in enumerate(steps):
            assert isinstance(step, SteppedHoleStep)
            assert step.index == i
            assert step.sketch.name == f"Bolt Bore Sketch {i + 1}"

    def test_steps_with_string_units(self):
        """Test that steps() handles string-unit radii/depths (e.g. inches)."""
        builder = SteppedExtrudeBuilder(radii=["0.5 in", "10 mm"], depths=["1 in", "5 mm"])

        steps = builder.steps()

        # 0.5 in (12.7mm) > 10mm, so it should sort first
        assert steps[0].radius == "0.5 in"
        assert steps[1].radius == "10 mm"

    def test_steps_sketch_has_single_circle(self):
        """Test that each step's sketch contains exactly one circle entity."""
        builder = SteppedExtrudeBuilder(radii=[5, 3], depths=[8, 20], plane_id="JCC")

        steps = builder.steps()

        for step in steps:
            feature = step.sketch.build(plane_id="JCC")
            entities = feature["feature"]["parameters"]
            # sanity: build() succeeds and yields a valid feature payload
            assert feature["feature"]["featureType"] == "newSketch"
            assert len(step.sketch.entities) == 1
