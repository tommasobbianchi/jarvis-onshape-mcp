#!/usr/bin/env python3
"""re2sketch — reverse-engineer a 2D profile from a photo into a metric Onshape sketch.

Pipeline: calibrate (px->mm from a ruler reference) -> contrast outline
(grayscale -> edge/threshold -> contours) -> vectorize (simplify / spline) ->
emit (a) an annotated preview PNG, (b) contours in mm as JSON, and (c) a
complete FeatureScript feature that rebuilds the profile as a real, in-scale
sketch on a chosen plane.

The scaling happens here, in image space, using the ruler — so the Onshape
sketch is born in millimetres. Onshape itself cannot insert a raster image
into a sketch via API, nor raster-trace it; this is why the work is done here
and only the finished vector sketch is injected (via write_featurescript_feature).

Usage (calibration points are pixel coords of two ruler ticks a known mm apart):
  re2sketch.py --image foto.jpg --ref 120,540 980,540 --ref-mm 100 \
      --roi 60,60,1100,700 --keep largest --simplify-mm 0.3 --out /tmp/re_out

Outputs in --out: preview.png, contours_mm.json, sketch.fs
"""
from __future__ import annotations
import argparse, json, math, os, sys

try:
    import cv2
    import numpy as np
except Exception as e:  # pragma: no cover
    sys.stderr.write(f"error: OpenCV/numpy not available: {e!r}\n"
                     "install: python3 -m venv .venv && .venv/bin/pip install opencv-python-headless numpy\n")
    sys.exit(3)

# Optional: shapely for tested self-intersection repair (CadVertor's buffer(0)
# approach). If absent, fall back to raw contour points.
try:
    from shapely.geometry import Polygon as _ShPoly
except Exception:
    _ShPoly = None

FS_VERSION = "2931"  # keep in sync with jarvis custom_features.DEFAULT_FS_VERSION


def _pt(s: str):
    x, y = s.split(",")
    return (float(x), float(y))


def refine_point(gray, p, win):
    """Snap a clicked ruler tick to the strongest gradient pixel in a window."""
    x, y = int(round(p[0])), int(round(p[1]))
    h, w = gray.shape
    x0, x1 = max(0, x - win), min(w, x + win + 1)
    y0, y1 = max(0, y - win), min(h, y + win + 1)
    patch = gray[y0:y1, x0:x1].astype(np.float32)
    if patch.size == 0:
        return (float(x), float(y))
    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    my, mx = np.unravel_index(int(np.argmax(mag)), mag.shape)
    return (float(x0 + mx), float(y0 + my))


def detect_fiducials(gray, dict_name):
    """Detect ArUco/AprilTag markers. Returns {id: 4x2 corner array (px)} or {}.

    A coplanar fiducial of known size gives px->mm WITHOUT the ruler-parallax
    error (ruler and part usually lie on different planes), and >=2 markers let
    us rectify perspective via a homography. (Design ref: skotagiri/tooltrace.)
    """
    if not hasattr(cv2, "aruco"):
        return {}
    adict = getattr(cv2.aruco, dict_name, None)
    if adict is None:
        return {}
    dictionary = cv2.aruco.getPredefinedDictionary(adict)
    params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(dictionary, params)
    corners, ids, _ = detector.detectMarkers(gray)
    out = {}
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            out[int(i)] = c.reshape(4, 2).astype(float)
    return out


def marker_scale_px_per_mm(markers, marker_mm):
    """Average px-per-mm from every marker's 4 side lengths."""
    sides = []
    for c in markers.values():
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
            sides.append(math.hypot(c[a][0] - c[b][0], c[a][1] - c[b][1]))
    return (sum(sides) / len(sides)) / marker_mm if sides else None


def extract_contours(gray, args, scale):
    g = gray
    if args.blur and args.blur >= 3:
        k = args.blur | 1
        g = cv2.GaussianBlur(g, (k, k), 0)
    if args.invert:
        g = 255 - g
    if args.method == "canny":
        lo, hi = (int(v) for v in args.canny.split(","))
        edges = cv2.Canny(g, lo, hi)
    elif args.method == "otsu":
        _, edges = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:  # adaptive
        edges = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 31, 5)
    if args.close and args.close > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=args.close)

    if args.fill:
        # CLOSED-CONTOUR mode for hand-drawn / open outlines: bridge the pencil
        # gaps, fill the enclosed region, then trace the SOLID region's outer
        # boundary instead of the thin stroke. An opening step removes thin
        # spurs (e.g. a reference tick sticking out of the outline).
        gap = max(1, int(round(args.gap_close_mm * scale)))
        bridge = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap + 1, 2 * gap + 1))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, bridge, iterations=1)
        # flood-fill from a border pixel: everything reachable from outside is
        # background; its inverse is the enclosed (filled) region.
        h, w = closed.shape
        ff = closed.copy()
        mask = np.zeros((h + 2, w + 2), np.uint8)
        cv2.floodFill(ff, mask, (0, 0), 255)
        filled = closed | cv2.bitwise_not(ff)
        # kill thin spurs / reattach to the body: opening with a disk sized to
        # the spur width we want gone.
        spur = max(1, int(round(args.spur_mm * scale)))
        disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * spur + 1, 2 * spur + 1))
        filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, disk, iterations=1)
        edges = filled

    mode = cv2.RETR_EXTERNAL if args.keep in ("largest", "outer", "fill") else cv2.RETR_TREE
    cnts, _ = cv2.findContours(edges, mode, cv2.CHAIN_APPROX_SIMPLE)
    min_area_px = args.min_area_mm2 * (scale ** 2)
    cnts = [c for c in cnts if cv2.contourArea(c) >= min_area_px]
    cnts.sort(key=cv2.contourArea, reverse=True)
    return cnts, edges


def main():
    ap = argparse.ArgumentParser(description="Reverse-engineer a 2D profile photo into a metric Onshape sketch.")
    ap.add_argument("--image", required=True)
    ap.add_argument("--ref", nargs=2, type=_pt, default=None, metavar=("X1,Y1", "X2,Y2"),
                    help="two ruler-tick pixel coords a known distance apart (manual calibration)")
    ap.add_argument("--ref-mm", type=float, default=None, help="real distance between --ref points (mm)")
    ap.add_argument("--fiducial", action="store_true",
                    help="calibrate (and de-skew) from ArUco markers in the photo instead of --ref")
    ap.add_argument("--marker-mm", dest="marker_mm", type=float, default=None,
                    help="physical side length of the ArUco marker (mm) — required with --fiducial")
    ap.add_argument("--aruco-dict", dest="aruco_dict", default="DICT_4X4_50",
                    help="OpenCV ArUco dictionary name (e.g. DICT_4X4_50, DICT_5X5_100, DICT_APRILTAG_36h11)")
    ap.add_argument("--rectify", action="store_true",
                    help="with >=2 markers in --fiducial mode, warp the photo flat (homography) before tracing")
    ap.add_argument("--refine", dest="refine", action="store_true", default=True)
    ap.add_argument("--no-refine", dest="refine", action="store_false")
    ap.add_argument("--refine-win", type=int, default=15)
    ap.add_argument("--method", choices=["canny", "otsu", "adaptive"], default="canny")
    ap.add_argument("--canny", default="50,150")
    ap.add_argument("--blur", type=int, default=5)
    ap.add_argument("--close", type=int, default=1)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--fill", action="store_true",
                    help="closed-contour mode: bridge gaps, fill region, trace the solid outline (for hand-drawn/open outlines)")
    ap.add_argument("--gap-close-mm", dest="gap_close_mm", type=float, default=2.0,
                    help="max pencil-gap width to bridge in --fill mode (mm)")
    ap.add_argument("--spur-mm", dest="spur_mm", type=float, default=2.0,
                    help="thin spurs up to this width are removed in --fill mode (mm) — e.g. a reference tick")
    ap.add_argument("--roi", type=str, default=None, help="X,Y,W,H px — restrict detection (exclude the ruler)")
    ap.add_argument("--keep", choices=["largest", "outer", "all"], default="largest")
    ap.add_argument("--min-area-mm2", type=float, default=25.0)
    ap.add_argument("--simplify-mm", type=float, default=0.3, help="approxPolyDP epsilon in mm")
    ap.add_argument("--curve", choices=["polyline", "spline"], default="polyline")
    ap.add_argument("--origin", choices=["centroid", "bbox", "ref1"], default="centroid")
    ap.add_argument("--plane", choices=["top", "front", "right"], default="top")
    ap.add_argument("--flip-y", dest="flip_y", action="store_true", default=True)
    ap.add_argument("--no-flip-y", dest="flip_y", action="store_false")
    ap.add_argument("--feature-name", default="RE image profile")
    ap.add_argument("--emit", choices=["onshape", "freecad", "both"], default="onshape",
                    help="which CAD back-end to emit: Onshape FeatureScript, a FreeCAD "
                         "Sketcher python script (run via freecad-mcp execute_code), or both")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        sys.stderr.write(f"error: image not found: {args.image}\n"); sys.exit(2)
    img = cv2.imread(args.image)
    if img is None:
        sys.stderr.write(f"error: could not read image: {args.image}\n"); sys.exit(2)
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # --- calibration ---
    markers = {}
    p1 = p2 = None
    dist_px = None
    if args.fiducial:
        if args.marker_mm is None:
            sys.stderr.write("error: --fiducial requires --marker-mm (marker side in mm)\n"); sys.exit(1)
        markers = detect_fiducials(gray, args.aruco_dict)
        if not markers:
            sys.stderr.write(f"error: no ArUco markers ({args.aruco_dict}) found — "
                             "check the dictionary or fall back to --ref\n"); sys.exit(4)
        # Optional perspective rectification: a single marker's 4 corners are a known
        # square (marker_mm x marker_mm), so they fully determine a homography to a
        # fronto-parallel view. Warping the whole image by it removes camera tilt and
        # makes scale uniform everywhere — far more correct than a bbox stretch.
        if args.rectify:
            tgt = marker_scale_px_per_mm(markers, args.marker_mm) or 10.0  # keep ~same px/mm
            side = args.marker_mm * tgt
            mc = next(iter(markers.values())).astype(np.float32)  # detector order: TL,TR,BR,BL
            srcq = mc
            # place the marker square far from the origin so the part (around it) stays in frame
            ox = oy = 4 * side
            dstq = np.array([[ox, oy], [ox + side, oy],
                             [ox + side, oy + side], [ox, oy + side]], np.float32)
            Hmat = cv2.getPerspectiveTransform(srcq, dstq)
            outW = max(W, int(ox + side + 4 * side))
            outH = max(H, int(oy + side + 4 * side))
            # white border: otherwise the off-image area is black and (with --invert)
            # becomes foreground, merging with the part and killing contour detection.
            img = cv2.warpPerspective(img, Hmat, (outW, outH),
                                      borderValue=(255, 255, 255))
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            H, W = gray.shape[:2]
            markers = detect_fiducials(gray, args.aruco_dict)  # re-detect on warped image
            if not markers:
                sys.stderr.write("error: lost markers after rectify warp\n"); sys.exit(4)
        scale = marker_scale_px_per_mm(markers, args.marker_mm)
        if not scale:
            sys.stderr.write("error: could not measure marker scale\n"); sys.exit(4)
    else:
        if not args.ref or args.ref_mm is None:
            sys.stderr.write("error: need either --fiducial (+ --marker-mm) or --ref X1,Y1 X2,Y2 --ref-mm\n")
            sys.exit(1)
        p1, p2 = args.ref
        if args.refine:
            p1 = refine_point(gray, p1, args.refine_win)
            p2 = refine_point(gray, p2, args.refine_win)
        dist_px = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if dist_px < 1:
            sys.stderr.write("error: reference points coincide\n"); sys.exit(1)
        scale = dist_px / args.ref_mm  # px per mm

    # --- ROI crop (exclude ruler etc.) ---
    # Crop (don't mask): masking with 0 + --invert turns the outside into a
    # white border that hijacks Otsu and yields no real contours.
    roi = None
    off = (0, 0)
    work = gray
    if args.roi:
        rx, ry, rw, rh = (int(v) for v in args.roi.split(","))
        roi = (rx, ry, rw, rh)
        off = (rx, ry)
        work = gray[ry:ry + rh, rx:rx + rw]

    cnts, edges = extract_contours(work, args, scale)
    # offset contours back into full-image coordinates
    if off != (0, 0):
        for c in cnts:
            c[:, 0, 0] += off[0]
            c[:, 0, 1] += off[1]

    # Drop artifacts: contours that trace the ROI rectangle / image border, or
    # span almost the whole working region (the masked-edge "frame" contour).
    region_area = (roi[2] * roi[3]) if roi else (W * H)
    rx0, ry0, rx1, ry1 = (roi[0], roi[1], roi[0] + roi[2], roi[1] + roi[3]) if roi else (0, 0, W, H)
    kept = []
    for c in cnts:
        if cv2.contourArea(c) >= 0.85 * region_area:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        touch = (bx <= rx0 + 2 and by <= ry0 + 2 and bx + bw >= rx1 - 2 and by + bh >= ry1 - 2)
        if touch and cv2.contourArea(c) >= 0.6 * region_area:
            continue
        kept.append(c)
    cnts = kept
    if args.keep == "largest" and cnts:
        cnts = cnts[:1]
    if not cnts:
        sys.stderr.write("error: no contours found — adjust --method/--canny/--blur/--roi\n"); sys.exit(4)

    # --- origin in px ---
    if args.origin == "ref1":
        ox, oy = p1
    elif args.origin == "bbox":
        allpts = np.vstack([c.reshape(-1, 2) for c in cnts])
        ox, oy = float(allpts[:, 0].min()), float(allpts[:, 1].max())  # bottom-left
    else:  # centroid of the largest kept contour
        M = cv2.moments(cnts[0])
        ox = M["m10"] / M["m00"] if M["m00"] else float(cnts[0][:, 0, 0].mean())
        oy = M["m01"] / M["m00"] if M["m00"] else float(cnts[0][:, 0, 1].mean())

    def to_mm(px, py):
        x = (px - ox) / scale
        y = (-(py - oy) if args.flip_y else (py - oy)) / scale
        return (round(x, 4), round(y, 4))

    # --- simplify + convert ---
    # A self-intersecting outline makes Onshape's extrude fail with
    # CANNOT_RESOLVE_ENTITIES. CadVertor's tested fix: wrap in a shapely Polygon
    # and, if invalid, repair with buffer(0); take the exterior ring. Reused
    # here (see /home/tommaso/projects/cad-3d/strumenti/CadVertor) rather than
    # re-rolling geometry cleanup. Falls back to raw approxPolyDP if no shapely.
    eps_px = max(0.5, args.simplify_mm * scale)
    contours_mm = []
    for c in cnts:
        approx = cv2.approxPolyDP(c, eps_px, True).reshape(-1, 2)
        if len(approx) < 3:
            continue
        ring = approx
        if _ShPoly is not None:
            poly = _ShPoly([(float(px), float(py)) for px, py in approx])
            if not poly.is_valid:
                poly = poly.buffer(0)          # repair self-intersections
            if poly.is_empty:
                continue
            if poly.geom_type == "MultiPolygon":
                poly = max(poly.geoms, key=lambda g: g.area)
            xs, ys = poly.exterior.coords.xy
            ring = list(zip(xs, ys))[:-1]      # drop shapely's repeated closing pt
        pts = [to_mm(px, py) for px, py in ring]
        if len(pts) >= 3:
            contours_mm.append(pts)
    if not contours_mm:
        sys.stderr.write("error: contours too small after simplify\n"); sys.exit(4)

    os.makedirs(args.out, exist_ok=True)

    # --- preview overlay ---
    prev = img.copy()
    cv2.drawContours(prev, cnts, -1, (0, 220, 0), 2)
    if markers:
        for mid, c in markers.items():
            cv2.polylines(prev, [c.astype(int)], True, (0, 0, 255), 2)
            cv2.putText(prev, str(mid), (int(c[0][0]), int(c[0][1]) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.putText(prev, f"{len(markers)} marker(s) {args.marker_mm:g}mm  ({scale:.3f}px/mm)"
                    + ("  rectified" if args.rectify else ""),
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    else:
        for pp in (p1, p2):
            cv2.circle(prev, (int(pp[0]), int(pp[1])), 6, (0, 0, 255), -1)
        cv2.line(prev, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])), (0, 0, 255), 2)
        cv2.putText(prev, f"{args.ref_mm:g}mm = {dist_px:.1f}px  ({scale:.3f}px/mm)",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.circle(prev, (int(ox), int(oy)), 7, (0, 215, 255), -1)
    bar = int(round(10 * scale))  # 10 mm scale bar
    cv2.line(prev, (10, H - 20), (10 + bar, H - 20), (255, 0, 0), 4)
    cv2.putText(prev, "10mm", (10, H - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
    if roi:
        cv2.rectangle(prev, (roi[0], roi[1]), (roi[0] + roi[2], roi[1] + roi[3]), (255, 255, 0), 1)
    cv2.imwrite(os.path.join(args.out, "preview.png"), prev)

    meta = {
        "scale_px_per_mm": round(scale, 5),
        "calibration": ("fiducial" if markers else "ref"),
        "ref_mm": args.ref_mm, "dist_px": (round(dist_px, 2) if dist_px else None),
        "ref_px": ([list(p1), list(p2)] if p1 is not None else None),
        "markers": ({int(k): v.tolist() for k, v in markers.items()} if markers else None),
        "marker_mm": args.marker_mm, "rectified": bool(args.fiducial and args.rectify),
        "origin_px": [round(ox, 2), round(oy, 2)],
        "image_size": [W, H], "n_contours": len(contours_mm),
        "points_per_contour": [len(c) for c in contours_mm],
        "bbox_mm": _bbox(contours_mm), "contours_mm": contours_mm,
    }
    with open(os.path.join(args.out, "contours_mm.json"), "w") as f:
        json.dump(meta, f, indent=1)

    outputs = {}
    if args.emit in ("onshape", "both"):
        fs_path = os.path.join(args.out, "sketch.fs")
        with open(fs_path, "w") as f:
            f.write(emit_fs(contours_mm, args))
        outputs["sketch_fs"] = fs_path
    if args.emit in ("freecad", "both"):
        fc_path = os.path.join(args.out, "sketch_freecad.py")
        with open(fc_path, "w") as f:
            f.write(emit_freecad(contours_mm, args))
        outputs["sketch_freecad"] = fc_path

    # concise stdout summary for the agent
    print(json.dumps({
        "ok": True, "scale_px_per_mm": round(scale, 4),
        "calibration": meta["calibration"], "emit": args.emit,
        "dist_px": (round(dist_px, 1) if dist_px else None),
        "n_contours": len(contours_mm), "points_per_contour": meta["points_per_contour"],
        "bbox_mm": meta["bbox_mm"], "origin_px": meta["origin_px"],
        "preview": os.path.join(args.out, "preview.png"),
        "contours_json": os.path.join(args.out, "contours_mm.json"),
        **outputs,
    }, indent=1))


def _bbox(contours_mm):
    xs = [p[0] for c in contours_mm for p in c]
    ys = [p[1] for c in contours_mm for p in c]
    return {"w": round(max(xs) - min(xs), 3), "h": round(max(ys) - min(ys), 3),
            "x": [round(min(xs), 3), round(max(xs), 3)], "y": [round(min(ys), 3), round(max(ys), 3)]}


def emit_fs(contours_mm, args):
    plane_map = {
        "top":   "plane(vector(0, 0, 0) * meter, vector(0, 0, 1), vector(1, 0, 0))",
        "front": "plane(vector(0, 0, 0) * meter, vector(0, 1, 0), vector(1, 0, 0))",
        "right": "plane(vector(0, 0, 0) * meter, vector(1, 0, 0), vector(0, 1, 0))",
    }
    lines = [
        f"FeatureScript {FS_VERSION};",
        f'import(path : "onshape/std/geometry.fs", version : "{FS_VERSION}.0");',
        "",
        f'annotation {{ "Feature Type Name" : "{args.feature_name}" }}',
        "export const reProfile = defineFeature(function(context is Context, id is Id, definition is map)",
        "    precondition {}",
        "    {",
        f'        var sk = newSketchOnPlane(context, id + "sk", {{ "sketchPlane" : {plane_map[args.plane]} }});',
    ]
    for i, c in enumerate(contours_mm):
        pts = list(c)
        if args.curve == "spline":
            if pts and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            arr = ", ".join(f"vector({x}, {y}) * millimeter" for x, y in pts[:-1])
            lines.append(f'        skFitSpline(sk, "c{i}", {{ "points" : [{arr}], "isPeriodic" : true }});')
        else:  # polyline (closed)
            if pts and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            arr = ", ".join(f"vector({x}, {y}) * millimeter" for x, y in pts)
            lines.append(f'        skPolyline(sk, "c{i}", {{ "points" : [{arr}] }});')
    lines += [
        "        skSolve(sk);",
        "    });",
        "",
    ]
    return "\n".join(lines)


def emit_freecad(contours_mm, args):
    """Emit a FreeCAD python script that builds a Sketcher sketch from the same
    mm point list, runnable via freecad-mcp's execute_code (XML-RPC :9875) or
    pasted into FreeCAD's console. Same contours as the Onshape path — only the
    back-end differs. Writes a status JSON so the RPC's flaky stdout can be
    bypassed (read the file via ssh), per the freecad-reverse-engineering skill.

    Plane mapping (FreeCAD sketch is XY in its own placement):
      top -> XY_Plane, front -> XZ_Plane, right -> YZ_Plane.
    """
    plane_attr = {"top": "XY_Plane", "front": "XZ_Plane", "right": "YZ_Plane"}[args.plane]
    name = args.feature_name.replace('"', "'")
    closed = (args.curve == "polyline")  # polyline => line segments; spline => one BSpline
    lines = [
        "import FreeCAD as App, Part, Sketcher, json, traceback",
        "st = {'step': 'start'}",
        "try:",
        "    doc = App.ActiveDocument or App.newDocument('REImport')",
        f"    sk = doc.addObject('Sketcher::SketchObject', {name!r})",
        f"    try: sk.AttachmentSupport = [(doc.{plane_attr}, '')]; sk.MapMode = 'FlatFace'",
        "    except Exception: pass",
        "    geo = []",
    ]
    for i, c in enumerate(contours_mm):
        pts = [list(p) for p in c]
        if pts and pts[0] != pts[-1]:
            pts = pts + [pts[0]]            # close the loop
        lines.append(f"    pts{i} = {pts!r}")
        if args.curve == "spline":
            lines.append(f"    bs{i} = Part.BSplineCurve()")
            lines.append(f"    bs{i}.interpolate([App.Vector(x, y, 0) for (x, y) in pts{i}[:-1]], PeriodicFlag=True)")
            lines.append(f"    geo.append(bs{i})")
        else:
            lines.append(f"    for a, b in zip(pts{i}[:-1], pts{i}[1:]):")
            lines.append(f"        geo.append(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))")
    lines += [
        "    sk.addGeometry(geo, False)",
        "    doc.recompute()",
        "    st['ok'] = True; st['geometry_count'] = len(geo); st['name'] = sk.Name",
        "except Exception:",
        "    st['ok'] = False; st['error'] = traceback.format_exc()",
        "import os",
        "json.dump(st, open(os.path.expanduser('~/re_status.json'), 'w'), indent=1)",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
