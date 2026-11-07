"""Sketch design options over a ClaudeMark capture, without modelling them in Rhino (CPython 3: numpy, PIL).

    python cm_sketch.py render <capture folder> <options.json> <out folder> [--depth <depth.bin>]
    python cm_sketch.py post   <capture folder> <out folder>          (sends the board to ClaudeReply)

options.json:
{
  "title": "Canopy options",
  "options": [
    {"label": "A", "title": "One folded plane", "idea": "a single folded roof that rises toward the street",
     "primitives": [
        {"type": "box", "min": [x, y, z], "max": [x, y, z]},
        {"type": "box", "center": [x, y, z], "size": [dx, dy, dz], "rotation": deg},
        {"type": "prism", "base": [[x, y], ...], "z0": z, "z1": z},
        {"type": "cylinder", "center": [x, y], "radius": r, "z0": z, "z1": z, "segments": 24},
        {"type": "faces", "faces": [[[x, y, z], ...], ...]},               # any faceted solid or sheet
        {"type": "loft", "curves": [[[x, y, z], ...], [[x, y, z], ...]]},  # ruled sheet through 2+ polylines
        {"type": "line", "points": [[x, y, z], ...]}
     ],
     "notes": [{"at": [x, y, z], "text": "4 m clear"}]}
  ]
}
Coordinates are Rhino world units. The capture's saved camera projects them, so the sketch sits in the user’s exact view.
The optional depth file (tools/cm_core.py depth_from_meta) hides the parts the user’s existing model stands in front of.
"""
import json
import math
import os
import shutil
import sys
import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

COLOURS = {"A": (46, 144, 250), "B": (18, 183, 106), "C": (240, 150, 20), "D": (155, 89, 230), "E": (230, 70, 110)}
FONT_DIR = r"C:\Windows\Fonts"


def font(size, bold=True):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, "ARIALNB.TTF" if bold else "ARIALN.TTF"), int(size))
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------- the saved camera

class Camera(object):
    def __init__(self, meta):
        c = meta["camera"]
        self.W, self.H = [int(v) for v in meta["view"]["size"]]
        self.M = np.array(c["world_to_screen"], dtype=np.float64)
        self.Mi = np.linalg.inv(self.M)
        self.loc = np.array(c["location"], dtype=np.float64)
        d = np.array(c["direction"], dtype=np.float64)
        self.dir = d / np.linalg.norm(d)
        up = np.array(c["up"], dtype=np.float64)
        self.right = np.cross(self.dir, up)
        self.right /= np.linalg.norm(self.right)
        self.up = np.cross(self.right, self.dir)
        self.parallel = c.get("projection") == "parallel"
        f = c.get("frustum") or {}
        self.near = float(f.get("near", 1e-3)) if not self.parallel else -1e18
        self._rays = None

    def project(self, P):
        """(n,3) world -> (n,2) pixels, (n,) view depth along the camera direction."""
        P = np.atleast_2d(P)
        h = np.c_[P, np.ones(len(P))] @ self.M.T
        return h[:, :2] / h[:, 3:4], (P - self.loc) @ self.dir

    def rays(self):
        """Per-pixel ray origin and unit direction, (H, W, 3) each, for pixel centres."""
        if self._rays is None:
            ys, xs = np.mgrid[0:self.H, 0:self.W].astype(np.float64)
            xs += 0.5
            ys += 0.5
            def unproject(z):
                h = np.stack([xs, ys, np.full_like(xs, z), np.ones_like(xs)], axis=-1) @ self.Mi.T
                return h[..., :3] / h[..., 3:4]
            a, b = unproject(0.0), unproject(1.0)
            D = b - a
            D /= np.linalg.norm(D, axis=-1, keepdims=True)
            flip = (D @ self.dir) < 0
            D[flip] *= -1
            if self.parallel:
                O = a + D * (((self.loc - a) * D).sum(-1, keepdims=True))
            else:
                O = np.broadcast_to(self.loc, D.shape).copy()
            self._rays = (O, D)
        return self._rays


# ---------------------------------------------------------------- primitives -> polygons

def _box_corners(p):
    if "min" in p:
        (x0, y0, z0), (x1, y1, z1) = p["min"], p["max"]
        c = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
        return np.array(c, dtype=np.float64)
    cx, cy, cz = p["center"]
    dx, dy, dz = p["size"]
    a = math.radians(p.get("rotation", 0.0))
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for z in (-dz / 2.0, dz / 2.0):
        for y in (-dy / 2.0, dy / 2.0):
            for x in (-dx / 2.0, dx / 2.0):
                out.append((cx + x * ca - y * sa, cy + x * sa + y * ca, cz + z))
    return np.array(out, dtype=np.float64)


def polygons(prim):
    """A primitive as polygons: list of (vertices (n,3), hard_edges [bool per edge i -> i+1])."""
    t = prim["type"]
    polys = []
    if t == "box":
        c = _box_corners(prim)
        for f in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            polys.append((c[list(f)], [True] * 4))
    elif t in ("prism", "slab"):
        base = [tuple(v) for v in prim["base"]]
        z0, z1 = float(prim["z0"]), float(prim.get("z1", float(prim["z0"]) + float(prim.get("height", 0))))
        bot = np.array([(x, y, z0) for x, y in base])
        top = np.array([(x, y, z1) for x, y in base])
        polys.append((bot[::-1], [True] * len(base)))
        polys.append((top, [True] * len(base)))
        for i in range(len(base)):
            j = (i + 1) % len(base)
            polys.append((np.array([bot[i], bot[j], top[j], top[i]]), [True] * 4))
    elif t == "cylinder":
        (cx, cy), r = prim["center"], float(prim["radius"])
        z0, z1, n = float(prim["z0"]), float(prim["z1"]), int(prim.get("segments", 24))
        ring = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        bot = np.array([(x, y, z0) for x, y in ring])
        top = np.array([(x, y, z1) for x, y in ring])
        polys.append((bot[::-1], [True] * n))
        polys.append((top, [True] * n))
        for i in range(n):
            j = (i + 1) % n
            polys.append((np.array([bot[i], bot[j], top[j], top[i]]), [True, "sil", True, "sil"]))
    elif t == "faces":
        for f in prim["faces"]:
            polys.append((np.array(f, dtype=np.float64), [True] * len(f)))
    elif t == "wall":
        polys.extend(_wall_polygons(prim))
    elif t == "loft":
        curves = [np.array(cv, dtype=np.float64) for cv in prim["curves"]]
        for a, b in zip(curves, curves[1:]):
            n = min(len(a), len(b))
            for i in range(n - 1):
                # a[i]->a[i+1] along curve a (hard), a[i+1]->b[i+1] (soft unless the last strip),
                # b[i+1]->b[i] along curve b (hard), b[i]->a[i] (soft unless the first strip)
                edges = [True, i == n - 2, True, i == 0]
                polys.append((np.array([a[i], a[i + 1], b[i + 1], b[i]]), edges))
    return polys


def _wall_polygons(prim):
    """A straight wall with rectangular openings.

    {"type": "wall", "start": [x, y, z], "end": [x, y, z], "height": h, "thickness": t,
     "openings": [{"x": along-wall offset of the left edge, "z": sill height, "w": width, "h": height}, ...]}
    The outside face runs start -> end; the wall's thickness goes to the left of that direction (seen from
    above). Faces are split into rectangles around the openings, with reveals lining each opening."""
    S, E = np.array(prim["start"], dtype=np.float64), np.array(prim["end"], dtype=np.float64)
    L = np.linalg.norm((E - S)[:2])
    u = np.array([(E - S)[0] / L, (E - S)[1] / L, 0.0])
    inward = np.array([-u[1], u[0], 0.0]) * float(prim["thickness"])
    up = np.array([0.0, 0.0, 1.0])
    Hh = float(prim["height"])
    ops = prim.get("openings", [])
    xs = sorted(set([0.0, L] + [o["x"] for o in ops] + [o["x"] + o["w"] for o in ops]))
    zs = sorted(set([0.0, Hh] + [o["z"] for o in ops] + [o["z"] + o["h"] for o in ops]))

    def at(x, z, back=False):
        return S + u * x + up * z + (inward if back else 0.0)

    def in_opening(xm, zm):
        return any(o["x"] < xm < o["x"] + o["w"] and o["z"] < zm < o["z"] + o["h"] for o in ops)
    polys = []
    for i in range(len(xs) - 1):
        for j in range(len(zs) - 1):
            x0, x1, z0, z1 = xs[i], xs[i + 1], zs[j], zs[j + 1]
            if in_opening((x0 + x1) / 2.0, (z0 + z1) / 2.0):
                continue
            # cell edges are soft unless they lie on the wall's outline or an opening's edge
            def hard(xa, za, xb, zb):
                mx, mz = (xa + xb) / 2.0, (za + zb) / 2.0
                on_outline = (xa == xb and mx in (0.0, L)) or (za == zb and mz in (0.0, Hh))
                if xa == xb:   # a vertical edge: hard on the outline, or where it bounds an opening
                    return on_outline or any(abs(xa - o["x"]) < 1e-6 or abs(xa - o["x"] - o["w"]) < 1e-6
                                             for o in ops if o["z"] <= mz <= o["z"] + o["h"])
                return on_outline or any(abs(za - o["z"]) < 1e-6 or abs(za - o["z"] - o["h"]) < 1e-6
                                         for o in ops if o["x"] <= mx <= o["x"] + o["w"])
            e = [hard(x0, z0, x1, z0), hard(x1, z0, x1, z1), hard(x1, z1, x0, z1), hard(x0, z1, x0, z0)]
            polys.append((np.array([at(x0, z0), at(x1, z0), at(x1, z1), at(x0, z1)]), e))
            polys.append((np.array([at(x0, z0, True), at(x0, z1, True), at(x1, z1, True), at(x1, z0, True)]),
                          [e[3], e[2], e[1], e[0]]))
    # top, ends and bottom
    polys.append((np.array([at(0, Hh), at(L, Hh), at(L, Hh, True), at(0, Hh, True)]), [True] * 4))
    polys.append((np.array([at(0, 0), at(0, Hh), at(0, Hh, True), at(0, 0, True)]), [True] * 4))
    polys.append((np.array([at(L, 0), at(L, 0, True), at(L, Hh, True), at(L, Hh)]), [True] * 4))
    # reveals lining each opening
    for o in ops:
        x0, x1, z0, z1 = o["x"], o["x"] + o["w"], o["z"], o["z"] + o["h"]
        for a, b in (((x0, z0), (x1, z0)), ((x1, z0), (x1, z1)), ((x1, z1), (x0, z1)), ((x0, z1), (x0, z0))):
            polys.append((np.array([at(a[0], a[1]), at(b[0], b[1]), at(b[0], b[1], True), at(a[0], a[1], True)]),
                          [True, False, True, False]))
    return polys


def _clip_near(V, edges, cam):
    """Clip a polygon to the near side of the camera (view depth > near). Clip-boundary edges become soft."""
    if cam.parallel:
        return V, edges
    s = (V - cam.loc) @ cam.dir
    eps = cam.near * 1.001
    if (s > eps).all():
        return V, edges
    outV, outE = [], []
    n = len(V)
    for i in range(n):
        j = (i + 1) % n
        a_in, b_in = s[i] > eps, s[j] > eps
        if a_in:
            outV.append(V[i])
            outE.append(edges[i])   # the segment leaving V[i] is (part of) original edge i
        if a_in != b_in:
            t = (eps - s[i]) / (s[j] - s[i])
            outV.append(V[i] + (V[j] - V[i]) * t)
            outE.append(edges[i] if not a_in else False)
    if len(outV) < 3:
        return None, None
    return np.array(outV), outE


# ---------------------------------------------------------------- rendering

def render_option(cam, opt, base_rgb, scene_depth, colour):
    """One option drawn over the capture: translucent shaded faces, visible edges, hidden parts ghosted."""
    H, W = cam.H, cam.W
    O, D = cam.rays()
    zbuf = np.full((H, W), np.inf)
    shade = np.zeros((H, W, 3))
    face_polys = []
    light = cam.dir * -1.0 + cam.up * 0.8 - cam.right * 0.5
    light /= np.linalg.norm(light)
    named = {"context": (200, 200, 196), "glass": (175, 208, 232), "dark": (70, 72, 76), "timber": (176, 128, 84)}
    glass_prims = []
    for prim in opt.get("primitives", []):
        if prim["type"] == "line":
            continue
        pc = prim.get("colour")
        if isinstance(pc, (list, tuple)):
            base_col = tuple(int(c) for c in pc)
        else:
            base_col = named.get(pc, colour)   # "context", "glass", "dark", "timber", or the option's colour
        if pc == "glass":
            glass_prims.append(prim)   # drawn afterwards, translucent, so what's behind it shows
            continue
        for V, edges in polygons(prim):
            V, edges = _clip_near(V, edges, cam)
            if V is None:
                continue
            n = np.cross(V[1] - V[0], V[2] - V[0])
            if np.linalg.norm(n) < 1e-12:
                for k in range(2, len(V) - 1):
                    n = np.cross(V[k] - V[0], V[k + 1] - V[0])
                    if np.linalg.norm(n) > 1e-12:
                        break
            nn = np.linalg.norm(n)
            if nn < 1e-12:
                continue
            n = n / nn
            px, _ = cam.project(V)
            x0, y0 = max(0, int(np.floor(px[:, 0].min()))), max(0, int(np.floor(px[:, 1].min())))
            x1, y1 = min(W, int(np.ceil(px[:, 0].max())) + 1), min(H, int(np.ceil(px[:, 1].max())) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            m = Image.new("L", (x1 - x0, y1 - y0), 0)
            ImageDraw.Draw(m).polygon([(float(p[0] - x0), float(p[1] - y0)) for p in px], fill=255)
            mask = np.asarray(m) > 0
            if not mask.any():
                continue
            Os, Ds = O[y0:y1, x0:x1][mask], D[y0:y1, x0:x1][mask]
            denom = Ds @ n
            ok = np.abs(denom) > 1e-12
            t = np.where(ok, ((V[0] - Os) @ n) / np.where(ok, denom, 1.0), np.inf)
            X = Os + Ds * t[:, None]
            s = (X - cam.loc) @ cam.dir
            ys, xs = np.nonzero(mask)
            ys, xs = ys + y0, xs + x0
            closer = s < zbuf[ys, xs]
            ys, xs, s = ys[closer], xs[closer], s[closer]
            zbuf[ys, xs] = s
            facing = n if (n @ -cam.dir) >= 0 else -n
            lam = 0.55 + 0.45 * max(0.0, float(facing @ light))
            shade[ys, xs] = np.array(base_col) * lam
            face_polys.append((V, edges, n, base_col))
    # occlusion by the user’s model
    if scene_depth is not None:
        # Hidden only when clearly behind the user’s model: anything ON an existing surface (a base on the ground,
        # a path drawn on the floor) is within the depth buffer's tolerance and must stay visible.
        hidden = zbuf > scene_depth * (1 + 0.004) + 1e-9
    else:
        hidden = np.zeros((H, W), bool)
    have = np.isfinite(zbuf)
    out = base_rgb.astype(np.float64).copy()
    vis = have & ~hidden
    ghost = have & hidden
    out[vis] = out[vis] * 0.30 + shade[vis] * 0.70
    out[ghost] = out[ghost] * 0.86 + np.array(colour) * 0.14
    # glass: a translucent tint over whatever is behind it, where it is in front of the solid sketch and the user’s model
    glass_edges = []
    for prim in glass_prims:
        for V, edges in polygons(prim):
            V, edges = _clip_near(V, edges, cam)
            if V is None:
                continue
            n = np.cross(V[1] - V[0], V[2] - V[0])
            if np.linalg.norm(n) < 1e-12:
                continue
            n = n / np.linalg.norm(n)
            px, _ = cam.project(V)
            x0, y0 = max(0, int(np.floor(px[:, 0].min()))), max(0, int(np.floor(px[:, 1].min())))
            x1, y1 = min(W, int(np.ceil(px[:, 0].max())) + 1), min(H, int(np.ceil(px[:, 1].max())) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            m = Image.new("L", (x1 - x0, y1 - y0), 0)
            ImageDraw.Draw(m).polygon([(float(p[0] - x0), float(p[1] - y0)) for p in px], fill=255)
            mask = np.asarray(m) > 0
            if not mask.any():
                continue
            Os, Ds = O[y0:y1, x0:x1][mask], D[y0:y1, x0:x1][mask]
            denom = Ds @ n
            ok = np.abs(denom) > 1e-12
            t = np.where(ok, ((V[0] - Os) @ n) / np.where(ok, denom, 1.0), np.inf)
            s = ((Os + Ds * t[:, None]) - cam.loc) @ cam.dir
            ys, xs = np.nonzero(mask)
            ys, xs = ys + y0, xs + x0
            front = s <= zbuf[ys, xs] * (1 + 0.003)
            if scene_depth is not None:
                front &= s <= scene_depth[ys, xs] * (1 + 0.004)
            ys, xs = ys[front], xs[front]
            out[ys, xs] = out[ys, xs] * 0.72 + np.array(named["glass"]) * 0.28
            glass_edges.append((V, edges))
    img = Image.fromarray(out.clip(0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(img)
    edge_col = tuple(int(c * 0.45) for c in colour)
    lw = max(2, int(round(W / 700.0)))

    def visible_samples(A, B):
        ends = cam.project(np.array([A, B]))[0]
        L = np.linalg.norm(ends[1] - ends[0])
        k = int(min(4000, max(2, L)))
        ts = np.linspace(0, 1, k)
        P = A[None, :] + (B - A)[None, :] * ts[:, None]
        q, s = cam.project(P)
        xi, yi = np.clip(q[:, 0].astype(int), 0, W - 1), np.clip(q[:, 1].astype(int), 0, H - 1)
        inside = (q[:, 0] >= 0) & (q[:, 0] < W) & (q[:, 1] >= 0) & (q[:, 1] < H)
        tol = np.maximum(s * 0.003, 1e-6)
        self_vis = s <= zbuf[yi, xi] + tol
        nb = [zbuf[np.clip(yi + dy, 0, H - 1), np.clip(xi + dx, 0, W - 1)] for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0))]
        self_vis |= np.any([s <= z + tol for z in nb], axis=0)
        scene_vis = np.ones_like(s, bool) if scene_depth is None else s <= scene_depth[yi, xi] * (1 + 0.004)
        return q, inside & self_vis & scene_vis, inside & self_vis & ~scene_vis

    def draw_runs(q, flags, col, width, dashed=False):
        run = []
        for i in range(len(q)):
            if flags[i] and (not dashed or (i // 6) % 2 == 0):
                run.append((float(q[i, 0]), float(q[i, 1])))
            else:
                if len(run) > 1:
                    dr.line(run, fill=col, width=width)
                run = []
        if len(run) > 1:
            dr.line(run, fill=col, width=width)

    for V, edges, n, bc in face_polys:
        ecol = tuple(int(c * 0.45) for c in bc)
        for i in range(len(V)):
            e = edges[i]
            if e is False:
                continue
            A, B = V[i], V[(i + 1) % len(V)]
            if e == "sil":
                continue
            q, vis_s, hid_s = visible_samples(A, B)
            draw_runs(q, vis_s, ecol, lw if bc == colour else max(1, lw - 1))
            draw_runs(q, hid_s, tuple(int(c * 0.8) for c in colour), 1, dashed=True)
    gcol = (110, 150, 180)
    for V, edges in glass_edges:   # light outlines on the glass, so panes read as panes
        for i in range(len(V)):
            if edges[i] is False:
                continue
            q, vis_s, hid_s = visible_samples(V[i], V[(i + 1) % len(V)])
            draw_runs(q, vis_s, gcol, 1)
    # cylinder silhouettes: side edges where one neighbour faces the camera and the other doesn't
    for prim in opt.get("primitives", []):
        if prim["type"] == "cylinder":
            (cx, cy), r = prim["center"], float(prim["radius"])
            z0, z1 = float(prim["z0"]), float(prim["z1"])
            mid = np.array([cx, cy, (z0 + z1) / 2.0])
            to_cam = (cam.loc - mid) if not cam.parallel else -cam.dir
            to_cam[2] = 0
            if np.linalg.norm(to_cam) > 1e-9:
                to_cam /= np.linalg.norm(to_cam)
                for sgn in (1, -1):
                    side = np.array([-to_cam[1], to_cam[0], 0]) * sgn
                    A, B = np.array([cx, cy, z0]) + side * r, np.array([cx, cy, z1]) + side * r
                    q, vis_s, hid_s = visible_samples(A, B)
                    draw_runs(q, vis_s, edge_col, lw)
        if prim["type"] == "line":
            pts = np.array(prim["points"], dtype=np.float64)
            for A, B in zip(pts, pts[1:]):
                q, vis_s, hid_s = visible_samples(A, B)   # solid where seen, dashed behind the user’s model
                draw_runs(q, vis_s, edge_col, lw + 1)
                draw_runs(q, hid_s, tuple(int(c * 0.8) for c in colour), 1, dashed=True)
    # notes: small tags in the option's colour
    fnt = font(max(14, W // 70))
    for note in opt.get("notes", []):
        q, s = cam.project(np.array([note["at"]], dtype=np.float64))
        if s[0] <= 0 and not cam.parallel:
            continue
        x, y = float(q[0, 0]), float(q[0, 1])
        tw = dr.textlength(note["text"], font=fnt)
        th = fnt.size + 6
        bx, by = min(max(x + 10, 4), W - tw - 16), min(max(y - th - 10, 4), H - th - 4)
        dr.line([(x, y), (bx + 4, by + th)], fill=edge_col, width=2)
        dr.ellipse([x - 4, y - 4, x + 4, y + 4], fill=colour, outline=edge_col)
        dr.rounded_rectangle([bx, by, bx + tw + 12, by + th], radius=6, fill=(20, 21, 24), outline=colour, width=2)
        dr.text((bx + 6, by + 2), note["text"], font=fnt, fill=colour)
    # title block: the letter in a chip, then the idea
    big, small = font(max(26, W // 32)), font(max(15, W // 62), bold=False)
    label, title, idea = opt.get("label", "?"), opt.get("title", ""), opt.get("idea", "")
    chip = big.size + 18
    dr.rounded_rectangle([16, 16, 16 + chip, 16 + chip], radius=10, fill=colour)
    dr.text((16 + chip / 2.0, 16 + chip / 2.0), label, font=big, fill=(255, 255, 255), anchor="mm")
    dr.text((28 + chip, 16), title, font=big, fill=(20, 20, 20), stroke_width=3, stroke_fill=(255, 255, 255))
    if idea:
        dr.text((28 + chip, 22 + big.size), idea, font=small, fill=(30, 30, 30), stroke_width=2, stroke_fill=(255, 255, 255))
    return img, int(vis.sum()), int(ghost.sum())


def load_depth(path, cam):
    with open(os.path.splitext(path)[0] + ".json") as fh:
        hdr = json.load(fh)
    w, h = hdr["w"], hdr["h"]
    a = np.fromfile(path, dtype="<f4").reshape(h, w).astype(np.float64)
    a[a <= 0] = np.inf
    # nearest upsample to the capture's size (the depth pass is coarser than the capture)
    yi = np.clip(((np.arange(cam.H) + 0.5) * h / cam.H).astype(int), 0, h - 1)
    xi = np.clip(((np.arange(cam.W) + 0.5) * w / cam.W).astype(int), 0, w - 1)
    return a[yi][:, xi]


def render(capture, options_json, out, depth=None):
    with open(os.path.join(capture, "meta.json")) as fh:
        meta = json.load(fh)
    with open(options_json) as fh:
        spec = json.load(fh)
    cam = Camera(meta)
    base = Image.open(os.path.join(capture, "clean.png")).convert("RGB")
    base = ImageEnhance.Color(base).enhance(0.55)
    base_rgb = np.asarray(base)
    scene = load_depth(depth, cam) if depth else None
    os.makedirs(out, exist_ok=True)
    made = []
    for k, opt in enumerate(spec["options"]):
        label = opt.get("label") or "ABCDE"[k]
        opt["label"] = label
        col = COLOURS.get(label, (46, 144, 250))
        img, nvis, nghost = render_option(cam, opt, base_rgb, scene, col)
        path = os.path.join(out, "option_%s.png" % label)
        img.save(path)
        made.append({"label": label, "title": opt.get("title", ""), "idea": opt.get("idea", ""),
                     "image": os.path.basename(path), "visible_px": nvis, "hidden_px": nghost})
        print("option %s: %d px visible, %d px behind the user’s model -> %s" % (label, nvis, nghost, path))
    # the board: options side by side, each at a common width
    tw = 900
    th = int(round(cam.H * tw / float(cam.W)))
    gap, head = 18, 70
    board = Image.new("RGB", (len(made) * tw + (len(made) - 1) * gap + 2 * gap, th + head + gap), (255, 255, 255))
    dr = ImageDraw.Draw(board)
    dr.text((gap, 16), (spec.get("title") or "OPTIONS").upper(), font=font(40), fill=(0, 0, 0))
    for i, m in enumerate(made):
        im = Image.open(os.path.join(out, m["image"])).resize((tw, th), Image.LANCZOS)
        board.paste(im, (gap + i * (tw + gap), head))
    board.save(os.path.join(out, "board.png"))
    with open(os.path.join(out, "options_rendered.json"), "w") as fh:
        json.dump({"title": spec.get("title", "Options"), "options": made, "capture": capture}, fh, indent=1)
    print("board:", os.path.join(out, "board.png"))
    return made


def post(capture, out):
    """Put the rendered options in the outbox as a ClaudeReply options board."""
    root = os.path.join(os.environ.get("CLAUDEMARK_ROOT")
                        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outbox")
    with open(os.path.join(capture, "meta.json")) as fh:
        meta = json.load(fh)
    with open(os.path.join(out, "options_rendered.json")) as fh:
        rendered = json.load(fh)
    rid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-options"
    tmp = os.path.join(root, "." + rid)
    os.makedirs(tmp, exist_ok=True)
    shutil.copyfile(os.path.join(capture, "clean.png"), os.path.join(tmp, "clean.png"))
    for m in rendered["options"]:
        shutil.copyfile(os.path.join(out, m["image"]), os.path.join(tmp, m["image"]))
    shutil.copyfile(os.path.join(out, "board.png"), os.path.join(tmp, "marked.png"))
    keep = dict((k, meta[k]) for k in ("camera", "view", "doc", "cplane", "image", "clipping_planes") if k in meta)
    rec = {"reply_to": os.path.basename(os.path.normpath(capture)), "kind": "options",
           "created": datetime.datetime.now().isoformat(), "title": rendered.get("title", "Options"),
           "options": rendered["options"], "strokes": [], "meta": keep}
    with open(os.path.join(tmp, "reply.json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    final = os.path.join(root, rid)
    os.rename(tmp, final)
    print("posted options board:", final)
    return final


def main(argv):
    if len(argv) >= 4 and argv[0] == "render":
        depth = argv[argv.index("--depth") + 1] if "--depth" in argv else None
        render(argv[1], argv[2], argv[3], depth)
        return 0
    if len(argv) >= 3 and argv[0] == "post":
        post(argv[1], argv[2])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
