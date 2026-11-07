"""Render styles from a reference: read its light and grade, write V-Ray style recipes, grade real V-Ray previews,
post them as a ClaudeReply board, and turn the picked one into real settings (CPython 3: numpy, PIL, scipy).

    python cm_style.py read    <reference image> <out folder>              -> ref_read.json + ref_read.png (a read sheet)
    python cm_style.py resolve <capture> <styles.json>                     -> fills each style's sun: camera-relative
                                                                              bearings to azimuths, site-true dates and times
    python cm_style.py sun     <lat> <lon> <tz> <alt> <az> [--year Y]      -> the dates and times that give that sun
    python cm_style.py vropt   <base.vropt> <styles.json> <label> <out.vropt> [--lightmix]
    python cm_style.py fit     <ref_read.json> <preview.png> <out grade.json> [--strength S]
    python cm_style.py grade   <in.png> <grade.json> <out.png> [--look-only]
    python cm_style.py cube    <grade.json> <out.cube> [--look-only]
    python cm_style.py board   <capture> <styles.json> <previews folder> <out folder>
    python cm_style.py compare <a.png> <b.png>                             -> mean dE: does the final match the preview?
    python cm_style.py post    <capture> <out folder>
    python cm_style.py bake    <styles.json> <label> <grade.json> <out styles.json>

styles.json:
{
  "title": "Looks from R1",
  "reference": "C:/.../ref1_original.jpg",
  "site": {"lat": -37.832, "lon": 144.957, "tz": 10.0},            # standard time; clock labels add Victorian DST
  "styles": [
    {"label": "A", "title": "Faithful", "idea": "R1's low warm sun from behind-left, hazy, lifted blacks",
     "sun":    {"date": "2026-03-21", "time": "16:40"}              # standard time at the site, or
               {"alt": 12, "az": 290} | {"alt": 12, "rel": -120, "site_true": true} | {"off": true},
                                                                    # rel: bearing from the view direction, 0 = into
                                                                    # the sun, 90 = from the right, 180 = behind you
     "sky":    {"model": 5, "turbidity": 3, "ozone": 0.35, "intensity": 1.0, "size": 1.0, "filter": [1, 1, 1],
                "water_vapour": 2, "clouds": {"density": 0.5, "variety": 0.3, "cirrus": 0.2},
                "raw": {"horiz_illum": 25000}},                     # any other SunLight param
     "env":    "sky" | "keep" | {"hdri": "C:/.../x.hdr", "intensity": 1.0},
     "fog":    {"distance_m": 2500, "inscat": 1.0, "filter": [1, 1, 1]},  # aerial perspective in metres; null = off
     "camera": {"ev100": 12.5, "f": 8, "wb_K": 5600, "vignetting": 0.3},
     "tonemap": {"burn": 0.85},                                      # Reinhard burn: 1 = linear, lower rolls highlights
     "grade":  "fit" | {"look": "A", "median_L": 38} | null,         # fit to the reference / A's look metered to
                                                                    # its own exposure / none
     "volfog": {"distance_m": 60, "height_m": 16, "phase": 0.3},    # volumetric fog the lights glow in (slow)
     "post":   {"bloom": 0.3, "bloom_radius": 0.02},                # post-only extras merged into the grade
     "rig":    {"green_truss": 1.0, "amber_brick": 0.5},            # this style's multipliers on the shared rig
     "vray":   {"/SettingsGI": {"some_param": 1}}}                   # any other V-Ray setting, by plugin and param
  ],
  "rig": [{"group": "green_truss", "colour": [0.18, 1, 0.06], "luminance": 4000, "invisible": true,
           "strips": [{"from": [x, y, z], "to": [x, y, z], "width": 250, "normal": [0, 0.3, 0.95]}],
           "panels": [{"corners": [[x, y, z], ...4], "normal": [0, 0, -1]}]}]  # temporary mesh lights (cm_render rig_*)
}
sky and sun go to V-Ray's /Rhino Document Sun from Rhino (cm_render.style_light); everything else is a .vropt edit.

The grade is split in two. Exposure and white balance are physical: `bake` folds the fitted values into the
camera, so V-Ray renders them for real. The look (tone curve, saturation, shadow and highlight tints) is a post
grade, applied here and written as a .cube LUT for the user’s VFB, Photoshop or Premiere. V-Ray's frame-buffer layers
can't be set by script (a SettingsVFB vfb2_layers string loaded through a .vropt is ignored, tested 2026-10-18).
"""
import datetime
import json
import math
import os
import shutil
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = r"C:\Windows\Fonts"
CM_ROOT = os.environ.get("CLAUDEMARK_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def font(size, bold=True):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, "ARIALNB.TTF" if bold else "ARIALN.TTF"), int(size))
    except Exception:
        return ImageFont.load_default()


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)


# ---------------------------------------------------------------- colour

M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                      [0.2126729, 0.7151522, 0.0721750],
                      [0.0193339, 0.1191920, 0.9503041]])
M_XYZ2RGB = np.linalg.inv(M_RGB2XYZ)
WHITE = np.array([0.95047, 1.0, 1.08883])
LUMA = np.array([0.2126729, 0.7151522, 0.0721750])


def srgb_to_lin(x):
    x = np.asarray(x, dtype=np.float64)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(y):
    y = np.clip(np.asarray(y, dtype=np.float64), 0.0, None)
    return np.where(y <= 0.0031308, 12.92 * y, 1.055 * np.power(y, 1 / 2.4) - 0.055)


def lin_to_lab(lin):
    xyz = lin @ M_RGB2XYZ.T / WHITE
    f = np.where(xyz > 216 / 24389.0, np.cbrt(xyz), (24389 / 27.0 * xyz + 16) / 116.0)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def lab_to_lin(lab):
    fy = (lab[..., 0] + 16) / 116.0
    fx = fy + lab[..., 1] / 500.0
    fz = fy - lab[..., 2] / 200.0
    f = np.stack([fx, fy, fz], -1)
    xyz = np.where(f ** 3 > 216 / 24389.0, f ** 3, (116 * f - 16) / (24389 / 27.0)) * WHITE
    return xyz @ M_XYZ2RGB.T


def kelvin_rgb(K):
    """Linear sRGB of a black body at K (Kim et al. 2002 locus), luminance 1."""
    T = float(min(max(K, 1667.0), 25000.0))
    if T <= 4000:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    xyz = np.array([x / y, 1.0, (1 - x - y) / y])
    rgb = np.clip(M_XYZ2RGB @ xyz, 1e-4, None)
    return rgb / float(LUMA @ rgb)


def cct(lin_rgb):
    """Correlated colour temperature (McCamy) of a linear sRGB colour."""
    X, Y, Z = M_RGB2XYZ @ np.asarray(lin_rgb, dtype=np.float64)
    s = X + Y + Z
    if s <= 0:
        return None
    x, y = X / s, Y / s
    n = (x - 0.3320) / (0.1858 - y)
    return float(449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33)


# ---------------------------------------------------------------- images

def load_rgb(path, max_side=None):
    """Float sRGB [0,1] h x w x 3 and a mask of real pixels. A V-Ray PNG keeps the sky in RGB under alpha 0, so
    alpha only masks pixels that are black and transparent (nothing behind them)."""
    im = Image.open(path)
    alpha = im.getchannel("A") if "A" in im.getbands() else None
    im = im.convert("RGB")                 # before resizing: PIL premultiplies RGBA and would black out the sky
    if max_side and max(im.size) > max_side:
        k = max_side / float(max(im.size))
        size = (max(1, int(im.size[0] * k)), max(1, int(im.size[1] * k)))
        im = im.resize(size, Image.LANCZOS)
        alpha = alpha.resize(size, Image.BILINEAR) if alpha is not None else None
    rgb = np.asarray(im).astype(np.float64) / 255.0
    al = np.asarray(alpha).astype(np.float64) / 255.0 if alpha is not None else np.ones(rgb.shape[:2])
    mask = (al > 0.5) | (rgb.max(-1) > 0.02)
    return rgb, mask


def save_rgb(path, rgb):
    Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path)


BANDS = ((0, 35), (35, 70), (70, 101))


def stats(rgb, mask):
    """What the eye reads as a render's look: tone, contrast, clipping, saturation, the tint of its neutrals."""
    lin = srgb_to_lin(rgb)
    lab = lin_to_lab(lin)
    L, a, b = lab[..., 0][mask], lab[..., 1][mask], lab[..., 2][mask]
    C = np.hypot(a, b)
    Y = (lin @ LUMA)[mask]
    pct = np.percentile(L, [1, 5, 10, 25, 50, 75, 90, 95, 99])
    out = {"L_pct": [round(float(v), 2) for v in pct],
           "median_Y": float(np.median(Y)),
           "range_stops": round(float(np.log2(max(np.percentile(Y, 95), 1e-5) / max(np.percentile(Y, 5), 1e-5))), 2),
           "clip_high": round(float(np.mean(rgb[mask].max(-1) > 0.995)), 4),
           "clip_low": round(float(np.mean(L < 2)), 4)}
    mid = (L > 25) & (L < 85)
    out["chroma_mid"] = round(float(C[mid].mean()) if mid.any() else 0.0, 2)
    tints, neutral = [], []
    lin_m = lin[mask]
    for lo, hi in BANDS:
        sel = (L >= lo) & (L < hi)
        if sel.sum() < 50:
            tints.append([0.0, 0.0])
            neutral.append(None)
            continue
        q = np.percentile(C[sel], 35)
        n = sel & (C <= q)
        tints.append([round(float(a[n].mean()), 2), round(float(b[n].mean()), 2)])
        neutral.append([float(v) for v in lin_m[n].mean(0)])
    out["tint"] = tints                     # [a*, b*] of the least-coloured third of shadows, mids, highlights
    out["neutral_lin"] = neutral
    hi = neutral[2] or neutral[1]
    out["cct_high"] = round(cct(hi)) if hi is not None and cct(hi) else None
    h, w = rgb.shape[:2]
    Lfull = lab[..., 0]
    cy, cx = int(h * 0.12), int(w * 0.12)
    corners = np.concatenate([Lfull[:cy, :cx].ravel(), Lfull[:cy, -cx:].ravel(),
                              Lfull[-cy:, :cx].ravel(), Lfull[-cy:, -cx:].ravel()])
    centre = Lfull[int(h * .35):int(h * .65), int(w * .35):int(w * .65)]
    out["vignette_dL"] = round(float(centre.mean() - corners.mean()), 2)
    top = lab[:int(h * 0.18)].reshape(-1, 3)
    out["top_lab"] = [round(float(v), 1) for v in top.mean(0)]
    return out


def palette(rgb, mask, k=6):
    lab = lin_to_lab(srgb_to_lin(rgb))[mask]
    rng = np.random.default_rng(0)
    pts = lab[rng.choice(len(lab), min(len(lab), 20000), replace=False)]
    cen = pts[rng.choice(len(pts), k, replace=False)]
    for _ in range(25):
        d = ((pts[:, None, :] - cen[None]) ** 2).sum(-1)
        lab_i = d.argmin(1)
        cen = np.array([pts[lab_i == j].mean(0) if (lab_i == j).any() else cen[j] for j in range(k)])
    share = np.bincount(lab_i, minlength=k) / float(len(pts))
    order = np.argsort(cen[:, 0])
    srgb = lin_to_srgb(lab_to_lin(cen[order]))
    return [{"rgb": [int(round(v * 255)) for v in np.clip(c, 0, 1)], "share": round(float(s), 3)}
            for c, s in zip(srgb, share[order])]


def read(ref, out):
    """Read a reference: numbers for the recipe, and a sheet to look at beside it."""
    os.makedirs(out, exist_ok=True)
    rgb, mask = load_rgb(ref, 1000)
    st = stats(rgb, mask)
    st["palette"] = palette(rgb, mask)
    st["source"] = ref
    st["size"] = list(Image.open(ref).size)
    save_json(os.path.join(out, "ref_read.json"), st)
    # the sheet: reference, L* histogram with percentiles, palette, the numbers
    W = 1500
    im = Image.fromarray((rgb * 255).astype(np.uint8))
    iw = 960
    ih = int(im.size[1] * iw / float(im.size[0]))
    sheet = Image.new("RGB", (W, max(ih, 760) + 40), (250, 250, 248))
    sheet.paste(im.resize((iw, ih), Image.LANCZOS), (20, 20))
    dr = ImageDraw.Draw(sheet)
    x0, y0, hw, hh = iw + 50, 30, W - iw - 80, 150
    L = lin_to_lab(srgb_to_lin(rgb))[..., 0][mask]
    hist, _ = np.histogram(L, bins=100, range=(0, 100))
    hist = hist / float(hist.max())
    for i, v in enumerate(hist):
        x = x0 + i * hw / 100.0
        dr.rectangle([x, y0 + hh * (1 - v), x + hw / 100.0, y0 + hh], fill=(60, 60, 60))
    for p in st["L_pct"]:
        x = x0 + p * hw / 100.0
        dr.line([x, y0 + hh, x, y0 + hh + 8], fill=(220, 60, 30), width=2)
    dr.text((x0, y0 + hh + 12), "L*  1/5/10/25/50/75/90/95/99%", font=font(18, False), fill=(90, 90, 90))
    y = y0 + hh + 46
    sw = hw / float(len(st["palette"]))
    for i, p in enumerate(st["palette"]):
        dr.rectangle([x0 + i * sw, y, x0 + (i + 1) * sw - 4, y + 60], fill=tuple(p["rgb"]))
        dr.text((x0 + i * sw, y + 64), "%d%%" % round(100 * p["share"]), font=font(16, False), fill=(90, 90, 90))
    y += 100
    tint = st["tint"]
    rows = [("median L*", "%.0f" % st["L_pct"][4]), ("contrast L*10-90", "%.0f" % (st["L_pct"][6] - st["L_pct"][2])),
            ("black / white L*", "%.0f / %.0f" % (st["L_pct"][0], st["L_pct"][8])),
            ("range 5-95%", "%.1f stops" % st["range_stops"]),
            ("clipped high / low", "%.1f%% / %.1f%%" % (100 * st["clip_high"], 100 * st["clip_low"])),
            ("chroma (mids)", "%.0f" % st["chroma_mid"]),
            ("highlight colour", "%s K" % st["cct_high"]),
            ("shadow tint a*,b*", "%+.1f, %+.1f" % tuple(tint[0])),
            ("mid tint a*,b*", "%+.1f, %+.1f" % tuple(tint[1])),
            ("highlight tint a*,b*", "%+.1f, %+.1f" % tuple(tint[2])),
            ("vignette", "%+.0f L* centre vs corners" % st["vignette_dL"]),
            ("top of frame L*a*b*", "%.0f, %+.0f, %+.0f" % tuple(st["top_lab"]))]
    for k, v in rows:
        dr.text((x0, y), k, font=font(20, False), fill=(90, 90, 90))
        dr.text((x0 + 220, y), v, font=font(20), fill=(20, 20, 20))
        y += 30
    sheet.save(os.path.join(out, "ref_read.png"))
    print(json.dumps({k: st[k] for k in ("L_pct", "range_stops", "clip_high", "chroma_mid", "cct_high", "tint",
                                         "vignette_dL", "top_lab")}))
    print("sheet:", os.path.join(out, "ref_read.png"))
    return st


# ---------------------------------------------------------------- the grade

IDENTITY = {"exposure": 0.0, "wb": [1.0, 1.0, 1.0], "gamma": 1.0, "contrast": 0.0, "black": 0.0, "white": 1.0,
            "saturation": 1.0, "tint_shadow": [0.0, 0.0], "tint_mid": [0.0, 0.0], "tint_high": [0.0, 0.0],
            "bloom": 0.0, "bloom_radius": 0.02}


def tone(t, g):
    t = np.clip(t, 0, 1) ** g["gamma"]
    t = t - g["contrast"] * np.sin(2 * np.pi * t) / (2 * np.pi)      # an S (contrast > 0) that keeps 0, 0.5 and 1
    return g["black"] + (g["white"] - g["black"]) * t


def apply_grade(rgb, g, look_only=False):
    g = dict(IDENTITY, **(g or {}))
    lin = srgb_to_lin(rgb)
    if not look_only:
        lin = lin * (2.0 ** g["exposure"]) * np.asarray(g["wb"])
    lab = lin_to_lab(np.clip(lin, 0, 1))
    t = tone(lab[..., 0] / 100.0, g)
    lab[..., 0] = 100 * t
    ws, wm, wh = (1 - t) ** 2, 2 * t * (1 - t), t ** 2           # shadows, mids, highlights (they sum to 1)
    for c in (1, 2):
        lab[..., c] = lab[..., c] * g["saturation"] + ws * g["tint_shadow"][c - 1] + wm * g["tint_mid"][c - 1]             + wh * g["tint_high"][c - 1]
    out = lab_to_lin(lab)
    if g["bloom"] > 0 and out.ndim == 3 and out.shape[0] > 8:
        # glow around bright sources, like the VFB's Lens Effects bloom (which can't be set by script): the light
        # above a threshold, blurred wide, added back. Set per style ("post": {"bloom": 0.3}); never fitted.
        from scipy.ndimage import gaussian_filter
        hot = np.clip(out - 0.6, 0, None)
        sig = max(1.0, g["bloom_radius"] * max(out.shape[:2]))
        glow = np.stack([gaussian_filter(hot[..., c], sig) + 0.5 * gaussian_filter(hot[..., c], sig / 4) for c in range(3)], -1)
        out = out + g["bloom"] * glow
    return np.clip(lin_to_srgb(out), 0, 1)


def fit(ref_read, preview, out, strength=1.0):
    """Fit the grade that takes a V-Ray preview toward the reference. Physical first: white balance from the
    highlights (the colour of the light) and exposure from the median (bake both into the camera). Then the look:
    a gentle tone curve, saturation, and a tint per band (split toning).

    Tone percentiles depend on content as well as style (a dark building in the reference is not a grade), so
    the curve is held near identity and only its ends (black and white points) are matched closely."""
    from scipy.optimize import least_squares
    ref = load_json(ref_read) if isinstance(ref_read, str) else ref_read
    rgb, mask = load_rgb(preview, 1000)
    before = stats(rgb, mask)
    g = dict(IDENTITY)
    g["exposure"] = float(np.clip(np.log2(max(ref["median_Y"], 1e-5) / max(before["median_Y"], 1e-5)), -3, 3))
    rn, pn = (ref["neutral_lin"][2] or ref["neutral_lin"][1]), (before["neutral_lin"][2] or before["neutral_lin"][1])
    if rn and pn:
        rn, pn = np.asarray(rn), np.asarray(pn)
        gains = (rn / (LUMA @ rn)) / (pn / (LUMA @ pn))
        g["wb"] = [float(v) for v in np.clip(gains / (LUMA @ gains), 0.7, 1.4)]
    mid = stats(apply_grade(rgb, g), mask)
    p_src = np.asarray(mid["L_pct"]) / 100.0
    p_ref = np.asarray(ref["L_pct"]) / 100.0
    w = np.array([3, 1, 0.5, 0.5, 0.5, 0.5, 0.5, 1, 3])          # the ends are style; the middle is mostly content

    def resid(x):
        gg = dict(g, gamma=x[0], contrast=x[1], black=x[2], white=x[3])
        return np.concatenate([w * (tone(p_src, gg) - p_ref), 0.4 * (x - np.array([1, 0, 0, 1]))])
    sol = least_squares(resid, [1.0, 0.0, 0.0, 1.0], bounds=([0.7, -0.35, 0.0, 0.8], [1.4, 0.35, 0.12, 1.0]))
    g["gamma"], g["contrast"], g["black"], g["white"] = [float(v) for v in sol.x]
    st2 = stats(apply_grade(rgb, g), mask)
    if st2["chroma_mid"] > 3 and ref["chroma_mid"] > 0:
        g["saturation"] = float(np.clip(ref["chroma_mid"] / st2["chroma_mid"], 0.6, 1.5))
    keys = ("tint_shadow", "tint_mid", "tint_high")
    for _ in range(4):                                            # the bands overlap: correct a few times
        st2 = stats(apply_grade(rgb, g), mask)
        for band, key in enumerate(keys):
            d = np.asarray(ref["tint"][band]) - np.asarray(st2["tint"][band])
            g[key] = [float(v) for v in np.clip(np.asarray(g[key]) + d, -12, 12)]
    if strength < 1.0:
        for k, v in IDENTITY.items():
            if isinstance(v, list):
                g[k] = [v0 + strength * (x - v0) for v0, x in zip(v, g[k])]
            else:
                g[k] = v + strength * (g[k] - v)
    after = stats(apply_grade(rgb, g), mask)

    def dist(s):
        return {"L_pct_rms": round(float(np.sqrt(np.mean((np.asarray(s["L_pct"]) - np.asarray(ref["L_pct"])) ** 2))), 2),
                "black_white": [round(s["L_pct"][0] - ref["L_pct"][0], 1), round(s["L_pct"][8] - ref["L_pct"][8], 1)],
                "tints": [[round(x - y, 1) for x, y in zip(s["tint"][b], ref["tint"][b])] for b in range(3)],
                "chroma_mid": round(s["chroma_mid"] - ref["chroma_mid"], 1)}
    g["fit"] = {"preview": preview, "strength": strength, "before": dist(before), "after": dist(after),
                "clip_high_after": after["clip_high"]}
    save_json(out, g)
    print("exposure %+.2f stops, wb %s, gamma %.2f, contrast %+.2f, black %.3f, white %.3f, sat %.2f, tints %s"
          % (g["exposure"], [round(v, 3) for v in g["wb"]], g["gamma"], g["contrast"], g["black"], g["white"],
             g["saturation"], [[round(v, 1) for v in g[k]] for k in keys]))
    print("distance to reference: before", g["fit"]["before"])
    print("                       after ", g["fit"]["after"])
    return g


def write_cube(g, out, look_only=True, n=33):
    """The grade as a 3D LUT (.cube, sRGB in and out): the user’s VFB's LUT layer, Photoshop Color Lookup, Premiere."""
    v = np.linspace(0, 1, n)
    b, gg, r = np.meshgrid(v, v, v, indexing="ij")          # red runs fastest in a .cube
    grid = np.stack([r, gg, b], -1).reshape(-1, 1, 3)
    res = apply_grade(grid, g, look_only).reshape(-1, 3)
    with open(out, "w") as fh:
        fh.write('TITLE "ClaudeMark %s"\nLUT_3D_SIZE %d\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n'
                 % (os.path.splitext(os.path.basename(out))[0], n))
        for c in res:
            fh.write("%.6f %.6f %.6f\n" % tuple(c))
    return out


LOOK_KEYS = ("gamma", "contrast", "black", "white", "saturation", "tint_shadow", "tint_mid", "tint_high")


def _median_L(rgb, mask, g):
    lab = lin_to_lab(np.clip(srgb_to_lin(apply_grade(rgb[::2, ::2], g)), 0, 1))
    return float(np.median(lab[..., 0][mask[::2, ::2]]))


def grade_for(style, previews, rgb=None, mask=None):
    """A style's grade, saved as previews/grade_<label>.json (what `bake` reads):
       "fit"                         the file `fit` wrote (fitted to the reference)
       {"look": "A", "median_L": 38}  A's look (tone, saturation, tints) with this style's own exposure, metered so
                                     the median L* lands on the target; any IDENTITY key may be given outright
       null                          no grade."""
    gsrc = style.get("grade")
    path = os.path.join(previews, "grade_%s.json" % style["label"])
    if not gsrc:
        return None
    if gsrc == "fit":
        if not os.path.exists(path):
            return None
        g = load_json(path)
        g.update(style.get("post") or {})
        return g
    g = dict(IDENTITY)
    if gsrc.get("look"):
        # Only the tone curve and a capped saturation carry over. The other style's tints (and its saturation, fitted
        # on its own light) are corrections for THAT light against the reference: on another light they go garish
        # (A's look turned blue hour saturated blue and overcast highlights cyan, 2026-10-18). "tints": true forces them.
        look = load_json(os.path.join(previews, "grade_%s.json" % gsrc["look"]))
        g.update(dict((k, look[k]) for k in ("gamma", "contrast", "black", "white")))
        g["saturation"] = float(np.clip(look.get("saturation", 1.0), 0.85, 1.15))
        if gsrc.get("tints"):
            g.update(dict((k, look[k]) for k in ("tint_shadow", "tint_mid", "tint_high")))
    g.update(dict((k, v) for k, v in gsrc.items() if k in IDENTITY))
    if gsrc.get("median_L") is not None and rgb is not None:
        lo, hi = -5.0, 5.0
        for _ in range(22):
            g["exposure"] = 0.5 * (lo + hi)
            if _median_L(rgb, mask, g) < gsrc["median_L"]:
                lo = g["exposure"]
            else:
                hi = g["exposure"]
        g["metered"] = {"median_L": gsrc["median_L"], "exposure": round(g["exposure"], 2)}
    g.update(style.get("post") or {})
    g["source"] = gsrc
    save_json(path, g)
    return g


# ---------------------------------------------------------------- the sun

def solar_position(lat, lon, tz, year, doy, minutes):
    """NOAA solar position. doy and minutes (local standard time) may be numpy arrays. -> altitude, azimuth (deg,
    azimuth clockwise from north), no refraction."""
    doy = np.asarray(doy, dtype=np.float64)
    minutes = np.asarray(minutes, dtype=np.float64)
    jd = (datetime.date(year, 1, 1).toordinal() + 1721424.5) + (doy - 1) + (minutes / 60.0 - tz) / 24.0
    jc = (jd - 2451545.0) / 36525.0
    L0 = np.mod(280.46646 + jc * (36000.76983 + jc * 0.0003032), 360)
    M = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    Mr = np.radians(M)
    C = np.sin(Mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + np.sin(2 * Mr) * (0.019993 - 0.000101 * jc) \
        + np.sin(3 * Mr) * 0.000289
    true_long = L0 + C
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * np.sin(np.radians(omega))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * np.cos(np.radians(omega))
    decl = np.degrees(np.arcsin(np.sin(np.radians(eps)) * np.sin(np.radians(app_long))))
    y = np.tan(np.radians(eps / 2)) ** 2
    L0r = np.radians(L0)
    eqt = 4 * np.degrees(y * np.sin(2 * L0r) - 2 * e * np.sin(Mr) + 4 * e * y * np.sin(Mr) * np.cos(2 * L0r)
                         - 0.5 * y * y * np.sin(4 * L0r) - 1.25 * e * e * np.sin(2 * Mr))
    tst = np.mod(minutes + eqt + 4 * lon - 60 * tz, 1440)
    ha = np.where(tst / 4 < 0, tst / 4 + 180, tst / 4 - 180)
    latr, dr_, har = np.radians(lat), np.radians(decl), np.radians(ha)
    cz = np.clip(np.sin(latr) * np.sin(dr_) + np.cos(latr) * np.cos(dr_) * np.cos(har), -1, 1)
    zen = np.degrees(np.arccos(cz))
    sz = np.sin(np.radians(zen))
    with np.errstate(invalid="ignore", divide="ignore"):
        caz = (np.sin(latr) * cz - np.sin(dr_)) / (np.cos(latr) * sz)
    az = np.degrees(np.arccos(np.clip(caz, -1, 1)))
    az = np.where(ha > 0, np.mod(az + 180, 360), np.mod(540 - az, 360))
    return 90 - zen, az


def victoria_dst(d):
    """Daylight time in Victoria: first Sunday of October to first Sunday of April."""
    def first_sunday(y, m):
        x = datetime.date(y, m, 1)
        return x + datetime.timedelta(days=(6 - x.weekday()) % 7)
    return d >= first_sunday(d.year, 10) or d < first_sunday(d.year, 4)


def solve_sun(lat, lon, tz, alt, az, year=None, n=2):
    """Dates and local standard times at the site that give a sun at (alt, az). The same position comes twice a
    year (either side of a solstice): both are returned, with the angular miss in degrees."""
    year = year or datetime.date.today().year
    days = np.arange(1, 366)
    mins = np.arange(4 * 60, 21 * 60 + 1, 2)
    D, Mi = np.meshgrid(days, mins, indexing="ij")
    A, Z = solar_position(lat, lon, tz, year, D, Mi)
    a0, z0 = np.radians(alt), np.radians(az)
    a1, z1 = np.radians(A), np.radians(Z)
    cosd = np.sin(a0) * np.sin(a1) + np.cos(a0) * np.cos(a1) * np.cos(z1 - z0)
    miss = np.degrees(np.arccos(np.clip(cosd, -1, 1)))
    miss[A < min(-2.0, alt - 3.0)] = 999          # twilight and night targets search below the horizon too
    out = []
    order = np.argsort(miss, axis=None)
    for k in order:
        i, j = np.unravel_index(k, miss.shape)
        d = datetime.date(year, 1, 1) + datetime.timedelta(days=int(D[i, j]) - 1)
        if any(abs((d - o["_d"]).days) < 40 for o in out):
            continue
        m = int(Mi[i, j])
        dst = victoria_dst(d) if lat < -30 and 140 < lon < 150 else False
        clock = m + (60 if dst else 0)
        out.append({"_d": d, "date": d.isoformat(), "time": "%02d:%02d" % (m // 60, m % 60),
                    "clock": "%d:%02d %s%s" % (((clock // 60) - 1) % 12 + 1, clock % 60, "am" if clock < 720 else "pm",
                                               " daylight time" if dst else ""),
                    "alt": round(float(A[i, j]), 1), "az": round(float(Z[i, j]), 1),
                    "miss_deg": round(float(miss[i, j]), 2)})
        if len(out) >= n:
            break
    for o in out:
        o.pop("_d")
    return out


UNITS_PER_M = {"Millimeters": 1000.0, "Centimeters": 100.0, "Meters": 1.0, "Inches": 39.3701, "Feet": 3.28084}


def camera_heading(meta, north_deg=90.0):
    """The view direction as a compass bearing (clockwise from north; Rhino's North angle is from +X)."""
    d = meta["camera"]["direction"]
    return (north_deg - math.degrees(math.atan2(d[1], d[0]))) % 360.0


def resolve(capture, styles_path):
    """Turn each style's sun into what Rhino sets: camera-relative bearings become azimuths, and a site-true sun
    becomes the real date and time at the site (or the nearest one, with the miss reported)."""
    meta = load_json(os.path.join(capture, "meta.json"))
    spec = load_json(styles_path)
    site = spec.get("site") or {"lat": -37.832, "lon": 144.957, "tz": 10.0}
    north = float(site.get("north", 90.0))
    head = camera_heading(meta, north)
    for s in spec["styles"]:
        sun = s.get("sun") or {}
        if sun.get("off") or "date" in sun:
            continue
        if "rel" in sun:
            sun["az"] = round((head + float(sun["rel"])) % 360.0, 1)
        if sun.get("site_true") and "alt" in sun and "az" in sun:
            hits = solve_sun(site["lat"], site["lon"], site["tz"], sun["alt"], sun["az"], sun.get("year"))
            best = hits[0]
            sun["solved"] = hits
            if best["miss_deg"] <= 3.0:
                sun.update({"date": best["date"], "time": best["time"], "clock": best["clock"]})
            else:
                sun["warning"] = ("no date at the site gives a sun at %.0f deg up, bearing %.0f; nearest is %s %s "
                                  "(%.0f deg off)" % (sun["alt"], sun["az"], best["date"], best["clock"], best["miss_deg"]))
        s["sun"] = sun
        print(s["label"], json.dumps(sun))
    spec["camera_heading"] = round(head, 1)
    units = (meta.get("doc") or {}).get("units", "Millimeters")
    spec["units_per_m"] = UNITS_PER_M.get(str(units).split(".")[-1], 1000.0)
    save_json(styles_path, spec)
    return spec


# ---------------------------------------------------------------- V-Ray settings (.vropt)

def _fmt(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return ("%.6f" % v).rstrip("0").rstrip(".")
    return str(v)


class Vropt(object):
    """A V-Ray settings file (plain XML: <vropt><Asset url=...><plugin><vrayplugin><parameters>...)."""

    def __init__(self, path):
        self.tree = ET.parse(path)
        self.root = self.tree.getroot()

    def asset(self, url, plugin=None, kind="settings"):
        for a in self.root.findall("Asset"):
            if a.get("url") == url:
                return a
        if plugin is None:
            return None
        a = ET.SubElement(self.root, "Asset", {"renderer": "vray", "url": url, "type": kind, "layout": ""})
        p = ET.SubElement(a, "plugin")
        vp = ET.SubElement(p, "vrayplugin", {"name": plugin, "type": kind, "version": "23"})
        ET.SubElement(vp, "parameters")
        return a

    def get(self, url, name):
        a = self.asset(url)
        if a is None:
            return None
        for prm in a.iter("parameter"):
            if prm.get("name") == name:
                v = prm.find("value")
                if v is None:
                    return None
                if len(v):
                    return [float(c.text) for c in v]
                return v.text
        return None

    def set(self, url, name, value, ptype=None, userdata=False):
        a = self.asset(url)
        if a is None:
            raise KeyError("no %s in the settings file" % url)
        params = a.find("plugin/vrayplugin/parameters")
        if params is None:
            params = ET.SubElement(a.find("plugin/vrayplugin"), "parameters")
        prm = None
        for p in params.findall("parameter"):
            if p.get("name") == name:
                prm = p
                break
        if ptype is None:
            ptype = prm.get("type") if prm is not None else (
                "bool" if isinstance(value, bool) else "integer" if isinstance(value, int) else
                "float" if isinstance(value, float) else "color" if isinstance(value, (list, tuple)) else "string")
        if prm is None:
            attrs = {"handler": "default", "label": name, "name": name, "listType": "none", "type": ptype}
            if userdata:
                attrs["isUserData"] = "1"
            prm = ET.SubElement(params, "parameter", attrs)
        for c in list(prm):
            prm.remove(c)
        val = ET.SubElement(prm, "value")
        if ptype == "list":
            # V-Ray XML lists: <value><list><entry>/Plugin name</entry>...</list></value> (format from Cosmos .vrmat
            # files). Write lists here, never through rhVRay: a plugin list set live crashed the user’s Rhino (2026-10-18).
            lst = ET.SubElement(val, "list")
            for item in value:
                ET.SubElement(lst, "entry").text = str(item)
        elif ptype == "color":
            for k, c in zip("rgb", value):
                ET.SubElement(val, k).text = _fmt(float(c))
        elif ptype == "bool":
            val.text = "1" if value else "0"
        else:
            val.text = _fmt(value)

    def save(self, path):
        self.tree.write(path, encoding="utf-8", xml_declaration=False)


def ev_to_shutter(ev100, f):
    """Shutter speed (V-Ray's 1/s value) for EV100 at f-number f and ISO 100."""
    return (2.0 ** ev100) / (f * f)


def style_vropt(base, spec, label, out, lightmix=False):
    """One style's V-Ray settings: the user’s own settings (base, a SaveSettings snapshot) with this style's changes.
    LoadSettings merges, so the file carries every setting the style touches explicitly."""
    s = [x for x in spec["styles"] if x["label"] == label][0]
    v = Vropt(base)
    changed = []
    cam = s.get("camera") or {}
    if cam:
        f = float(cam.get("f", 8.0))
        v.set("/CameraPhysical", "exposure", True)
        v.set("/CameraPhysical", "ISO", 100.0)
        v.set("/CameraPhysical", "f_number", f)
        if "ev100" in cam:
            v.set("/CameraPhysical", "shutter_speed", round(ev_to_shutter(float(cam["ev100"]), f), 4))
        if "wb" in cam:
            v.set("/CameraPhysical", "white_balance", [float(c) for c in cam["wb"]], "color")
        elif "wb_K" in cam:
            v.set("/CameraPhysical", "white_balance", [round(float(c), 4) for c in kelvin_rgb(cam["wb_K"])], "color")
        if "vignetting" in cam:
            v.set("/CameraPhysical", "vignetting", float(cam["vignetting"]))
        changed.append("camera")
    tm = s.get("tonemap") or {}
    if "burn" in tm:
        v.set("/SettingsColorMapping", "type", 6)
        v.set("/SettingsColorMapping", "bright_mult", float(tm["burn"]))
        changed.append("tonemap")
    fog = s.get("fog")
    if fog:
        # V-Ray stores the distance in model units (300 in a mm model is a 0.3 m white-out, seen 2026-10-18)
        dist = float(fog["distance_m"]) * float(spec.get("units_per_m", 1000.0)) if "distance_m" in fog             else float(fog.get("distance", 3000))
        v.set("/VolumeAerialPerspective", "enabled", True)
        v.set("/VolumeAerialPerspective", "distance", dist)
        v.set("/VolumeAerialPerspective", "inscat_mult", float(fog.get("inscat", 1.0)))
        v.set("/VolumeAerialPerspective", "filter_color", [float(c) for c in fog.get("filter", [1, 1, 1])], "color")
        v.set("/VolumeAerialPerspective", "sun", "/Rhino Document Sun", "plugin")
        changed.append("fog")
    elif fog is None and "fog" in s and v.asset("/VolumeAerialPerspective") is not None:
        v.set("/VolumeAerialPerspective", "enabled", False)
    vf = s.get("volfog")
    upm = float(spec.get("units_per_m", 1000.0))
    if vf:
        # V-Ray's volumetric fog: the lights scatter in it (glowing air). Slow; distances in metres here.
        v.set("/EnvironmentFog", "enabled", True)
        v.set("/EnvironmentFog", "distance", float(vf.get("distance_m", 60)) * upm)
        v.set("/EnvironmentFog", "use_height", True)
        v.set("/EnvironmentFog", "height", float(vf.get("height_m", 16)) * upm)
        v.set("/EnvironmentFog", "color", [float(c) for c in vf.get("colour", [1, 1, 1])], "color")
        v.set("/EnvironmentFog", "phase_function", float(vf.get("phase", 0.3)))
        v.set("/EnvironmentFog", "affect_background", bool(vf.get("affect_background", False)))
        if vf.get("lights"):
            # the fog scatters only the lights it's given (mode 2 = this list); with none it only darkens
            v.set("/EnvironmentFog", "light_mode", 2)
            v.set("/EnvironmentFog", "lights", list(vf["lights"]), "list")
        changed.append("volumetric fog" + (" + %d lights" % len(vf["lights"]) if vf.get("lights") else ""))
    elif "volfog" in s and v.asset("/EnvironmentFog") is not None:
        v.set("/EnvironmentFog", "enabled", False)
    env = s.get("env", "keep")
    slots = ("bg_tex", "gi_tex", "reflect_tex", "refract_tex")
    if env == "sky":
        a = v.asset("/Environment Sky", "TexSky", "texture")
        v.set("/Environment Sky", "sun", "/Rhino Document Sun", "plugin")
        for k in slots:
            v.set("/SettingsEnvironment", k, "/Environment Sky", "plugin")
        v.set("/SettingsEnvironment", "bg_tex_tex", "/Environment Sky", "plugin", userdata=True)
        changed.append("env sky")
    elif isinstance(env, dict) and env.get("hdri"):
        v.set("/Environment Texture/BitmapBuffer", "file", env["hdri"].replace("/", "\\"), "string")
        for k in slots:
            v.set("/SettingsEnvironment", k, "/Environment Texture", "plugin")
            v.set("/SettingsEnvironment", k + "_mult", float(env.get("intensity", 1.0)), "float")
        v.set("/SettingsEnvironment", "bg_tex_tex", "/Environment Texture", "plugin", userdata=True)
        changed.append("env hdri")
    for url, params in (s.get("vray") or {}).items():
        for k, val in params.items():
            v.set(url, k, val)
        changed.append(url)
    if lightmix:
        v.asset("/RenderChannelLightMix", "RenderChannelLightMix", "render_channel")
        changed.append("lightmix")
    v.save(out)
    print("%s: %s -> %s" % (label, ", ".join(changed) or "nothing changed", out))
    return out


def bake(styles_path, label, grade_path, out):
    """Fold a fitted grade's exposure and white balance into the style's camera, so V-Ray renders them for real.
    The look (tone, saturation, tints) stays a post grade (.cube)."""
    spec = load_json(styles_path)
    g = load_json(grade_path)
    s = [x for x in spec["styles"] if x["label"] == label][0]
    cam = s.setdefault("camera", {})
    ev0 = float(cam.get("ev100", 12.0))
    cam["ev100"] = round(ev0 - g.get("exposure", 0.0), 2)            # +1 stop brighter = 1 EV lower
    w0 = np.asarray(cam["wb"]) if "wb" in cam else kelvin_rgb(cam.get("wb_K", 6500))
    w1 = w0 / np.asarray(g.get("wb", [1, 1, 1]))                     # V-Ray divides by the white point
    w1 = w1 / float(LUMA @ w1)
    cam["wb"] = [round(float(c), 4) for c in w1]
    cam.pop("wb_K", None)
    s["baked"] = {"from": grade_path, "ev_was": ev0, "exposure_added": round(g.get("exposure", 0.0), 2),
                  "wb_gains": g.get("wb")}
    s["grade_look"] = grade_path
    save_json(out, spec)
    print("%s: EV100 %.2f -> %.2f, white balance %s" % (label, ev0, cam["ev100"], cam["wb"]))
    return spec


# ---------------------------------------------------------------- board and post

def settings_line(s):
    bits = []
    sun = s.get("sun") or {}
    if sun.get("off"):
        bits.append("no sun")
    elif "alt" in sun or "date" in sun:
        when = (" %s %s" % (datetime.date.fromisoformat(sun["date"]).strftime("%d %b").lstrip("0"),
                            sun.get("clock") or sun.get("time"))) if sun.get("date") else ""
        bits.append(("sun %s deg up%s" % (int(round(sun["alt"])), when)) if "alt" in sun else "sun" + when)
        if sun.get("warning"):
            bits.append("not possible at the site")
    sky = s.get("sky") or {}
    if sky.get("clouds"):
        bits.append("clouds")
    if (sky.get("model") == 2):
        bits.append("overcast sky")
    if s.get("fog"):
        bits.append("haze %s m" % s["fog"].get("distance_m", "?"))
    cam = s.get("camera") or {}
    if "ev100" in cam:
        bits.append("EV %.1f" % cam["ev100"])
    if "wb_K" in cam:
        bits.append("WB %d K" % cam["wb_K"])
    return " | ".join(bits)


def compare(a, b):
    """How far apart two renders are: mean colour difference (CIE76 dE) and L* percentile rms, compared small."""
    A, ma = load_rgb(a, 480)
    B, mb = load_rgb(b, 480)
    if B.shape != A.shape:
        B = np.asarray(Image.fromarray((B * 255).astype(np.uint8)).resize((A.shape[1], A.shape[0]), Image.LANCZOS)) / 255.0
    la, lb = lin_to_lab(srgb_to_lin(A)), lin_to_lab(srgb_to_lin(B))
    dE = np.sqrt(((la - lb) ** 2).sum(-1))
    pa, pb = stats(A, ma)["L_pct"], stats(B, ma)["L_pct"]
    res = {"mean_dE": round(float(dE.mean()), 2), "p95_dE": round(float(np.percentile(dE, 95)), 2),
           "L_pct_rms": round(float(np.sqrt(np.mean((np.asarray(pa) - np.asarray(pb)) ** 2))), 2),
           "mean_Lab_a": [round(float(v), 1) for v in la.reshape(-1, 3).mean(0)],
           "mean_Lab_b": [round(float(v), 1) for v in lb.reshape(-1, 3).mean(0)]}
    print(json.dumps(res))
    return res


def board(capture, styles_path, previews, out):
    """Graded previews at capture size, each with the reference inset top right, plus a board to look at."""
    meta = load_json(os.path.join(capture, "meta.json"))
    spec = load_json(styles_path)
    W, H = [int(v) for v in meta["view"]["size"]]
    os.makedirs(out, exist_ok=True)
    ref = spec.get("reference")
    ref_im = Image.open(ref).convert("RGB") if ref and os.path.exists(ref) else None
    made = []
    for s in spec["styles"]:
        p = os.path.join(previews, "%s.denoiser.png" % s["label"])       # denoised, and free of the user’s VFB filters
        if not os.path.exists(p):
            p = os.path.join(previews, "%s.png" % s["label"])
        if not os.path.exists(p):
            print("no preview for", s["label"])
            continue
        rgb, mask = load_rgb(p)
        g = grade_for(s, previews, rgb, mask)
        if g:
            rgb = apply_grade(rgb, g)
        im = Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).resize((W, H), Image.LANCZOS)
        im.save(os.path.join(out, "%s_clean.png" % s["label"]))
        if ref_im is not None:
            iw = int(W * 0.2)
            ih = int(ref_im.size[1] * iw / float(ref_im.size[0]))
            inset = ref_im.resize((iw, ih), Image.LANCZOS)
            x, y = W - iw - 18, 18 + 46
            dr = ImageDraw.Draw(im)
            dr.rectangle([x - 4, y - 4, x + iw + 3, y + ih + 3], fill=(255, 255, 255))
            im.paste(inset, (x, y))
            dr.rectangle([x, y + ih - 26, x + 46, y + ih], fill=(0, 0, 0))
            dr.text((x + 8, y + ih - 24), "REF", font=font(18), fill=(255, 255, 255))
        name = "option_%s.png" % s["label"]
        im.save(os.path.join(out, name))
        made.append({"label": s["label"], "title": s.get("title", ""), "idea": s.get("idea", ""), "image": name,
                     "settings": settings_line(s), "graded": bool(g)})
        print("option %s: %s" % (s["label"], settings_line(s)))
    tw = 760
    th = int(round(H * tw / float(W)))
    gap, head, foot = 18, 70, 64
    cols = len(made) + (1 if ref_im is not None else 0)
    bd = Image.new("RGB", (cols * tw + (cols + 1) * gap, head + th + foot + gap), (255, 255, 255))
    dr = ImageDraw.Draw(bd)
    dr.text((gap, 16), (spec.get("title") or "RENDER STYLES").upper(), font=font(40), fill=(0, 0, 0))
    k = 0
    if ref_im is not None:
        r = ref_im.copy()
        r.thumbnail((tw, th), Image.LANCZOS)
        bd.paste(r, (gap + (tw - r.size[0]) // 2, head + (th - r.size[1]) // 2))
        dr.text((gap, head + th + 8), "REFERENCE", font=font(24), fill=(0, 0, 0))
        k = 1
    for m in made:
        x = gap + k * (tw + gap)
        bd.paste(Image.open(os.path.join(out, "%s_clean.png" % m["label"])).resize((tw, th), Image.LANCZOS), (x, head))
        dr.text((x, head + th + 8), "%s  %s" % (m["label"], m["title"].upper()), font=font(24), fill=(0, 0, 0))
        dr.text((x, head + th + 36), m["settings"], font=font(18, False), fill=(80, 80, 80))
        k += 1
    bd.save(os.path.join(out, "board.png"))
    save_json(os.path.join(out, "styles_rendered.json"),
              {"title": spec.get("title", "Render styles"), "options": made, "capture": capture,
               "styles": os.path.abspath(styles_path), "previews": os.path.abspath(previews)})
    print("board:", os.path.join(out, "board.png"))
    return made


def post(capture, out):
    """Put the graded previews in the outbox as a ClaudeReply options board in style mode."""
    root = os.path.join(CM_ROOT, "outbox")
    meta = load_json(os.path.join(capture, "meta.json"))
    rendered = load_json(os.path.join(out, "styles_rendered.json"))
    rid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-styles"
    tmp = os.path.join(root, "." + rid)
    os.makedirs(tmp, exist_ok=True)
    shutil.copyfile(os.path.join(capture, "clean.png"), os.path.join(tmp, "clean.png"))
    for m in rendered["options"]:
        shutil.copyfile(os.path.join(out, m["image"]), os.path.join(tmp, m["image"]))
    shutil.copyfile(os.path.join(out, "board.png"), os.path.join(tmp, "marked.png"))
    keep = dict((k, meta[k]) for k in ("camera", "view", "doc", "cplane", "image", "clipping_planes") if k in meta)
    rec = {"reply_to": os.path.basename(os.path.normpath(capture)), "kind": "options", "mode": "style",
           "created": datetime.datetime.now().isoformat(), "title": rendered.get("title", "Render styles"),
           "options": rendered["options"], "styles": rendered.get("styles"), "previews": rendered.get("previews"),
           "strokes": [], "meta": keep}
    save_json(os.path.join(tmp, "reply.json"), rec)
    final = os.path.join(root, rid)
    os.rename(tmp, final)
    print("posted style board:", final)
    return final


# ---------------------------------------------------------------- command line

def _flag(argv, name, default=None, cast=str):
    return cast(argv[argv.index(name) + 1]) if name in argv else default


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "read" and len(argv) >= 3:
        read(argv[1], argv[2])
    elif cmd == "resolve" and len(argv) >= 3:
        resolve(argv[1], argv[2])
    elif cmd == "sun" and len(argv) >= 6:
        lat, lon, tz, alt, az = [float(x) for x in argv[1:6]]
        print(json.dumps(solve_sun(lat, lon, tz, alt, az, _flag(argv, "--year", None, int)), indent=1))
    elif cmd == "vropt" and len(argv) >= 5:
        style_vropt(argv[1], load_json(argv[2]), argv[3], argv[4], "--lightmix" in argv)
    elif cmd == "fit" and len(argv) >= 4:
        fit(argv[1], argv[2], argv[3], _flag(argv, "--strength", 1.0, float))
    elif cmd == "grade" and len(argv) >= 4:
        rgb, _ = load_rgb(argv[1])
        save_rgb(argv[3], apply_grade(rgb, load_json(argv[2]), "--look-only" in argv))
        print("graded:", argv[3])
    elif cmd == "cube" and len(argv) >= 3:
        print("lut:", write_cube(load_json(argv[1]), argv[2], "--look-only" in argv))
    elif cmd == "compare" and len(argv) >= 3:
        compare(argv[1], argv[2])
    elif cmd == "board" and len(argv) >= 5:
        board(argv[1], argv[2], argv[3], argv[4])
    elif cmd == "post" and len(argv) >= 3:
        post(argv[1], argv[2])
    elif cmd == "bake" and len(argv) >= 5:
        bake(argv[1], argv[2], argv[3], argv[4])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
