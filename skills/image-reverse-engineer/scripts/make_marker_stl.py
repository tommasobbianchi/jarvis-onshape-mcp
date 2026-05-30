#!/usr/bin/env python3
"""make_marker_stl — ArUco marker as a 3D STL for bicolor FDM printing.

Geometry: a full base plate (the "black" colour, includes the white quiet zone
border which is also part of the base material) + the marker's WHITE modules
raised by --top-mm on top. Print the base in black, do a filament change at
z = --base-mm, finish the raised modules in white. The active marker side is
exactly --mm; measure the printed side and pass it to re2sketch --marker-mm.

  make_marker_stl.py --mm 50 --id 0 --dict DICT_4X4_50 \
      --base-mm 2 --top-mm 0.6 --quiet-modules 1 --out aruco.stl
"""
import argparse, struct, sys
import cv2
import numpy as np


def boxes_to_binary_stl(boxes, path):
    """boxes: list of (x0,y0,z0,x1,y1,z1) in mm. Writes a binary STL of axis-aligned cuboids."""
    tris = []

    def quad(a, b, c, d, n):
        tris.append((n, a, b, c))
        tris.append((n, a, c, d))

    for (x0, y0, z0, x1, y1, z1) in boxes:
        v = {
            "000": (x0, y0, z0), "100": (x1, y0, z0), "110": (x1, y1, z0), "010": (x0, y1, z0),
            "001": (x0, y0, z1), "101": (x1, y0, z1), "111": (x1, y1, z1), "011": (x0, y1, z1),
        }
        quad(v["000"], v["010"], v["110"], v["100"], (0, 0, -1))   # bottom
        quad(v["001"], v["101"], v["111"], v["011"], (0, 0, 1))    # top
        quad(v["000"], v["100"], v["101"], v["001"], (0, -1, 0))   # front (y0)
        quad(v["010"], v["011"], v["111"], v["110"], (0, 1, 0))    # back (y1)
        quad(v["000"], v["001"], v["011"], v["010"], (-1, 0, 0))   # left (x0)
        quad(v["100"], v["110"], v["111"], v["101"], (1, 0, 0))    # right (x1)

    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for n, a, b, c in tris:
            f.write(struct.pack("<3f", *n))
            f.write(struct.pack("<3f", *a))
            f.write(struct.pack("<3f", *b))
            f.write(struct.pack("<3f", *c))
            f.write(struct.pack("<H", 0))
    return len(tris)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mm", type=float, default=50.0, help="active marker side (mm)")
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--dict", default="DICT_4X4_50")
    ap.add_argument("--base-mm", type=float, default=2.0, help="black base plate thickness")
    ap.add_argument("--top-mm", type=float, default=0.6, help="raised white module height")
    ap.add_argument("--quiet-modules", type=float, default=1.0, help="white quiet-zone border in modules")
    ap.add_argument("--merge-modules", action="store_true", default=True,
                    help="merge orthogonally-adjacent white cells into rows (fewer triangles)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    adict = getattr(cv2.aruco, args.dict, None)
    if adict is None:
        sys.exit(f"unknown dict {args.dict}")
    dictionary = cv2.aruco.getPredefinedDictionary(adict)
    n = dictionary.markerSize + 2          # modules per side incl. black border (e.g. 6)
    mod = args.mm / n                       # mm per module

    # render at exactly n px and read the module matrix (255=white module)
    img = cv2.aruco.generateImageMarker(dictionary, args.id, n)
    white = (img > 127)                     # bool grid [row][col], row 0 = top

    # Build ONE grid over the whole plate at module resolution: a quiet-zone ring
    # of WHITE cells (required — ArUco needs the black border surrounded by white)
    # plus the inner n x n marker matrix. White cells are raised; black cells stay
    # at base level. Quiet zone must be an integer number of modules.
    qm = int(round(args.quiet_modules))
    if abs(qm - args.quiet_modules) > 1e-6:
        sys.stderr.write("note: quiet-modules rounded to integer for the 3D grid\n")
    N = n + 2 * qm                          # total modules per side incl. quiet ring
    total = N * mod                         # full plate side

    grid = np.zeros((N, N), dtype=bool)     # True = white (raised)
    grid[:, :] = True                       # quiet ring defaults white...
    for r in range(n):
        for c in range(n):
            grid[qm + r][qm + c] = bool(white[r][c])   # ...inner = marker matrix

    boxes = [(0.0, 0.0, 0.0, total, total, args.base_mm)]   # black base plate
    z0, z1 = args.base_mm, args.base_mm + args.top_mm

    placed = 0
    if args.merge_modules:
        for r in range(N):
            c = 0
            while c < N:
                if grid[r][c]:
                    c2 = c
                    while c2 < N and grid[r][c2]:
                        c2 += 1
                    x0 = c * mod
                    y0 = (N - 1 - r) * mod    # flip so STL +y = image up
                    boxes.append((x0, y0, z0, c2 * mod, y0 + mod, z1))
                    placed += (c2 - c)
                    c = c2
                else:
                    c += 1
    else:
        for r in range(N):
            for c in range(N):
                if grid[r][c]:
                    x0 = c * mod
                    y0 = (N - 1 - r) * mod
                    boxes.append((x0, y0, z0, x0 + mod, y0 + mod, z1)); placed += 1

    ntri = boxes_to_binary_stl(boxes, args.out)
    print(f"dict={args.dict} id={args.id}")
    print(f"active side = {args.mm:g} mm  ({n} modules, {mod:.3f} mm/module)")
    print(f"full plate  = {total:.2f} x {total:.2f} mm  (incl. {args.quiet_modules:g}-module quiet zone)")
    print(f"Z: base {args.base_mm:g} mm (black) + raised modules {args.top_mm:g} mm (white)"
          f"  -> total {args.base_mm + args.top_mm:g} mm")
    print(f"white modules placed: {placed} cells, {len(boxes)} boxes, {ntri} triangles")
    print(f"STL: {args.out}")
    print("PRINT: filament change at z = %.2f mm (base->white). Measure printed active side, "
          "pass to re2sketch --marker-mm." % args.base_mm)


if __name__ == "__main__":
    main()
