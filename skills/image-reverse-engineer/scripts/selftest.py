#!/usr/bin/env python3
"""selftest — regression guard for re2sketch.py.

Generates synthetic images with known ground truth and asserts the helper
recovers the right scale/size, so future edits can't silently break the
calibration / rectify / convert path. Run with the skill venv:

  $SKILL/.venv/bin/python scripts/selftest.py

Exit 0 = all pass. No network, no Onshape, no printed markers needed.
"""
import json, os, subprocess, sys, tempfile
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
R2S = os.path.join(HERE, "re2sketch.py")
TMP = tempfile.mkdtemp(prefix="re2s_selftest_")
ADICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
FAILS = []


def run(args, out):
    r = subprocess.run([PY, R2S, *args, "--out", out], capture_output=True, text=True)
    if r.returncode != 0:
        return None, (r.stderr.strip() or f"exit {r.returncode}")
    return json.load(open(os.path.join(out, "contours_mm.json"))), None


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")
    if not cond:
        FAILS.append(name)


def approx(a, b, tol):
    return abs(a - b) <= tol


def corner_angles(pts):
    import math
    out, N = [], len(pts)
    for i in range(N):
        a, b, c = pts[(i - 1) % N], pts[i], pts[(i + 1) % N]
        v1 = (a[0] - b[0], a[1] - b[1]); v2 = (c[0] - b[0], c[1] - b[1])
        m1 = math.hypot(*v1); m2 = math.hypot(*v2)
        if m1 * m2 == 0:
            continue
        d = v1[0] * v2[0] + v1[1] * v2[1]
        out.append(math.degrees(math.acos(max(-1, min(1, d / (m1 * m2))))))
    return out


# --- T1: fiducial calibration, axis-aligned (scale + bbox) ---
img = np.full((900, 800, 3), 255, np.uint8)
img[50:350, 50:350] = cv2.cvtColor(cv2.aruco.generateImageMarker(ADICT, 0, 300), cv2.COLOR_GRAY2BGR)  # 30mm@10px/mm
cv2.rectangle(img, (200, 450), (600, 700), (0, 0, 0), -1)  # 40x25 mm
cv2.imwrite(os.path.join(TMP, "fid.png"), img)
m, err = run(["--image", os.path.join(TMP, "fid.png"), "--fiducial", "--marker-mm", "30",
              "--aruco-dict", "DICT_4X4_50", "--roi", "180,430,440,290", "--method", "otsu",
              "--invert", "--keep", "largest", "--curve", "polyline", "--simplify-mm", "0.5",
              "--min-area-mm2", "50"], os.path.join(TMP, "fid_out"))
if m is None:
    check("T1 fiducial", False, err)
else:
    check("T1 scale ~10px/mm", approx(m["scale_px_per_mm"], 10.0, 0.3), f"scale={m['scale_px_per_mm']}")
    check("T1 bbox 40x25mm", approx(m["bbox_mm"]["w"], 40, 1.5) and approx(m["bbox_mm"]["h"], 25, 1.5),
          f"{m['bbox_mm']['w']:.1f}x{m['bbox_mm']['h']:.1f}")
    check("T1 calib=fiducial", m["calibration"] == "fiducial")

# --- T2: rectify squares up a tilted part (corner angles ~90) ---
flat = np.full((1100, 1000, 3), 255, np.uint8)
flat[60:360, 60:360] = cv2.cvtColor(cv2.aruco.generateImageMarker(ADICT, 0, 300), cv2.COLOR_GRAY2BGR)
cv2.rectangle(flat, (120, 500), (620, 800), (0, 0, 0), -1)  # 50x30 mm
Hsim = cv2.getPerspectiveTransform(
    np.float32([[0, 0], [1000, 0], [1000, 1100], [0, 1100]]),
    np.float32([[80, 40], [940, 150], [880, 1050], [40, 980]]))
cv2.imwrite(os.path.join(TMP, "tilt.png"),
            cv2.warpPerspective(flat, Hsim, (1000, 1100), borderValue=(255, 255, 255)))
m, err = run(["--image", os.path.join(TMP, "tilt.png"), "--fiducial", "--marker-mm", "30",
              "--aruco-dict", "DICT_4X4_50", "--rectify", "--method", "otsu", "--invert",
              "--keep", "largest", "--curve", "polyline", "--simplify-mm", "0.5",
              "--min-area-mm2", "50"], os.path.join(TMP, "tilt_out"))
if m is None:
    check("T2 rectify", False, err)
else:
    check("T2 rectify bbox ~50x30mm", approx(m["bbox_mm"]["w"], 50, 2.5) and approx(m["bbox_mm"]["h"], 30, 2.5),
          f"{m['bbox_mm']['w']:.1f}x{m['bbox_mm']['h']:.1f}")
    ring = m["contours_mm"][0]
    angs = corner_angles(ring) if len(ring) == 4 else []
    sq = bool(angs) and all(abs(a - 90) <= 3 for a in angs)
    check("T2 rectify corners ~90deg", sq, f"angles={[round(a,1) for a in angs]}")

# --- T3: convert yields a closed simple ring (extrudable) ---
blob = np.full((600, 600, 3), 255, np.uint8)
poly = np.array([[100, 100], [500, 120], [480, 300], [520, 480], [120, 460], [90, 300]], np.int32)
cv2.fillPoly(blob, [poly], (0, 0, 0))
cv2.imwrite(os.path.join(TMP, "blob.png"), blob)
m, err = run(["--image", os.path.join(TMP, "blob.png"), "--ref", "100,100", "200,100", "--ref-mm", "10",
              "--no-refine", "--roi", "70,70,480,440", "--method", "otsu", "--invert",
              "--keep", "largest", "--curve", "polyline", "--simplify-mm", "0.5",
              "--min-area-mm2", "50"], os.path.join(TMP, "blob_out"))
if m is None:
    check("T3 convert", False, err)
else:
    check("T3 closed simple ring >=3pts", len(m["contours_mm"][0]) >= 3, f"{len(m['contours_mm'][0])} pts")

# --- T4: gasket regression (only if the real photo is present) ---
gasket = "/home/tommaso/re_gasket/gasket_full.jpg"
if os.path.isfile(gasket):
    m, err = run(["--image", gasket, "--ref", "375,900", "1140,900", "--ref-mm", "60", "--no-refine",
                  "--roi", "360,360,800,1010", "--method", "otsu", "--invert", "--close", "2",
                  "--fill", "--gap-close-mm", "4", "--spur-mm", "5", "--keep", "largest",
                  "--curve", "polyline", "--simplify-mm", "0.6", "--min-area-mm2", "200"],
                 os.path.join(TMP, "gasket_out"))
    if m is None:
        check("T4 gasket", False, err)
    else:
        check("T4 gasket bbox ~62.7x66.4mm",
              approx(m["bbox_mm"]["w"], 62.7, 2) and approx(m["bbox_mm"]["h"], 66.4, 2),
              f"{m['bbox_mm']['w']:.1f}x{m['bbox_mm']['h']:.1f}")
else:
    print("[SKIP] T4 gasket regression (photo not present)")

# --- T5: FreeCAD back-end emits a compilable Sketcher script ---
fimg = np.full((600, 600, 3), 255, np.uint8)
cv2.rectangle(fimg, (150, 150), (450, 400), (0, 0, 0), -1)  # 30x25 mm @10px/mm
cv2.imwrite(os.path.join(TMP, "fc.png"), fimg)
fc_outdir = os.path.join(TMP, "fc_out")
m, err = run(["--image", os.path.join(TMP, "fc.png"), "--ref", "150,150", "250,150", "--ref-mm", "10",
              "--no-refine", "--roi", "130,130,340,290", "--method", "otsu", "--invert",
              "--keep", "largest", "--curve", "polyline", "--simplify-mm", "0.5",
              "--min-area-mm2", "50", "--emit", "freecad"], fc_outdir)
if m is None:
    check("T5 freecad emit", False, err)
else:
    fc = os.path.join(fc_outdir, "sketch_freecad.py")  # written to the out dir
    ok_file = os.path.isfile(fc)
    compiles = False
    has_geom = False
    if ok_file:
        src = open(fc).read()
        try:
            compile(src, "sketch_freecad.py", "exec"); compiles = True
        except SyntaxError:
            compiles = False
        has_geom = "addGeometry" in src and "Part.LineSegment" in src
    check("T5 freecad script compiles", compiles, fc or "(no file)")
    check("T5 freecad has Sketcher geometry", has_geom)

print()
if FAILS:
    print(f"FAILED: {FAILS}"); sys.exit(1)
print("ALL TESTS PASSED")
