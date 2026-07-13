"""Unit tests for FS prelude retargeting and the new custom-feature param types."""

import pytest

from onshape_mcp.api.custom_features import (
    _to_onshape_parameter,
    retarget_fs_version,
)


class TestRetargetFsVersion:
    """Rewriting the FeatureScript prelude + std imports to a live version."""

    def test_rewrites_prelude_and_import(self):
        src = (
            'FeatureScript 2931;\n'
            'import(path : "onshape/std/geometry.fs", version : "2931.0");\n'
        )
        out = retarget_fs_version(src, "3008")

        assert "FeatureScript 3008;" in out
        assert 'version : "3008.0"' in out
        assert "2931" not in out

    def test_rewrites_every_import(self):
        src = (
            'FeatureScript 2909;\n'
            'import(path : "onshape/std/geometry.fs", version : "2909.0");\n'
            'import(path : "onshape/std/vector.fs", version : "2909.0");\n'
        )
        out = retarget_fs_version(src, "3008")

        assert out.count('version : "3008.0"') == 2
        assert "2909" not in out

    def test_tolerates_no_space_import_form(self):
        src = 'FeatureScript 2931;\nimport(path:"onshape/std/geometry.fs",version:"2931.0");\n'
        out = retarget_fs_version(src, "3008")

        assert "FeatureScript 3008;" in out
        assert '"3008.0"' in out

    def test_leaves_unrecognizable_source_alone(self):
        """A fragment with no prelude must pass through untouched, not get mangled."""
        src = "opPlane(context, id, {});"

        assert retarget_fs_version(src, "3008") == src

    def test_does_not_touch_version_like_numbers_in_body(self):
        """Only the prelude and import versions move; literals in code stay put."""
        src = (
            'FeatureScript 2931;\n'
            'import(path : "onshape/std/geometry.fs", version : "2931.0");\n'
            '// pitch 2931 is not a version\n'
            'const x = 2931;\n'
        )
        out = retarget_fs_version(src, "3008")

        assert "FeatureScript 3008;" in out
        assert "const x = 2931;" in out
        assert "// pitch 2931 is not a version" in out


class TestCustomFeatureParameters:
    """The parameter types that unlock geometry-picking FS features."""

    def test_query_from_single_deterministic_id(self):
        p = _to_onshape_parameter({"id": "seed", "type": "query", "value": "JLC"})

        assert p["btType"] == "BTMParameterQueryList-148"
        assert p["parameterId"] == "seed"
        assert p["queries"][0]["deterministicIds"] == ["JLC"]

    def test_query_from_list(self):
        p = _to_onshape_parameter({"id": "edges", "type": "query", "value": ["JLC", "JHA"]})

        assert p["queries"][0]["deterministicIds"] == ["JLC", "JHA"]

    def test_query_rejects_empty(self):
        with pytest.raises(ValueError, match="at least one deterministic ID"):
            _to_onshape_parameter({"id": "seed", "type": "query", "value": []})

    def test_enum_carries_name(self):
        p = _to_onshape_parameter({
            "id": "depthMode", "type": "enum",
            "value": "FULL_FACE", "enumName": "ThreadDepthMode",
        })

        assert p["btType"] == "BTMParameterEnum-145"
        assert p["value"] == "FULL_FACE"
        assert p["enumName"] == "ThreadDepthMode"
        # Namespace is stamped later by instantiate_custom_feature, which is the
        # only place that knows it.
        assert p["namespace"] == ""

    def test_integer_is_not_a_float(self):
        """isInteger(...) preconditions yield quantityType=INTEGER, which a
        float-shaped quantity does not satisfy."""
        p = _to_onshape_parameter({"id": "starts", "type": "integer", "value": 2})

        assert p["isInteger"] is True
        assert p["value"] == 2
        assert isinstance(p["value"], int)
        assert p["expression"] == "2"

    def test_unknown_type_still_raises(self):
        with pytest.raises(ValueError, match="unsupported parameter type"):
            _to_onshape_parameter({"id": "x", "type": "vector", "value": 1})
