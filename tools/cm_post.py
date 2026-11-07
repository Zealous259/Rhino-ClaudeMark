"""Draw ClaudeMark analysis and montage results (CPython 3: numpy, PIL, matplotlib, PyMuPDF).

    python cm_post.py section  section.json  OUTDIR
    python cm_post.py sun      sun.json      OUTDIR
    python cm_post.py shadows  shadows.json  OUTDIR
    python cm_post.py isovist  isovist.json  OUTDIR
    python cm_post.py overlay  PHOTO EDGES.png OUT.png        (model edges over a photo: check the camera)
    python cm_post.py montage  PHOTO RENDER_RGBA.png OUT.jpg   (render composited over the photo)

The JSON comes from tools/cm_analyse.py running in Rhino. Sheets carry a title and the legend or scale
they need, nothing else (the user’s rule: titles only). SVGs name the user’s board font first so Illustrator uses it;
PNG previews use Arial Narrow.
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance

FONT_DIR = r"C:\Windows\Fonts"
SVG_FONT = "'Helvetica Neue LT Pro', 'HelveticaNeueLT Pro 97 BlkCn', 'Arial Narrow', Arial, sans-serif"


def font(size, bold=True, narrow=True):
    name = ("ARIALNB.TTF" if bold else "ARIALN.TTF") if narrow else ("arialbd.ttf" if bold else "arial.ttf")
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), int(size))
    except Exception:
        return ImageFont.load_default()


def load(path):
    with open(path) as fh:
        return json.load(fh)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def svg_to_png(svg_path, png_path, dpi=150):
    import pymupdf
    doc = pymupdf.open(svg_path)
    pix = doc[0].get_pixmap(dpi=dpi)
    pix.save(png_path)
    doc.close()


def nice_scale(extent_units_mm, avail_mm):
    """The smallest standard 1:n that fits extent (already in mm at 1:1) into avail mm."""
    for n in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 1250, 2000, 2500, 5000, 10000, 20000):
        if extent_units_mm / n <= avail_mm:
            return n
    return 50000


def scale_bar(x, y, n, unit_mm_per_m, max_len_mm=60):
    """SVG for a scale bar at 1:n: alternating blocks, labelled in metres."""
    m_per_mm = n / 1000.0
    total_m = max_len_mm * m_per_mm
    step = 10 ** math.floor(math.log10(total_m / 4.0)) if total_m > 0 else 1
    for k in (1, 2, 5, 10):
        if total_m / (step * k) <= 5:
            step *= k
            break
    seg = step / m_per_mm
    count = max(1, int(max_len_mm // seg))
    parts = []
    for i in range(count):
        fill = "#000" if i % 2 == 0 else "#fff"
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="1.6" fill="%s" stroke="#000" stroke-width="0.2"/>'
                     % (x + i * seg, y, seg, fill))
    for i in range(count + 1):
        label = ("%g" % (i * step)) + (" m" if i == count else "")
        parts.append('<text x="%.2f" y="%.2f" font-size="2.6" font-family="%s" text-anchor="middle">%s</text>'
                     % (x + i * seg, y + 5, SVG_FONT, _esc(label)))
    return "\n".join(parts)


# ---------------------------------------------------------------- section

BANDS = [(2.0, "#222222", 0.35), (6.0, "#555555", 0.25), (15.0, "#8a8a8a", 0.18), (1e9, "#b8b8b8", 0.13)]


def section(path, out):
    d = load(path)
    unit_m = d.get("unit_m", 1.0)
    pts = [p for c in d["cut"] for p in c["pts"]] + [p for b in d["beyond"] for p in b["pts"]]
    if not pts:
        raise SystemExit("section: nothing was cut or seen beyond")
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    page_w, page_h, margin, title_h = 420.0, 297.0, 18.0, 16.0
    n = max(nice_scale((u1 - u0) * unit_m * 1000.0, page_w - 2 * margin),
            nice_scale((v1 - v0) * unit_m * 1000.0, page_h - 2 * margin - title_h))
    k = unit_m * 1000.0 / n   # mm on paper per model unit
    ox = (page_w - (u1 - u0) * k) / 2.0
    oy = margin + title_h + (page_h - 2 * margin - title_h - (v1 - v0) * k) / 2.0

    def X(u):
        return ox + (u - u0) * k

    def Y(v):
        return oy + (v1 - v) * k

    def poly(ps):
        return " ".join("%.3f,%.3f" % (X(p[0]), Y(p[1])) for p in ps)

    g_beyond = {i: [] for i in range(len(BANDS))}
    for b in d["beyond"]:
        dm = b.get("d", 0.0) * unit_m
        i = next(j for j, (lim, _, _) in enumerate(BANDS) if dm <= lim)
        g_beyond[i].append('<polyline points="%s"/>' % poly(b["pts"]))
    cut_fill, cut_line = [], []
    for c in d["cut"]:
        if c.get("closed") and len(c["pts"]) > 2:
            cut_fill.append('<polygon points="%s"/>' % poly(c["pts"]))
        else:
            cut_line.append('<polyline points="%s"/>' % poly(c["pts"]))
    title = d.get("title") or "SECTION"
    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (page_w, page_h, page_w, page_h),
           '<rect width="100%" height="100%" fill="#ffffff"/>']
    for i in sorted(g_beyond, reverse=True):
        _, col, w = BANDS[i]
        name = ["Beyond 0-2 m", "Beyond 2-6 m", "Beyond 6-15 m", "Beyond 15 m+"][i]
        svg.append('<g id="%s" fill="none" stroke="%s" stroke-width="%.2f" stroke-linejoin="round" stroke-linecap="round">' % (_esc(name), col, w))
        svg.extend(g_beyond[i])
        svg.append("</g>")
    svg.append('<g id="Cut poche" fill="#141414" stroke="#000000" stroke-width="0.5" stroke-linejoin="round">')
    svg.extend(cut_fill)
    svg.append("</g>")
    svg.append('<g id="Cut lines" fill="none" stroke="#000000" stroke-width="0.7" stroke-linejoin="round" stroke-linecap="round">')
    svg.extend(cut_line)
    svg.append("</g>")
    svg.append('<text x="%g" y="%g" font-size="9" font-weight="900" font-family="%s">%s</text>' % (margin, margin + 7, SVG_FONT, _esc(title.upper())))
    svg.append('<text x="%g" y="%g" font-size="3.4" font-family="%s" text-anchor="end">1:%d at A3</text>' % (page_w - margin, margin + 7, SVG_FONT, n))
    svg.append(scale_bar(page_w - margin - 62, page_h - margin + 2, n, 1000.0))
    svg.append("</svg>")
    base = os.path.join(out, d.get("name", "section"))
    with open(base + ".svg", "w", encoding="utf-8") as fh:
        fh.write("\n".join(svg))
    svg_to_png(base + ".svg", base + ".png", dpi=150)
    # the cut line drawn on the user’s capture, with the look direction
    if d.get("capture") and d.get("line_px"):
        img = Image.open(d["capture"]).convert("RGB")
        dr = ImageDraw.Draw(img)
        (ax, ay), (bx, by) = d["line_px"]["a"], d["line_px"]["b"]
        lx, ly = d["line_px"].get("look", [0, 0])
        for wdt, col in ((9, (0, 0, 0)), (5, (240, 68, 56))):
            dr.line([(ax, ay), (bx, by)], fill=col, width=wdt)
        for (px, py) in ((ax, ay), (bx, by)):
            tip = (px + lx * 60, py + ly * 60)
            for wdt, col in ((9, (0, 0, 0)), (5, (240, 68, 56))):
                dr.line([(px, py), tip], fill=col, width=wdt)
            dr.ellipse([tip[0] - 9, tip[1] - 9, tip[0] + 9, tip[1] + 9], fill=(240, 68, 56), outline=(0, 0, 0), width=2)
        f = font(34)
        dr.text((ax + 12, ay - 46), "A", font=f, fill=(240, 68, 56), stroke_width=3, stroke_fill=(0, 0, 0))
        img.save(base + " - cut line on view.png")
    print("section: %s.svg / .png at 1:%d (A3)" % (base, n))


# ---------------------------------------------------------------- sun hours

def sun(path, out):
    import matplotlib
    cmap = matplotlib.colormaps["inferno"]
    d = load(path)
    base_img = Image.open(d["capture"]).convert("RGB")
    W, H = base_img.size
    g = float(d.get("grid_px", 8))
    dates = d["dates"]
    vmax = max(1.0, math.ceil(max([max(s["hours"]) for s in d["samples"]] + [1.0])))
    soft = ImageEnhance.Brightness(ImageOps.grayscale(base_img).convert("RGB")).enhance(1.15)
    panels = []
    for k, date in enumerate(dates):
        over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(over)
        tot = wsum = 0.0
        for s in d["samples"]:
            h = s["hours"][k]
            r, gg, b, _ = cmap(min(1.0, h / vmax))
            x, y = s["px"]
            dr.rectangle([x - g / 2.0, y - g / 2.0, x + g / 2.0, y + g / 2.0],
                         fill=(int(r * 255), int(gg * 255), int(b * 255), 215))
            w = s.get("w", 1.0)
            tot += h * w
            wsum += w
        img = Image.alpha_composite(soft.convert("RGBA"), over).convert("RGB")
        dr = ImageDraw.Draw(img)
        mean = tot / wsum if wsum else 0.0
        dr.text((28, 22), date.upper(), font=font(max(30, W // 40)), fill=(20, 20, 20))
        dr.text((28, 22 + max(30, W // 40) + 6), "mean %.1f h of direct sun" % mean, font=font(max(18, W // 80), bold=False),
                fill=(40, 40, 40))
        panels.append(img)
    # legend strip under the panels
    gap = 24
    sheet_w = sum(p.width for p in panels) + gap * (len(panels) - 1)
    leg_h = 110
    sheet = Image.new("RGB", (sheet_w, H + leg_h), (255, 255, 255))
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width + gap
    dr = ImageDraw.Draw(sheet)
    lw, lx, ly = min(900, sheet_w - 80), 40, H + 30
    for i in range(lw):
        r, gg, b, _ = cmap(i / float(lw - 1))
        dr.line([(lx + i, ly), (lx + i, ly + 26)], fill=(int(r * 255), int(gg * 255), int(b * 255)))
    for t in range(int(vmax) + 1):
        tx = lx + lw * t / vmax
        dr.line([(tx, ly + 26), (tx, ly + 34)], fill=(0, 0, 0), width=2)
        dr.text((tx - 6, ly + 38), "%d" % t, font=font(22, bold=False), fill=(0, 0, 0))
    dr.text((lx + lw + 20, ly - 2), "hours of direct sun", font=font(24), fill=(0, 0, 0))
    o = os.path.join(out, d.get("name", "sun hours") + ".png")
    sheet.save(o)
    print("sun: %s (scale 0-%d h)" % (o, vmax))


# ---------------------------------------------------------------- shadow study

def shadows(path, out):
    d = load(path)
    frames = d["frames"]
    dates = []
    for fr in frames:
        if fr["date"] not in dates:
            dates.append(fr["date"])
    times = sorted(set(fr["time"] for fr in frames))
    first = Image.open(frames[0]["path"])
    tw = 520
    th = int(first.height * tw / float(first.width))
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lab_w = 30 + max(probe.textlength(dt.upper(), font=font(30)) for dt in dates)
    lab_w, head_h, gap = int(lab_w), 50, 8
    sheet = Image.new("RGB", (lab_w + len(times) * (tw + gap), head_h + len(dates) * (th + gap)), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    for j, t in enumerate(times):
        dr.text((lab_w + j * (tw + gap) + 6, 10), t, font=font(30), fill=(0, 0, 0))
    for i, dt in enumerate(dates):
        dr.text((10, head_h + i * (th + gap) + th // 2 - 18), dt.upper(), font=font(30), fill=(0, 0, 0))
        for fr in frames:
            if fr["date"] != dt:
                continue
            j = times.index(fr["time"])
            im = Image.open(fr["path"]).convert("RGB").resize((tw, th), Image.LANCZOS)
            sheet.paste(im, (lab_w + j * (tw + gap), head_h + i * (th + gap)))
    o = os.path.join(out, d.get("name", "shadow study") + ".png")
    sheet.save(o)
    print("shadows: %s (%d dates x %d times)" % (o, len(dates), len(times)))


# ---------------------------------------------------------------- isovist

PIN_COLS = [(240, 68, 56), (46, 144, 250), (18, 183, 106), (253, 176, 34), (155, 89, 230)]


def isovist(path, out):
    d = load(path)
    unit_m = d.get("unit_m", 1.0)
    pins = d["pins"]
    allp = [p for pin in pins for p in pin["poly"]] + [p for ob in d.get("obstacles", []) for p in ob]
    xs, ys = [p[0] for p in allp], [p[1] for p in allp]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    page_w, page_h, margin, title_h = 297.0, 297.0, 16.0, 14.0
    n = max(nice_scale((x1 - x0) * unit_m * 1000.0, page_w - 2 * margin),
            nice_scale((y1 - y0) * unit_m * 1000.0, page_h - 2 * margin - title_h))
    k = unit_m * 1000.0 / n
    ox = (page_w - (x1 - x0) * k) / 2.0
    oy = margin + title_h + (page_h - 2 * margin - title_h - (y1 - y0) * k) / 2.0

    def P(p):
        return "%.3f,%.3f" % (ox + (p[0] - x0) * k, oy + (y1 - p[1]) * k)

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (page_w, page_h, page_w, page_h),
           '<rect width="100%" height="100%" fill="#ffffff"/>']
    for i, pin in enumerate(pins):
        r, g, b = PIN_COLS[i % len(PIN_COLS)]
        svg.append('<g id="Isovist %s"><polygon points="%s" fill="rgb(%d,%d,%d)" fill-opacity="0.28" stroke="rgb(%d,%d,%d)" stroke-width="0.35"/></g>'
                   % (_esc(pin["label"]), " ".join(P(p) for p in pin["poly"]), r, g, b, r, g, b))
    svg.append('<g id="Cut at eye height" fill="none" stroke="#000" stroke-width="0.6" stroke-linejoin="round">')
    for ob in d.get("obstacles", []):
        svg.append('<polyline points="%s"/>' % " ".join(P(p) for p in ob))
    svg.append("</g>")
    for i, pin in enumerate(pins):
        r, g, b = PIN_COLS[i % len(PIN_COLS)]
        cx, cy = [float(v) for v in P(pin["origin"]).split(",")]
        area_m2 = pin["area"] * unit_m * unit_m
        # the pin's number inside its dot, the area beside it (numbers, not tags)
        svg.append('<circle cx="%.3f" cy="%.3f" r="2.4" fill="rgb(%d,%d,%d)" stroke="#000" stroke-width="0.3"/>' % (cx, cy, r, g, b))
        svg.append('<text x="%.3f" y="%.3f" font-size="3.0" font-weight="700" font-family="%s" fill="#fff" '
                   'text-anchor="middle">%s</text>' % (cx, cy + 1.05, SVG_FONT, _esc(pin["label"])))
        svg.append('<text x="%.3f" y="%.3f" font-size="3.4" font-weight="700" font-family="%s">%s m&#178;</text>'
                   % (cx + 3.6, cy + 1.2, SVG_FONT, "{:,.0f}".format(area_m2) if area_m2 >= 10 else "%.1f" % area_m2))
    # north point (+Y is north unless told otherwise)
    nx, ny = page_w - margin - 6, margin + title_h + 6
    svg.append('<polygon points="%g,%g %g,%g %g,%g" fill="#000"/>' % (nx, ny - 6, nx - 3, ny + 3, nx + 3, ny + 3))
    svg.append('<text x="%g" y="%g" font-size="3.4" font-family="%s" text-anchor="middle">N</text>' % (nx, ny + 8, SVG_FONT))
    svg.append('<text x="%g" y="%g" font-size="9" font-weight="900" font-family="%s">ISOVIST</text>' % (margin, margin + 7, SVG_FONT))
    svg.append('<text x="%g" y="%g" font-size="3.4" font-family="%s" text-anchor="end">eye height %.1f m  -  1:%d</text>'
               % (page_w - margin, margin + 7, SVG_FONT, d.get("eye_m", 1.6), n))
    svg.append(scale_bar(margin, page_h - margin + 2, n, 1000.0))
    svg.append("</svg>")
    base = os.path.join(out, d.get("name", "isovist"))
    with open(base + ".svg", "w", encoding="utf-8") as fh:
        fh.write("\n".join(svg))
    svg_to_png(base + ".svg", base + ".png", dpi=150)
    if d.get("capture") and d.get("overlay_px"):
        img = Image.open(d["capture"]).convert("RGBA")
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(over)
        for i, ov in enumerate(d["overlay_px"]):
            r, g, b = PIN_COLS[i % len(PIN_COLS)]
            if len(ov["poly_px"]) > 2:
                dr.polygon([tuple(p) for p in ov["poly_px"]], fill=(r, g, b, 70), outline=(r, g, b, 255))
            x, y = ov["origin_px"]
            dr.ellipse([x - 10, y - 10, x + 10, y + 10], fill=(r, g, b, 255), outline=(0, 0, 0, 255), width=2)
        Image.alpha_composite(img, over).convert("RGB").save(base + " - on view.png")
    print("isovist: %s.svg / .png at 1:%d" % (base, n))


# ---------------------------------------------------------------- photo match

def overlay(photo, edges_json, out):
    """The solved camera's visible model edges (from tools/cm_analyse.py photo_edges) drawn over the photo
    in cyan with a dark casing: where they sit off the photo's edges, the camera is off."""
    ph = Image.open(photo).convert("RGB")
    d = load(edges_json)
    W, H = d["image_size"]
    sx, sy = ph.width / float(W), ph.height / float(H)
    dim = ImageEnhance.Brightness(ph).enhance(0.8)
    dr = ImageDraw.Draw(dim)
    lw = max(2, int(round(ph.width / 900.0)))
    for seg in d["segments"]:
        pts = [(x * sx, y * sy) for x, y in seg]
        if len(pts) > 1:
            dr.line(pts, fill=(0, 0, 0), width=lw + 2)
    for seg in d["segments"]:
        pts = [(x * sx, y * sy) for x, y in seg]
        if len(pts) > 1:
            dr.line(pts, fill=(56, 215, 255), width=lw)
    dim.save(out)
    print("overlay: %s (%d edges; mapping residual %s px)" % (out, len(d["segments"]),
          "%.3f" % d["mapping_residual_px"] if d.get("mapping_residual_px") is not None else "?"))


def montage(photo, render, out, mask=None):
    """The proposal render (RGBA, background transparent) over the photo. mask (white = visible) cuts out
    the pixels that existing context hides (tools/cm_analyse.py montage_occlusion)."""
    ph = Image.open(photo).convert("RGBA")
    rd = Image.open(render).convert("RGBA")
    if mask and os.path.exists(mask):
        m = Image.open(mask).convert("L").resize(rd.size, Image.NEAREST)
        a = np.minimum(np.asarray(rd.getchannel("A"), dtype=np.uint16), np.asarray(m, dtype=np.uint16)).astype(np.uint8)
        rd.putalpha(Image.fromarray(a))
    rd = rd.resize(ph.size, Image.LANCZOS)
    res = Image.alpha_composite(ph, rd).convert("RGB")
    res.save(out, quality=94)
    print("montage:", out)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd in ("section", "sun", "shadows", "isovist"):
        os.makedirs(argv[2], exist_ok=True)
        globals()[cmd](argv[1], argv[2])
    elif cmd == "overlay":
        overlay(argv[1], argv[2], argv[3])
    elif cmd == "montage":
        montage(argv[1], argv[2], argv[3], argv[4] if len(argv) > 4 else None)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
