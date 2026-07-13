"""Paradigm-level tool surface: author a FeatureScript custom feature and
instantiate it in a Part Studio in one call.

STATUS 2026-04-16: unblocked by FS research round 2 (see
`scratchpad/fs-custom-feature-research-2.md`). Three fixes applied vs the
earlier wip:

1. FS version bumped from stale 2242 to current 2909 (667 versions of
   drift was silently compiling every upload to an empty symbol table).
2. Namespace format corrected to `e{fs_eid}::m{microversion}` — letter
   prefixes glued directly to ids, no `::` between prefix and id.
3. Microversion fetched from `/featurespecs` after upload (the
   `sourceMicroversionId` field on each entry) instead of guessing.

The inline "paste a FS snippet and run it" path does not exist in
Onshape's public API. `/partstudios/.../featurescript` is read-only eval;
mutating the feature tree requires a BTMFeature-134 that references an
exported function defined in a Feature Studio element via
`{featureType, namespace}`.

`CustomFeatureManager.apply_featurescript_feature` orchestrates the
4-step dance behind a single call:

    1. Create a fresh Feature Studio element in the same workspace.
    2. POST the FS source as a BTFeatureStudioContents-2239 body.
    3. GET `/featurespecs` to confirm the FS compiled (featureSpecs
       non-empty, libraryVersion non-zero) and read `sourceMicroversionId`.
    4. POST a BTMFeature-134 into the Part Studio with
       `namespace="e{fs_eid}::m{microversion}"`. Routed through
       `apply_feature_and_check` for regen status.

References:
    scratchpad/fs-custom-feature-research-2.md (canonical flow + namespace)
    https://forum.onshape.com/discussion/26720/
    https://github.com/javawizard/onshape-std-library-mirror (FS version)
    https://github.com/onshape-public/go-client (payload shapes)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from loguru import logger

from .client import OnshapeClient
from .feature_apply import FeatureApplyResult, apply_feature_and_check
from .fs_notices import extract_fs_body, fetch_body_notices, format_notices


# FALLBACK ONLY. The live version is resolved at upload time by
# `resolve_fs_version()` and stamped into the source by `retarget_fs_version()`,
# so callers no longer maintain their prelude by hand.
#
# Measured 2026-07-13 (live std = 3008), so we stop guessing about this:
#   - Onshape IS backwards compatible. A source pinned to 2931 or 2909 compiles
#     fine. Version drift is NOT what produces an empty feature spec -- a
#     SYNTAX error is. Do not misread one as the other; that mistake cost a
#     long bisect once already.
#   - There is still a floor: pinning 2500 compiles to nothing. And an old
#     prelude keeps you on old std semantics and accumulating deprecations.
# So retargeting is hygiene, not a bug fix: it keeps every recipe on the current
# std library without anyone remembering to bump a number.
DEFAULT_FS_VERSION = "2931"

# Matches `FeatureScript 2931;` and `version : "2931.0"` / `version:"2931.0"`.
_FS_PRELUDE_RE = re.compile(r"(FeatureScript\s+)(\d+)(\s*;)")
_FS_IMPORT_RE = re.compile(r'(version\s*:\s*")(\d+)(\.\d+")')

# Onshape's public standard library document. Latest version entry = current
# FS library version. See `discover_fs_version()`.
_ONSHAPE_STD_DID = "12312312345abcabcabcdeff"


_VALID_FEATURE_TYPE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class CustomFeatureManager:
    """Create + apply ad-hoc FeatureScript custom features in a Part Studio."""

    def __init__(self, client: OnshapeClient):
        self.client = client
        self._fs_version: Optional[str] = None

    # ---- FS version discovery --------------------------------------------

    async def discover_fs_version(self) -> str:
        """Query the live FS library version from Onshape's public std doc.

        Returns the integer version string (e.g. "2909"). Useful when you
        suspect DEFAULT_FS_VERSION is stale; pin the result and reuse. Does
        NOT cache — callers should cache themselves if they care.
        """
        versions = await self.client.get(
            f"/api/v9/documents/d/{_ONSHAPE_STD_DID}/versions"
        )
        if not isinstance(versions, list) or not versions:
            raise RuntimeError(
                f"std versions returned unexpected shape: {versions!r}"
            )
        # First entry is typically the "Start" placeholder; skip it. Last
        # entry has the most recent "<n>.0" name.
        for entry in reversed(versions):
            name = (entry or {}).get("name", "")
            if name and name != "Start":
                # name is like "2909.0"; split off the .0
                left = name.split(".")[0].strip()
                if left.isdigit():
                    return left
        raise RuntimeError(
            f"could not parse FS version from std versions list: {versions!r}"
        )

    async def resolve_fs_version(self) -> str:
        """Live FS library version, cached for the life of this manager.

        Wraps `discover_fs_version()` with a cache and a fallback, so it is
        safe to call on every upload. Never raises: a failed lookup degrades
        to DEFAULT_FS_VERSION rather than blocking the user's feature.
        """
        if self._fs_version is None:
            try:
                self._fs_version = await self.discover_fs_version()
                logger.debug("resolved live FS version: {}", self._fs_version)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "FS version lookup failed ({}); falling back to {}",
                    e, DEFAULT_FS_VERSION,
                )
                self._fs_version = DEFAULT_FS_VERSION
        return self._fs_version

    # ---- Feature Studio element lifecycle ---------------------------------

    async def create_feature_studio(
        self, document_id: str, workspace_id: str, name: str
    ) -> str:
        """Create a new Feature Studio element in a document/workspace.

        Returns the new element's id.
        """
        path = f"/api/v9/featurestudios/d/{document_id}/w/{workspace_id}"
        response = await self.client.post(path, data={"name": name})
        element_id = response.get("id")
        if not element_id:
            raise RuntimeError(
                f"Feature Studio creation returned no id: {response!r}"
            )
        return element_id

    async def upload_fs_source(
        self,
        document_id: str,
        workspace_id: str,
        fs_element_id: str,
        contents: str,
    ) -> Dict[str, Any]:
        """Write FS source into an existing Feature Studio element.

        Minimal body per robot-education/robot-code precedent: just
        `{"contents": <source>}`. Onshape assigns the btType and
        microversionId server-side. Response carries the assigned
        microversionId on success.
        """
        path = f"/api/v9/featurestudios/d/{document_id}/w/{workspace_id}/e/{fs_element_id}"
        body = {"contents": contents}
        return await self.client.post(path, data=body)

    async def get_featurespecs(
        self,
        document_id: str,
        workspace_id: str,
        fs_element_id: str,
    ) -> Dict[str, Any]:
        """Read the compiled feature specs from a Feature Studio.

        `featureSpecs[]` is empty until the uploaded source compiles
        successfully — this is how we verify a `POST /contents` actually
        registered the exported symbols. Each spec entry carries a
        `sourceMicroversionId` we need for the instantiation namespace.
        """
        path = (
            f"/api/v9/featurestudios/d/{document_id}/w/{workspace_id}"
            f"/e/{fs_element_id}/featurespecs"
        )
        return await self.client.get(path)

    # ---- Instantiation ---------------------------------------------------

    async def instantiate_custom_feature(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_element_id: str,
        *,
        fs_element_id: str,
        source_microversion_id: str,
        feature_type: str,
        feature_name: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
    ) -> FeatureApplyResult:
        """POST a BTMFeature-134 that invokes `feature_type` from the given
        Feature Studio. Routed through `apply_feature_and_check` so regen
        status comes back cleanly.

        `parameters` is a list of `{id, type, value}` dicts — we convert each
        to the BTMParameterQuantity / BTMParameterString / BTMParameterBoolean
        shape that BTMFeature-134 expects.
        """
        if not _VALID_FEATURE_TYPE_RE.fullmatch(feature_type):
            raise ValueError(
                f"feature_type must be a valid FS identifier, got {feature_type!r}"
            )
        if not source_microversion_id:
            raise ValueError(
                "source_microversion_id is required. Get it from "
                "get_featurespecs(...) -- the field on each BTFeatureSpec-129 "
                "entry. Empty featurespecs means the FS didn't compile."
            )

        namespace = _build_namespace(fs_element_id, source_microversion_id)
        onshape_params = [
            _to_onshape_parameter(p) for p in (parameters or [])
        ]
        # An enum declared inside the caller's Feature Studio is not resolvable
        # from a bare name -- Onshape rejects it with "Parameter <x> ... does not
        # match its feature spec". It has to carry the SAME namespace as the
        # feature that owns it, so stamp it on here rather than making callers
        # pass a value they'd have to reconstruct.
        for p in onshape_params:
            if p.get("btType") == "BTMParameterEnum-145" and not p.get("namespace"):
                p["namespace"] = namespace

        payload = {
            "btType": "BTFeatureDefinitionCall-1406",
            "feature": {
                "btType": "BTMFeature-134",
                "featureType": feature_type,
                "name": feature_name,
                "namespace": namespace,
                "suppressed": False,
                "parameters": onshape_params,
            },
        }

        logger.debug(
            "instantiate_custom_feature featureType={} namespace={!r} "
            "param_count={}",
            feature_type,
            namespace,
            len(onshape_params),
        )

        return await apply_feature_and_check(
            self.client,
            document_id,
            workspace_id,
            part_studio_element_id,
            payload,
            operation="create",
        )

    # ---- One-call convenience -------------------------------------------

    async def apply_featurescript_feature(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_element_id: str,
        *,
        feature_type: str,
        feature_script: str,
        feature_name: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
        fs_element_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """End-to-end: create a fresh FS element, write the source, and
        instantiate the feature. Returns a dict carrying both the regen
        FeatureApplyResult and the FS element id (so callers can inspect
        the uploaded source if debugging).

        `feature_script` must be a complete FS source file — it should start
        with `FeatureScript <version>;` and export a `defineFeature(...)` with
        a top-level name equal to `feature_type`. The worked example in the
        tool description shows the minimum boilerplate.
        """
        fs_name = fs_element_name or f"ClaudeFS_{feature_type}"

        # Stamp the LIVE std-library version into the source. Whatever the
        # caller pinned is treated as a hint, not a contract, so recipes stay on
        # current std semantics without anyone maintaining a literal.
        fs_version = await self.resolve_fs_version()
        feature_script = retarget_fs_version(feature_script, fs_version)

        fs_eid = await self.create_feature_studio(
            document_id, workspace_id, fs_name
        )
        upload_resp = await self.upload_fs_source(
            document_id, workspace_id, fs_eid, feature_script
        )

        # Verify the FS compiled by polling /featurespecs. Onshape's compile
        # is synchronous with POST /contents, so one GET is enough — empty
        # featureSpecs means a SYNTAX error (the prelude was just retargeted,
        # and old preludes compile fine anyway).
        specs = await self.get_featurespecs(document_id, workspace_id, fs_eid)
        feature_specs = specs.get("featureSpecs") or []
        if not feature_specs:
            raise RuntimeError(
                f"Feature Studio {fs_eid} compiled to an empty feature spec. "
                f"The source was retargeted to the live FS version "
                f"({fs_version}), so this is a SYNTAX error, not version drift. "
                f"Onshape surfaces no diagnostic here -- known parse traps: no "
                f"if-EXPRESSION (`var x = if (c) a else b;`), no uninitialized "
                f"declarations (`var x is T;`), and a Query parameter requires a "
                f'"Filter" annotation. libraryVersion='
                f"{specs.get('libraryVersion')!r}. Uploaded source preview: "
                f"{feature_script[:200]!r}"
            )
        # Pick the spec whose exported `featureType` matches the caller's,
        # else fall back to the first. BTFeatureSpec-129 keys live at the
        # top level (featureType, sourceMicroversionId) — older probes
        # expected a nested `message` dict but live responses put them
        # top-level.
        target_spec = next(
            (s for s in feature_specs if s.get("featureType") == feature_type),
            feature_specs[0],
        )
        source_microversion = (
            target_spec.get("sourceMicroversionId")
            or upload_resp.get("sourceMicroversion")
            or upload_resp.get("microversionId")
        )
        if not source_microversion:
            raise RuntimeError(
                f"Could not extract sourceMicroversionId from featurespecs. "
                f"Spec entry keys: {list(msg.keys())}; upload_resp keys: "
                f"{list(upload_resp.keys())}"
            )

        apply_result = await self.instantiate_custom_feature(
            document_id,
            workspace_id,
            part_studio_element_id,
            fs_element_id=fs_eid,
            source_microversion_id=source_microversion,
            feature_type=feature_type,
            feature_name=feature_name,
            parameters=parameters,
        )

        # FS-error enrichment: when the feature compiled but failed at REGEN,
        # `apply_result.error_message` only carries the opaque enum
        # ("REGEN_ERROR (ERROR)"). Re-evaluate the body of the user's
        # defineFeature inline via /featurescript -- that endpoint streams
        # `notices[]` carrying the actual diagnostic ("Function opThisDoesNotExist
        # with 3 argument(s) not found"). Best-effort; a failed enrichment
        # leaves the original error_message untouched. See fs_notices.py and
        # scratchpad/fs-failure-evidence.md.
        if not apply_result.ok:
            try:
                body = extract_fs_body(feature_script)
                if body:
                    notices = await fetch_body_notices(
                        self.client,
                        document_id,
                        workspace_id,
                        part_studio_element_id,
                        body,
                    )
                    rendered = format_notices(notices)
                    if rendered:
                        base = apply_result.error_message or ""
                        apply_result.error_message = (
                            f"{base}\nFS NOTICES:\n{rendered}".lstrip()
                        )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"FS body re-eval enrichment failed: {e}")

        return {
            "apply_result": apply_result,
            "fs_element_id": fs_eid,
            "source_microversion_id": source_microversion,
            "fs_library_version": specs.get("libraryVersion"),
        }


# ---- helpers ---------------------------------------------------------------


def retarget_fs_version(source: str, version: str) -> str:
    """Rewrite an FS source's prelude + std imports to `version`.

    `FeatureScript 2931;` -> `FeatureScript <version>;`
    `version : "2931.0"`  -> `version : "<version>.0"`

    Onshape accepts older preludes (2909 and 2931 both still compile against a
    3008 std), so this is not a correctness fix -- it is upkeep. Callers hand us
    a source pinned to whatever they wrote it against; we move it to the current
    std at upload time so recipes stay on supported semantics and nobody has to
    remember to bump a literal.

    Returns the source unchanged if it has no recognizable prelude (e.g. the
    caller passed a fragment), so this is safe to run unconditionally.
    """
    if not _FS_PRELUDE_RE.search(source):
        return source
    out = _FS_PRELUDE_RE.sub(lambda m: f"{m.group(1)}{version}{m.group(3)}", source, count=1)
    out = _FS_IMPORT_RE.sub(lambda m: f"{m.group(1)}{version}{m.group(3)}", out)
    return out


def _build_namespace(fs_element_id: str, source_microversion_id: str) -> str:
    """Construct the `namespace` string for a BTMFeature-134 invoking a
    custom feature from a Feature Studio.

    Format (confirmed via Onshape forum post 26720, Paul J. Premakumar):
        e{fs_element_id}::m{source_microversion_id}

    Letter prefixes glued directly to ids with NO `::` separator between
    prefix and id. Earlier guesses using `{did}::ws::{wid}::e::{eid}` or
    `e::{eid}::m::{mv}` were all wrong.
    """
    return f"e{fs_element_id}::m{source_microversion_id}"


def _to_onshape_parameter(param: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a caller-friendly parameter descriptor to BTMParameter* JSON.

    Input shape: `{"id": "<name>", "type": "quantity|string|boolean|real", "value": <val>}`

    - quantity: value is a string like "5 mm" or "0.5 in"; becomes BTMParameterQuantity-147
    - string:   BTMParameterString-149
    - boolean:  BTMParameterBoolean-144
    - real:     BTMParameterQuantity-147 without units
    - query:    BTMParameterQueryList-148; value = deterministic ID (or list)
    - enum:     BTMParameterEnum-145; value = enum constant name

    Unknown types raise ValueError so bad inputs fail loudly.
    """
    pid = param.get("id")
    ptype = (param.get("type") or "").lower()
    value = param.get("value")
    if not pid:
        raise ValueError(f"parameter missing id: {param!r}")

    if ptype == "quantity":
        expression = value if isinstance(value, str) else f"{value}"
        return {
            "btType": "BTMParameterQuantity-147",
            "parameterId": pid,
            "expression": expression,
        }
    if ptype == "string":
        return {
            "btType": "BTMParameterString-149",
            "parameterId": pid,
            "value": "" if value is None else str(value),
        }
    if ptype == "boolean":
        return {
            "btType": "BTMParameterBoolean-144",
            "parameterId": pid,
            "value": bool(value),
        }
    if ptype == "real":
        return {
            "btType": "BTMParameterQuantity-147",
            "parameterId": pid,
            "isInteger": False,
            "value": float(value) if value is not None else 0.0,
            "expression": str(value) if value is not None else "0",
        }
    if ptype == "integer":
        # A precondition declared with isInteger(...) yields a spec with
        # quantityType=INTEGER; a float-shaped BTMParameterQuantity-147 does
        # not satisfy it.
        return {
            "btType": "BTMParameterQuantity-147",
            "parameterId": pid,
            "isInteger": True,
            "value": int(value) if value is not None else 0,
            "expression": str(int(value)) if value is not None else "0",
        }
    if ptype == "query":
        # Geometry selection. `value` is a deterministic ID (from list_entities)
        # or a list of them. Without this, any custom feature whose precondition
        # takes `definition.x is Query` is unreachable through this tool -- which
        # rules out the entire class of FS features that operate on picked faces
        # or edges (threads on a bore, sweeps along a chosen edge, ...).
        ids = value if isinstance(value, (list, tuple)) else [value]
        ids = [str(i) for i in ids if i]
        if not ids:
            raise ValueError(f"query parameter {pid!r} needs at least one deterministic ID")
        return {
            "btType": "BTMParameterQueryList-148",
            "parameterId": pid,
            "queries": [
                {
                    "btType": "BTMIndividualQuery-138",
                    "deterministicIds": ids,
                }
            ],
        }
    if ptype == "enum":
        # `value` is the enum CONSTANT name as declared in the FS source
        # (e.g. "FULL_FACE"). enumName defaults to the FS enum's own name,
        # which callers pass as `enumName`; Onshape tolerates it being absent
        # for custom-feature enums since the parameterId pins the type.
        return {
            "btType": "BTMParameterEnum-145",
            "parameterId": pid,
            "value": "" if value is None else str(value),
            "enumName": str(param.get("enumName") or ""),
            "namespace": "",
        }
    raise ValueError(
        f"unsupported parameter type {ptype!r} for id={pid!r}; "
        "use quantity | string | boolean | real | integer | query | enum"
    )
