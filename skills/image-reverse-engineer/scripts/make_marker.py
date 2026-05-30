#!/usr/bin/env python3
"""make_marker — generate a print-ready ArUco marker at an EXACT physical size.

Outputs a PNG (high-DPI) and a 1:1 PDF so the printed side length is exactly
--mm. A white quiet zone (>=1 module) is added around the marker. Matches the
re2sketch.py defaults (DICT_4X4_50, id 0) so the same image both prints and
calibrates. For bicolor 3D printing: black = base, white = inlaid modules
(or engrave the black modules into a white face).

  make_marker.py --mm 50 --id 0 --dict DICT_4X4_50 --dpi 600 --out /path/marker
"""
import argparse, os, sys
import cv2
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mm", type=float, default=50.0, help="active marker side length in mm")
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--dict", default="DICT_4X4_50")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--quiet-modules", type=float, default=1.0,
                    help="white border width in marker modules (>=1 recommended)")
    ap.add_argument("--out", required=True, help="output path prefix (no extension)")
    args = ap.parse_args()

    adict = getattr(cv2.aruco, args.dict, None)
    if adict is None:
        sys.exit(f"unknown dict {args.dict}")
    dictionary = cv2.aruco.getPredefinedDictionary(adict)

    # px from physical size: px = mm/25.4 * dpi
    side_px = int(round(args.mm / 25.4 * args.dpi))
    marker = cv2.aruco.generateImageMarker(dictionary, args.id, side_px)

    # modules in this dict (e.g. DICT_4X4 -> 4 data + border = 6 total)
    total_modules = dictionary.markerSize + 2
    module_px = side_px / total_modules
    quiet_px = int(round(args.quiet_modules * module_px))

    canvas = np.full((side_px + 2 * quiet_px, side_px + 2 * quiet_px), 255, np.uint8)
    canvas[quiet_px:quiet_px + side_px, quiet_px:quiet_px + side_px] = marker

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    png = args.out + ".png"
    cv2.imwrite(png, canvas)

    # 1:1 PDF via reportlab if available, else a DPI-tagged PNG note
    total_mm = args.mm + 2 * args.quiet_modules * (args.mm / total_modules)
    pdf = args.out + ".pdf"
    made_pdf = False
    try:
        from reportlab.pdfgen import canvas as pdfcanvas
        from reportlab.lib.units import mm as MM
        from reportlab.lib.pagesizes import A4
        c = pdfcanvas.Canvas(pdf, pagesize=A4)
        pw, ph = A4
        x = (pw - total_mm * MM) / 2
        y = (ph - total_mm * MM) / 2
        c.drawImage(png, x, y, width=total_mm * MM, height=total_mm * MM)
        c.setFont("Helvetica", 9)
        c.drawString(20 * MM, 15 * MM,
                     f"ArUco {args.dict} id={args.id}  active side = {args.mm:g} mm "
                     f"(print at 100% / Actual Size). Measure the printed side with calipers "
                     f"and pass that to --marker-mm.")
        c.showPage(); c.save()
        made_pdf = True
    except Exception as e:
        sys.stderr.write(f"(no PDF: reportlab missing — {e!r}; PNG is DPI-tagged)\n")

    print(f"dict={args.dict} id={args.id}")
    print(f"active side = {args.mm:g} mm  ({side_px}px @ {args.dpi}dpi)")
    print(f"with quiet zone: {total_mm:.2f} mm square, {canvas.shape[1]}x{canvas.shape[0]} px")
    print(f"PNG: {png}")
    print(f"PDF: {pdf}" if made_pdf else "PDF: (not created)")


if __name__ == "__main__":
    main()
