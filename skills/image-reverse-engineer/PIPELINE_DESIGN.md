# Image → metric Onshape sketch — pipeline design & reusable-asset map

Synthesises four research passes (Onshape public ecosystem, GitHub, tooltrace.ai,
the FeatureScript aggregators) plus FS sources extracted directly via the Onshape
REST API. Goal: build the GENERAL pipeline by reusing tested parts, not rewriting.

## The 5 stages and what to reuse for each

### 1. Capture (hardware/optics)
- Top-down, optical zoom (not close-up) to minimise **parallax** — the #1 error.
- A coplanar fiducial beats an in-frame ruler: a ruler only scales one line; it does
  not correct perspective.

### 2. Calibrate px→mm (+ de-skew)  ← BIGGEST upgrade
- **Now:** two ruler ticks + known mm (`--ref ... --ref-mm`). Works but no de-skew,
  and ruler/part parallax caused a ~1.9× error this session → calibrate on the PART's
  known extent instead.
- **Upgrade — AprilTag/ArUco fiducial mode (`cv2.aruco`)**: homography gives scale +
  perspective rectification in one shot. Design refs (study, no license to copy):
  `skotagiri/tooltrace` (AprilTag paper + perspective warp), `coreydylan/flatlay-measure`.
  Reference-object math: `snsharma1311/object-size` (pixels-per-metric, MIT-ish).
- **Upgrade — perspective de-skew** for tilted photos: `cv2.getPerspectiveTransform`
  pattern from `andrewdcampbell/OpenCV-Document-Scanner` (clean Python ref).

### 3. Outline extraction
- **Now:** OpenCV `findContours` + `--fill` closure (close gaps → flood-fill →
  opening to drop spurs). Good for mechanical parts with clean contrast.
- **Upgrade — FastSAM segmentation fallback** when contrast/threshold fails (shadows,
  busy background). Pattern from `skotagiri/tooltrace` (FastSAM ONNX) — robust outline.
- Organic curves where approxPolyDP is too coarse: `visioncortex/vtracer` (MIT, 6k★)
  or `potrace`, bridged to points via `mathandy/svgpathtools` (MIT).

### 4. Clean / close / validate  ← already solved
- **shapely** (BSD): `buffer(0)` self-intersection repair (lifted from CadVertor),
  `simplify`, `buffer(+d).buffer(-d)` gap-close, `.exterior`/`.interiors` outer-vs-holes.
  Already integrated in re2sketch.py convert path. This is the fix for
  CANNOT_RESOLVE_ENTITIES.
- Subpixel option: `scikit-image` `measure.find_contours`.
- **Primitive fitting** (so the sketch has real arcs/lines/circles, not a 200-pt
  polyline): `adityaintwala/Image2CAD` (Apache-2.0) line/circle detectors.

### 5. Inject into Onshape  ← architecture CONFIRMED correct
- The ONLY way to commit editable geometry via API: add a custom feature via
  `write_featurescript_feature` → `newSketchOnPlane` → emit → `skSolve` →
  `qSketchRegion` → `extrude`. Confirmed by the ecosystem agent:
  - `evalFeatureScript` is **lambda-only, cannot mutate** the model.
  - **No** raster→vector API, **no** DXF/SVG→sketch-entity API (`opImportForeign`
    is BREP-only; DXF import is UI-only). So DXF/SVG round-trips add a blob hop for
    no gain when we already hold clean contours → SKIP them.
- **Reference FS (extracted via API, archived in ./reference/):**
  - `CSV_to_Sketch.fs` — the canonical "(x,y) list → sketch" leg. Closure =
    `points = append(points, points[0])` for POLYLINE; SPLINE uses non-periodic
    `skFitSpline`. **This is exactly what our helper does** — validates our choice.
  - `3D_Spline.fs` — `opFitSpline` with `isPeriodic`. The periodic-spline route that
    self-intersects on hand-traced points (our failed-extrude bug). Use only for
    pre-cleaned/convex point sets.
  - `3D_Points.fs` — minimal points→geometry template.
  - License: all three are public Onshape docs = all-rights-reserved by default.
    Kept as LEARN-FROM reference, not redistributed verbatim. Our emitter is our own.

## Hard rules learned (do not relitigate)
- Default `--curve polyline` (simple closed loop = valid region). Periodic spline
  self-intersects → extrude fails.
- Calibrate on the PART, not the ruler (parallax).
- Read sketch.fs and pass verbatim to the injector — never hand-copy coordinates.
- ROI crop (not mask) to exclude the ruler without clipping the part.

## Prioritised next implementation steps
1. **AprilTag/ArUco calibration mode** (`--fiducial`) — ✅ DONE 2026-05-30.
   `--fiducial --marker-mm S [--aruco-dict D] [--rectify]`. px→mm from marker sides
   (no ruler parallax); `--rectify` warps fronto-parallel via a SINGLE marker's
   4 known-square corners (white border-fill so --invert keeps the part). Uses
   `cv2.aruco` (already in opencv-python-headless — no new dep). `--ref` fallback
   unchanged. Locked by `scripts/selftest.py` — 7 assertions, all PASS 2026-05-30
   (fiducial scale 9.97px/mm, bbox 40x25, rectify bbox 50x30 + corners ~90°, convert
   closed ring, gasket 62.7x66.4). HONEST CAVEAT: validated only on SYNTHETIC tilt;
   on a real camera photo with a printed marker still unproven (no printer now).
2. **FastSAM segmenter fallback** (`--segment sam`) — robust outline on hard photos.
   ⚠️ HEAVY (torch + onnxruntime + ~140MB weights), so keep it OPT-IN and
   lazy-loaded — invoked only when contrast extraction fails. Runs on **nativedev**
   (RTX 3090, 24GB — confirmed) as a persistent local service/venv. nativedev is the
   always-on node; clients (behemoth/fujiyama) are NOT for persistent inference even
   though they have GPUs — the "everything persistent on nativedev" rule wins.
   Architecture: a small local FastSAM helper returns a binary mask; the rest of the
   pipeline (contour→close→inject) is unchanged and stays light. Deferred until needed.
3. **Primitive fitting** (`--primitives`) — emit arcs/lines/circles (Image2CAD) for
   constraint-friendly sketches.
4. (optional) vtracer path for organic curves.

## Multi-CAD back-end (FreeCAD compatibility) — DONE 2026-05-30
Because the pipeline is already split (CAD-agnostic point list in mm + a thin
inject stage), supporting FreeCAD was just a second emitter:
- `--emit {onshape,freecad,both}`. Same `contours_mm`, two back-ends.
- `emit_freecad()` writes `sketch_freecad.py`: a FreeCAD `Sketcher::SketchObject`
  built from the same closed point list via `addGeometry([Part.LineSegment(...)])`
  (polyline) or `Part.BSplineCurve.interpolate(..., PeriodicFlag=True)` (spline),
  attached to XY/XZ/YZ plane per `--plane`. It writes status to `~/re_status.json`
  (the freecad-reverse-engineering skill's lesson: RPC stdout is flaky after a
  timeout — read the file instead).
- INJECT into FreeCAD via freecad-mcp XML-RPC :9875 `execute_code` (reuse
  `~/.claude/skills/freecad-reverse-engineering/scripts/fc_send.py <script> <host>`).
  The bridge needs FreeCAD open with "FreeCAD MCP -> Start RPC Server".
- Locked by selftest T5 (script compiles + has Sketcher geometry).
- LIVE-VERIFIED 2026-05-30 on the gasket: injected via fc_send.py into FreeCAD on
  the ThinkPad (RPC :9875). `re_status.json` → `ok:true, geometry_count:6,
  name:RE_image_profile`; independent doc query confirmed a
  `Sketcher::SketchObject` with 6 geometries in the active document. End-to-end
  FreeCAD back-end now proven, not just compile-validated.
- NOTE: the freecad-mcp RPC addon falls into a timeout/empty-output state after
  several rapid `execute_code` calls (documented in fc_send.py). The code still
  runs — read state from `~/re_status.json`, don't trust RPC stdout, and avoid
  hammering it with back-to-back calls.

## Where the software runs (architecture constraint)
FeatureScript is a sandboxed geometry-only language on Onshape's servers: no
Python/OpenCV/ML, no file/network. So the pipeline is necessarily SPLIT:
- **Off-Onshape (our infra):** calibration, OpenCV, FastSAM, vectorization → point
  list in mm. Heavy stages live here, on **nativedev** (always-on, RTX 3090 24GB).
  Clients (behemoth etc.) are not used for persistent inference.
- **On Onshape (FeatureScript via write_featurescript_feature):** only point-list →
  sketch → extrude. Lightweight. This split is a hard constraint, not a choice.

## Tooling status on nativedev
- skill venv: opencv-python-headless, numpy, shapely ✓. Need for upgrades: `cv2.aruco`
  (in opencv-contrib), a FastSAM ONNX weight + onnxruntime, optionally vtracer_py.
- No chrome on nativedev (browser skill unavailable here); FS extraction done via REST
  API with the jarvis keys — no login needed for PUBLIC documents.

## License-clean core to build on
vtracer (MIT), ezdxf (MIT), shapely (BSD), scikit-image (BSD), Image2CAD (Apache-2.0),
svgpathtools (MIT), cad-to-shapely (MIT). AVOID copying: skotagiri/tooltrace,
Automatic_Reverse_Engineering (no license — ideas only); public Onshape FS docs
(all-rights-reserved — patterns only).
