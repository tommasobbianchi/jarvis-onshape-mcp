---
name: image-reverse-engineer
description: >
  Reverse-engineer a real 2D profile from a PHOTO into a correctly-scaled
  Onshape sketch. Calibrates px->mm from a ruler/caliper in the shot, extracts
  the outline by contrast (OpenCV), vectorizes it, and injects a metric sketch
  into the open Part Studio via write_featurescript_feature. Use when the user
  has a photo of a part with a ruler beside it and wants to trace/scale it for
  3D printing. Triggers: "/image-reverse-engineer", "reverse engineering da
  foto", "ricalca questo profilo dalla foto", "scala l'immagine col metro",
  "outline a contrasto", "trasforma la foto del profilo in sketch", "trace this
  photo to a sketch", "vectorize this profile photo", "image to sketch with a
  ruler", "scale image from ruler". NOT for building a multi-feature part from a
  drawing — use vision-decompose for that.
allowed-tools:
  - Bash
  - Read
---

# Image → metric Onshape sketch (reverse engineering a 2D profile)

Goal: a photo of a flat part with a ruler/caliper next to it → a real,
in-millimetre Onshape sketch tracing its outline, ready to extrude.

## Hard-won lessons (read before tweaking)

- **Calibrate on the PART, not the ruler.** Ruler and part are usually on
  different planes → parallax (ruler read ~12.7 px/mm vs part-true ~8.5). Use
  `--ref` across the part's known extent (e.g. its 60 mm width), not the ruler ticks.
- **Default to `--curve polyline`, not spline.** A hand-traced periodic
  `skFitSpline` self-intersects and Onshape's extrude dies with
  `CANNOT_RESOLVE_ENTITIES`. A simple closed polyline from `findContours` is a
  valid region and extrudes cleanly. (Verified: 62.7×66.4×2 mm solid.)
- **Self-intersection repair is reused, not rewritten.** The convert path wraps
  each contour in a shapely `Polygon` and repairs with `buffer(0)`, the tested
  trick from **CadVertor** (`/home/tommaso/projects/cad-3d/strumenti/CadVertor/core/vectorization.py`).
  Needs `shapely` in the venv; falls back to raw points if absent.
- **`--fill` is the closure routine** for open/pencil outlines: bridge gaps,
  flood-fill the region, opening to drop thin spurs (e.g. a reference tick), then
  trace the SOLID region boundary — not the thin stroke.
- **ROI must exclude the ruler but not clip the part.** Too tall a ROI lets the
  ruler back in (→ thin-strip contour); too tight clips the bottom. Crop, don't mask.
- **Don't hand-copy coordinates into the inject call.** Read `sketch.fs` and pass
  its contents verbatim — manual copy caused several wasted failed injections.

## Why the work happens OUTSIDE Onshape (read this once)

Onshape's API/FeatureScript **cannot** insert a raster image into a sketch, and
Onshape has **no** raster→vector / contrast-tracing. So the manual "insert
image in sketch, dimension it to scale, trace by hand" route is NOT
automatable — and it's also where the "only the line scales, not the image"
friction comes from (Onshape rescales the background image only on the FIRST
sketch dimension). We sidestep all of that: calibrate and trace in **image
space** (OpenCV), where scaling is just `mm / px`, then inject a sketch that is
**born in millimetres**. No background image, no scaling dance.

## Tools

- Helper: `scripts/re2sketch.py` (run with the bundled venv).
  ```
  SKILL=/home/tommaso/projects/jarvis-onshape-mcp/skills/image-reverse-engineer
  $SKILL/.venv/bin/python $SKILL/scripts/re2sketch.py --help
  ```
- Back-end: `--emit {onshape,freecad,both}` (default onshape). Same contours, two emitters.
- Inject (Onshape): `mcp__onshape__write_featurescript_feature` with `sketch.fs`.
- Inject (FreeCAD): run the emitted `sketch_freecad.py` via freecad-mcp XML-RPC :9875
  `execute_code`, e.g. `~/.claude/skills/freecad-reverse-engineering/scripts/fc_send.py
  <out>/sketch_freecad.py <host>` (needs FreeCAD open + "FreeCAD MCP -> Start RPC Server";
  read result from `~/re_status.json`, not RPC stdout).
- Verify: `mcp__onshape__render_part_studio_views` / `measure` (Onshape); FreeCAD
  `re_status.json` `{ok, geometry_count}` + a screenshot.
- The image: either a path the user gives, or via the `/paste` skill
  (`~/.claude-clipboard/last.png`).

## Workflow

### 1. Get the image and LOOK at it
`Read` the image file directly (you are multimodal). Identify:
- the **part profile** to trace, and
- the **ruler/caliper** and two tick marks a **known distance** apart
  (pick them as **far apart as possible** — calibration error scales inversely
  with the reference length).

### 2. Calibration — two modes

**(a) Fiducial (preferred, robust):** if the photo contains an ArUco/AprilTag
marker of known physical size, use `--fiducial --marker-mm <side>`. The helper
measures px→mm from the marker's side lengths — coplanar with the part, so **no
ruler parallax**. With ≥2 markers add `--rectify` to warp the photo
fronto-parallel (homography) before tracing, removing perspective tilt too.
Dictionaries via `--aruco-dict` (default `DICT_4X4_50`; AprilTag = `DICT_APRILTAG_36h11`).
Print a marker: any ArUco generator, or `cv2.aruco.generateImageMarker`.

**(b) Manual ruler (fallback):** estimate the **pixel coordinates** of two ruler
ticks (origin top-left, x right, y down) and pass `--ref X1,Y1 X2,Y2 --ref-mm <d>`.
`--refine` (default) snaps each to the strongest nearby edge. **Calibrate on the
PART's known extent, not the ruler** if ruler and part are on different planes
(parallax). One of `--fiducial` or `--ref` is required.

### 3. Run the helper
```
$SKILL/.venv/bin/python $SKILL/scripts/re2sketch.py \
  --image <path> \
  --ref <x1,y1> <x2,y2> --ref-mm <real_mm> \
  --roi <X,Y,W,H>            # box around the PART, excludes the ruler/clutter \
  --method canny --canny 50,150 --blur 5 \
  --keep largest --min-area-mm2 25 --simplify-mm 0.3 \
  --curve polyline --origin centroid --plane top \
  --out /tmp/re_out
```
Outputs in `--out`: `preview.png`, `contours_mm.json`, `sketch.fs`. The helper
prints a JSON summary (scale, n_contours, points, **bbox_mm**).

### 4. VERIFY ON THE PREVIEW (vision in the loop — do not skip)
`Read` `preview.png`. Check:
- the **green outline** hugs the real profile (not the ruler, not shadows);
- the **red reference line** sits exactly tick-to-tick on the ruler;
- the **blue 10 mm scale bar** looks right against the ruler;
- the printed **bbox_mm** matches the part's expected real size.

If wrong, iterate the knobs and re-run:
- outline catches noise/shadows → raise `--canny` lows, increase `--blur`, or tighten `--roi`.
- outline broken/open → `--method otsu` or `--adaptive`, add `--close 2`, maybe `--invert`.
- too jagged / too many points → raise `--simplify-mm`; too rounded → lower it.
- inner holes wanted → `--keep all` (RETR_TREE); only the outer wall → `--keep largest`.
- smooth organic curve → `--curve spline`; mechanical/straight → `--curve polyline`.

### 5. Sanity-check dimensions (don't trust pixels blindly)
Compare `bbox_mm` to reality. A designer almost certainly used round numbers —
if a width reads 19.85 mm it was meant to be 20. Note intended dims; you can
edit them in Onshape after, or adjust `--ref` and re-run if the whole scale is off.

### 6. Inject the sketch into the open Part Studio
Use the bound document context (from `onshape-cad` / the active document:
documentId, workspaceId, elementId). Read `sketch.fs` and pass its **entire
contents** as `featureScript`:
```
mcp__onshape__write_featurescript_feature(
  documentId=..., workspaceId=..., elementId=<Part Studio>,
  featureType="reProfile",            # MUST match `export const reProfile`
  featureName="RE <part> profile",
  featureScript=<contents of sketch.fs>)
```
Returns `{ok, status, feature_id}`. `status:"OK"` = the sketch built and is in
scale. (It is a sketch *inside* a custom feature, so it will NOT appear in
`list_sketches` — that is expected; it is still usable for extrude.)

### 7. Verify in Onshape
`render_part_studio_views` (top view) and confirm the outline matches the
photo. `measure` a known dimension and check it equals the real mm.

## Gotchas
- **FS version**: `re2sketch.py` emits `FeatureScript 2931;`. If a future Onshape
  std bump makes `write_featurescript_feature` reject it, update `FS_VERSION` in
  the script (and the import line) to the current version.
- **Calibration is everything.** The single biggest error source is the ruler
  reference. Far-apart ticks + the preview scale-bar check catch most of it.
- **Exclude the ruler with `--roi`**, or the largest contour may be the ruler.
- **Each inject spawns a `ClaudeFS_reProfile` Feature Studio.** When iterating,
  delete the previous feature (`delete_feature`) before re-injecting, and tidy
  stray `ClaudeFS_reProfile` studios afterwards.
- This produces a faithful **starting** sketch. Clean it up in Onshape with
  constraints (horizontal/vertical/tangent) and rounded dimensions before
  extruding — the trace is evidence, not gospel.

## Relation to other skills
- `vision-decompose` — for turning a drawing/render into a *feature tree* (multi-
  feature parts). This skill is for *one precise 2D outline* from a photo.
- `onshape-cad` — binds Claude to the Onshape document open in the user's browser
  so you have the documentId/workspaceId/elementId to inject into.
