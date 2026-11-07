# -*- coding: utf-8 -*-
"""ClaudeMark toolkit core: a capture's saved camera as rays and projections, and the scene as meshes.

Runs inside Rhino (IronPython 2.7) through the MCP's python tool:

    exec(open(r"<kit folder>\\tools\\cm_core.py").read())
    folder, meta = load_capture("20261018-150514")
    cam = Cam(meta)
    sc_ = Scene(sc.doc)
    hit = pick(cam, sc_, 812.0, 433.0)        # {"point", "normal", "id", "layer", "on"}

The camera comes from meta["camera"]["world_to_screen"], recorded at the moment of capture, so
it is right however far the user’s viewport has moved since. Image px = viewport px, origin top-left.
"""
import os
import re
import json
import json.encoder
import math
import time
import datetime

# IronPython's json .decode('utf-8')s any (already unicode) string holding \x80-\xff and dies on "·" in layer
# names; never take that branch (same fix as claude_mark.py).
json.encoder.HAS_UTF8 = re.compile(r"(?!)")

import System
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc

def _cm_root():
    """The ClaudeMark folder: CLAUDEMARK_ROOT (set it before the exec, or in the environment), else the usual
    places. The hook names the folder in every capture it attaches."""
    for d in (globals().get("CLAUDEMARK_ROOT"), os.environ.get("CLAUDEMARK_ROOT")):
        if d and os.path.isdir(d):
            return d
    desk = System.Environment.GetFolderPath(System.Environment.SpecialFolder.DesktopDirectory)
    home = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile)
    for d in (os.path.join(desk, "Rhino-ClaudeMark"), os.path.join(home, ".claude", "rhino-markup")):
        if os.path.isfile(os.path.join(d, "claude_mark.py")):
            return d
    return os.path.join(desk, "Rhino-ClaudeMark")


CM_ROOT = _cm_root()
CM_OUTBOX = os.path.join(CM_ROOT, "outbox")


# ---------------------------------------------------------------- captures

def find_capture(stamp):
    for sub in ("sent", "inbox", "stale"):
        for name in (stamp, "." + stamp):
            p = os.path.join(CM_ROOT, sub, name)
            if os.path.isfile(os.path.join(p, "meta.json")):
                return p
    raise IOError("no ClaudeMark capture %s in sent/, inbox/ or stale/" % stamp)


def load_capture(stamp):
    folder = find_capture(stamp)
    with open(os.path.join(folder, "meta.json")) as f:
        return folder, json.load(f)


def output_dir(meta, folder, stamp):
    """Beside the .3dm in ClaudeMark\\<stamp> if the doc is saved, else the capture's analysis\\ folder."""
    doc_path = (meta.get("doc") or {}).get("path") or ""
    base = os.path.join(os.path.dirname(doc_path), "ClaudeMark", stamp) if doc_path else os.path.join(folder, "analysis")
    if not os.path.isdir(base):
        os.makedirs(base)
    return base


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)


def strokes_of(meta, kinds):
    return [(i, st) for i, st in enumerate(meta.get("strokes") or []) if st.get("kind") in kinds]


def doc_unit_scale(units_from=Rhino.UnitSystem.Millimeters):
    """Factor that turns a length in units_from into model units (1600 mm -> 1.6 in a metre model)."""
    return Rhino.RhinoMath.UnitScale(units_from, sc.doc.ModelUnitSystem)


# ---------------------------------------------------------------- the saved camera

def _mul(T, v):
    return [sum(T[i, j] * v[j] for j in range(4)) for i in range(4)]


class Cam(object):
    """The capture camera: pixel -> ray, world -> pixel."""

    def __init__(self, meta):
        c = meta["camera"]
        self.meta = meta
        self.size = tuple(meta["view"]["size"])
        self.parallel = c.get("projection") == "parallel"
        self.location = rg.Point3d(*c["location"])
        d = rg.Vector3d(*c["direction"])
        d.Unitize()
        self.direction = d
        self.up = rg.Vector3d(*c["up"])
        self.lens = c.get("lens_35mm")
        self.frustum = c.get("frustum")
        M = rg.Transform(1.0)
        for i in range(4):
            for j in range(4):
                M[i, j] = c["world_to_screen"][i][j]
        self.M = M
        ok, self.Mi = M.TryGetInverse()
        if not ok:
            raise ValueError("the capture's world_to_screen matrix doesn't invert")

    def project(self, p):
        """World point -> (x px, y px, screen depth). Depth grows away from the camera."""
        s = _mul(self.M, [p.X, p.Y, p.Z, 1.0])
        return s[0] / s[3], s[1] / s[3], s[2] / s[3]

    def ray(self, x, y):
        """The view ray through image pixel (x, y), starting at the camera (or its plane, if parallel)."""
        a = _mul(self.Mi, [x, y, 0.0, 1.0])
        b = _mul(self.Mi, [x, y, 1.0, 1.0])
        pa = rg.Point3d(a[0] / a[3], a[1] / a[3], a[2] / a[3])
        pb = rg.Point3d(b[0] / b[3], b[1] / b[3], b[2] / b[3])
        d = pb - pa
        d.Unitize()
        if d * self.direction < 0:
            d = -d
        if self.parallel:
            origin = pa + d * ((self.location - pa) * d)   # the camera's own plane
        else:
            origin = self.location
        return rg.Ray3d(origin, d)

    def check(self):
        """Pixel error of the camera target against the image centre (should be ~0)."""
        t = rg.Point3d(*self.meta["camera"]["target"])
        x, y, _ = self.project(t)
        return math.hypot(x - self.size[0] / 2.0, y - self.size[1] / 2.0)


# ---------------------------------------------------------------- the scene as meshes

def _object_meshes(obj, xform=None, depth=0):
    """Render meshes of an object, with block instances expanded (the user’s models nest blocks)."""
    out = []
    if obj is None or depth > 6:
        return out
    if isinstance(obj, Rhino.DocObjects.InstanceObject):
        idef = obj.InstanceDefinition
        x = obj.InstanceXform if xform is None else xform * obj.InstanceXform
        for o in idef.GetObjects():
            out.extend(_object_meshes(o, x, depth + 1))
        return out
    geo = obj.Geometry
    meshes = list(obj.GetMeshes(rg.MeshType.Render) or [])
    if not meshes:
        brep = None
        if isinstance(geo, rg.Brep):
            brep = geo
        elif isinstance(geo, rg.Extrusion):
            brep = geo.ToBrep()
        elif isinstance(geo, rg.SubD):
            m = rg.Mesh.CreateFromSubD(geo, 2)
            meshes = [m] if m else []
        elif isinstance(geo, rg.Mesh):
            meshes = [geo]
        elif isinstance(geo, rg.Surface):
            brep = geo.ToBrep()
        if brep is not None:
            meshes = list(rg.Mesh.CreateFromBrep(brep, rg.MeshingParameters.FastRenderMesh) or [])
    if not meshes:
        return out
    joined = rg.Mesh()
    for m in meshes:
        joined.Append(m)
    if xform is not None:
        joined = joined.DuplicateMesh()
        joined.Transform(xform)
    joined.FaceNormals.ComputeFaceNormals()
    out.append(joined)
    return out


class Scene(object):
    """Visible, renderable objects as (id, layer path, mesh, bbox). Hidden objects and layers are left
    out on purpose: ClaudeMark marks are about what the user could see."""

    def __init__(self, doc, exclude_ids=(), exclude_layers=(), only_ids=None):
        self.doc = doc
        self.items = []
        skip = set(str(i) for i in exclude_ids)
        es = Rhino.DocObjects.ObjectEnumeratorSettings()
        es.VisibleFilter = True
        es.HiddenObjects = False
        es.ObjectTypeFilter = (Rhino.DocObjects.ObjectType.Brep | Rhino.DocObjects.ObjectType.Extrusion |
                               Rhino.DocObjects.ObjectType.Mesh | Rhino.DocObjects.ObjectType.SubD |
                               Rhino.DocObjects.ObjectType.Surface | Rhino.DocObjects.ObjectType.InstanceReference)
        for obj in doc.Objects.GetObjectList(es):
            sid = str(obj.Id)
            if sid in skip or (only_ids is not None and sid not in only_ids):
                continue
            layer = doc.Layers[obj.Attributes.LayerIndex]
            if not layer.IsVisible:
                continue
            lp = layer.FullPath
            if any(lp == x or lp.startswith(x + "::") for x in exclude_layers):
                continue
            for m in _object_meshes(obj):
                self.items.append((sid, lp, m, m.GetBoundingBox(True)))
        bb = rg.BoundingBox.Empty
        for it in self.items:
            bb.Union(it[3])
        self.bbox = bb

    def hit(self, ray, max_t=None, skip_id=None):
        """Nearest hit along a ray: (t, point, normal, id, layer) or None. Normals face back along the ray."""
        best = None
        far = max_t if max_t is not None else (self.bbox.Diagonal.Length * 4 + 1.0 if self.bbox.IsValid else 1e9)
        line = rg.Line(ray.Position, ray.Position + ray.Direction * far)
        for sid, lp, m, bb in self.items:
            if sid == skip_id:
                continue
            ok, iv = rg.Intersect.Intersection.LineBox(line, bb, 0.0)
            if not ok:
                continue
            t = rg.Intersect.Intersection.MeshRay(m, ray)
            if t < 0 or (max_t is not None and t > max_t):
                continue
            if best is None or t < best[0]:
                best = (t, m, sid, lp)
        if best is None:
            return None
        t, m, sid, lp = best
        p = ray.PointAt(t)
        mp = m.ClosestMeshPoint(p, 0.0)
        n = rg.Vector3d(m.FaceNormals[mp.FaceIndex]) if mp is not None else -ray.Direction
        if n * ray.Direction > 0:
            n = -n
        return t, p, n, sid, lp

    def blocked(self, p, d, max_t=None, eps=None):
        """Does anything lie along direction d from point p (a shadow or sight test)?"""
        e = eps if eps is not None else max(1e-3, self.bbox.Diagonal.Length * 1e-5)
        return self.hit(rg.Ray3d(p + d * e, d), max_t) is not None


def cplane_point(meta, ray):
    """Where a ray meets the capture's construction plane (None if parallel to it or behind)."""
    cp = meta.get("cplane") or {}
    o = rg.Point3d(*cp.get("origin", [0, 0, 0]))
    x = rg.Vector3d(*cp.get("x", [1, 0, 0]))
    y = rg.Vector3d(*cp.get("y", [0, 1, 0]))
    pl = rg.Plane(o, x, y)
    ok, t = rg.Intersect.Intersection.LinePlane(rg.Line(ray.Position, ray.Position + ray.Direction), pl)
    if not ok or t <= 0:
        return None
    return ray.PointAt(t)


def pick(cam, scene, x, y, cplane_fallback=True):
    """What is under image pixel (x, y): the nearest visible surface, else the construction plane."""
    r = cam.ray(x, y)
    h = scene.hit(r)
    if h is not None:
        t, p, n, sid, lp = h
        return {"point": p, "normal": n, "id": sid, "layer": lp, "on": "object", "t": t}
    if cplane_fallback:
        p = cplane_point(cam.meta, r)
        if p is not None:
            n = rg.Vector3d(*cam.meta.get("cplane", {}).get("x", [1, 0, 0]))
            n = rg.Vector3d.CrossProduct(n, rg.Vector3d(*cam.meta.get("cplane", {}).get("y", [0, 1, 0])))
            n.Unitize()
            return {"point": p, "normal": n, "id": None, "layer": None, "on": "cplane", "t": (p - r.Position).Length}
    return None


def snap(cam, scene, x, y, tol_px=8.0):
    """The nearest mesh vertex of a visible object within tol_px of (x, y) that the camera can see.
    Used for photo-match points, where a corner beats wherever the click happened to land."""
    best = None
    for sid, lp, m, bb in scene.items:
        # skip objects whose box, projected to the screen, isn't near the click
        corners = [cam.project(c) for c in bb.GetCorners()]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        if not (min(xs) - tol_px <= x <= max(xs) + tol_px and min(ys) - tol_px <= y <= max(ys) + tol_px):
            continue
        for i in range(m.Vertices.Count):
            v = rg.Point3d(m.Vertices[i])
            px, py, _ = cam.project(v)
            d = math.hypot(px - x, py - y)
            if d > tol_px or (best is not None and d >= best[0]):
                continue
            # visible from the camera: nothing in front of the vertex along the view ray
            to_v = v - cam.ray(px, py).Position
            dist = to_v.Length
            if dist <= 0:
                continue
            ray = rg.Ray3d(cam.ray(px, py).Position, to_v / dist)
            h = scene.hit(ray)
            if h is not None and h[0] < dist * (1 - 1e-4) - 1e-6:
                continue
            best = (d, v, sid, lp)
    if best is None:
        return None
    return {"point": best[1], "id": best[2], "layer": best[3], "px_error": best[0]}


def stroke_px(st, step=4.0):
    """A stroke's points resampled every `step` px (pen lines, arrows, box outlines)."""
    pts = st["points"]
    if st["kind"] == "box":
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step))
        for k in range(1, n + 1):
            out.append([a[0] + (b[0] - a[0]) * k / float(n), a[1] + (b[1] - a[1]) * k / float(n)])
    return out


def point_in_poly(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def region_px(st):
    """The pixel polygon of a closed mark: a box, or a pen loop (closed by its first point)."""
    if st["kind"] == "box":
        (x0, y0), (x1, y1) = st["points"][0], st["points"][-1]
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return [list(p) for p in st["points"]]


def ensure_layer(path, colour=None):
    """Find or make a layer by full path (parents too). Returns its index."""
    idx = sc.doc.Layers.FindByFullPath(path, -1)
    if idx >= 0:
        return idx
    parent_id = System.Guid.Empty
    acc = []
    for part in path.split("::"):
        acc.append(part)
        full = "::".join(acc)
        idx = sc.doc.Layers.FindByFullPath(full, -1)
        if idx < 0:
            ly = Rhino.DocObjects.Layer()
            ly.Name = part
            if parent_id != System.Guid.Empty:
                ly.ParentLayerId = parent_id
            if colour is not None and full == path:
                ly.Color = colour
            idx = sc.doc.Layers.Add(ly)
        parent_id = sc.doc.Layers[idx].Id
    return idx


class SavedView(object):
    """Save the user’s active view's camera and display mode; restore them exactly."""

    def __init__(self, view=None):
        self.view = view or sc.doc.Views.ActiveView
        vp = self.view.ActiveViewport
        self.info = Rhino.DocObjects.ViewportInfo(vp)
        self.mode = vp.DisplayMode
        self.name = vp.Name

    def set_camera(self, cam_meta_or_info):
        vp = self.view.ActiveViewport
        if isinstance(cam_meta_or_info, Rhino.DocObjects.ViewportInfo):
            vp.SetViewProjection(cam_meta_or_info, True)
        else:
            vp.SetViewProjection(viewport_info_from_meta(cam_meta_or_info, vp), True)

    def restore(self):
        vp = self.view.ActiveViewport
        vp.SetViewProjection(self.info, True)
        vp.DisplayMode = self.mode
        if vp.Name != self.name:
            vp.Name = self.name
        self.view.Redraw()


class HiddenMarkers(object):
    """Hide the ClaudeMark:: layers (section lines, isovists) while capturing; show them again after."""

    def __enter__(self):
        self.idx = sc.doc.Layers.FindByFullPath("ClaudeMark", -1)
        self.was = None
        if self.idx >= 0:
            ly = sc.doc.Layers[self.idx]
            self.was = ly.IsVisible
            if self.was:
                ly.IsVisible = False
                ly.CommitChanges()
        return self

    def __exit__(self, *exc):
        if self.idx >= 0 and self.was:
            ly = sc.doc.Layers[self.idx]
            ly.IsVisible = True
            ly.CommitChanges()
        return False


def viewport_info_from_meta(meta, vp=None):
    """A ViewportInfo matching the capture camera (for captures and renders from the user’s exact view)."""
    c = meta["camera"]
    w, h = meta["view"]["size"]
    vi = Rhino.DocObjects.ViewportInfo(vp) if vp is not None else Rhino.DocObjects.ViewportInfo()
    vi.SetScreenPort(0, int(w), int(h), 0, 0, 0)
    loc = rg.Point3d(*c["location"])
    tgt = rg.Point3d(*c["target"])
    if c.get("projection") == "parallel":
        vi.ChangeToParallelProjection(True)
    else:
        vi.ChangeToPerspectiveProjection((tgt - loc).Length, True, c.get("lens_35mm") or 50.0)
    vi.SetCameraLocation(loc)
    vi.SetCameraDirection(rg.Vector3d(*c["direction"]))
    vi.SetCameraUp(rg.Vector3d(*c["up"]))
    vi.TargetPoint = tgt
    f = c.get("frustum")
    # A Two-Point Perspective view has a vertically shifted frustum. Rebuild it AS a two-point view: V-Ray renders a
    # plain perspective with that frustum unshifted (the user’s SMM preview framed 36% where the capture had 62%), and
    # ChangeToPerspectiveProjection(.., True, ..) locks symmetry so SetFrustum silently re-centres it (2026-10-18).
    two_point = c.get("two_point")
    if two_point is None and f and c.get("projection") != "parallel":
        d, u = c["direction"], c["up"]
        two_point = (abs(d[2]) < 1e-6 and abs(u[2] - 1.0) < 1e-6 and abs(f["left"] + f["right"]) < 1e-6 * abs(f["right"])
                     and abs(f["top"] + f["bottom"]) > 1e-3 * abs(f["top"] - f["bottom"]))
    if two_point and c.get("projection") != "parallel":
        vi.ChangeToTwoPointPerspectiveProjection((tgt - loc).Length, rg.Vector3d(0, 0, 1), c.get("lens_35mm") or 50.0)
        vi.SetCameraLocation(loc)
        vi.SetCameraDirection(rg.Vector3d(*c["direction"]))
        vi.TargetPoint = tgt
    if f:
        if not vi.IsTwoPointPerspectiveProjection:
            vi.UnlockFrustumSymmetry()         # (unlocking also breaks two-point, whose left/right must stay symmetric)
        vi.SetFrustum(f["left"], f["right"], f["bottom"], f["top"], f["near"], f["far"])
        if abs(vi.FrustumBottom - f["bottom"]) > 1e-3 * max(1.0, abs(f["top"] - f["bottom"])):
            raise Exception("frustum didn't stick: bottom %.4f, wanted %.4f" % (vi.FrustumBottom, f["bottom"]))
    return vi


def capture_from_meta(meta, out_path, mode_name=None):
    """Capture the user’s model as it is NOW from the capture's camera, at the capture's size.

    Returns the worst pixel disagreement between the live view and the saved matrix over a few test
    points: above ~1.5 px the fresh image doesn't register with the saved camera (the user’s viewport has a
    different aspect now) and shouldn't be used. The user’s camera and display mode are restored."""
    W, H = [int(v) for v in meta["view"]["size"]]
    cam = Cam(meta)
    sv = SavedView()
    vp = sv.view.ActiveViewport
    try:
        vp.SetViewProjection(viewport_info_from_meta(meta, vp), True)
        mode = Rhino.Display.DisplayModeDescription.FindByName(mode_name or meta["view"].get("display_mode") or "Shaded")
        if mode is None:
            mode = vp.DisplayMode
        live = vp.GetTransform(Rhino.DocObjects.CoordinateSystem.World, Rhino.DocObjects.CoordinateSystem.Screen)
        sx, sy = W / float(vp.Size.Width), H / float(vp.Size.Height)
        tests = [rg.Point3d(*meta["camera"]["target"])]
        bb = sc.doc.Objects.BoundingBox
        if bb.IsValid:
            tests += list(bb.GetCorners())
        err = 0.0
        for p in tests:
            a = _mul(live, [p.X, p.Y, p.Z, 1.0])
            b = _mul(cam.M, [p.X, p.Y, p.Z, 1.0])
            if abs(a[3]) < 1e-12 or abs(b[3]) < 1e-12:
                continue   # a point on the camera plane projects nowhere; skip it rather than divide by zero
            err = max(err, math.hypot(a[0] / a[3] * sx - b[0] / b[3], a[1] / a[3] * sy - b[1] / b[3]))
        with HiddenMarkers():
            bmp = sv.view.CaptureToBitmap(System.Drawing.Size(W, H), mode)
        bmp.Save(out_path, System.Drawing.Imaging.ImageFormat.Png)
        bmp.Dispose()
    finally:
        sv.restore()
    return err


def image_difference(path_a, path_b, size=(160, 90)):
    """Mean absolute grey difference (0-255) between two images, compared small."""
    a0, b0 = System.Drawing.Bitmap(path_a), System.Drawing.Bitmap(path_b)
    a = System.Drawing.Bitmap(a0, System.Drawing.Size(size[0], size[1]))
    b = System.Drawing.Bitmap(b0, System.Drawing.Size(size[0], size[1]))
    tot = 0.0
    for yy in range(size[1]):
        for xx in range(size[0]):
            pa, pb = a.GetPixel(xx, yy), b.GetPixel(xx, yy)
            tot += abs((pa.R + pa.G + pa.B) - (pb.R + pb.G + pb.B)) / 3.0
    for bm in (a0, b0, a, b):
        bm.Dispose()
    return tot / (size[0] * size[1])


def aspect_view(title, W, H, width=900):
    """A temporary floating Perspective view whose DRAWING AREA has the aspect W:H to within a pixel.

    A floating view's client area is smaller than the window rectangle, and a capture at another aspect
    keeps the horizontal field of view, so
    the window is grown until the viewport itself matches. Close it with close_aspect_view()."""
    main = sc.doc.Views.ActiveView
    was_max = main.Maximized
    rect = System.Drawing.Rectangle(40, 40, int(width), int(round(width * H / float(W))))
    view = sc.doc.Views.Add(title, Rhino.Display.DefinedViewportProjection.Perspective, rect, True)
    for _ in range(4):
        cs = view.ActiveViewport.Size
        want_h = int(round(cs.Width * H / float(W)))
        if abs(cs.Height - want_h) <= 0:
            break
        ws = view.Size
        view.Size = System.Drawing.Size(ws.Width, ws.Height + (want_h - cs.Height))
    cs = view.ActiveViewport.Size
    err = abs(cs.Width / float(cs.Height) - W / float(H)) / (W / float(H))
    return view, (main, was_max), err


def close_aspect_view(view, restore):
    main, was_max = restore
    view.Close()
    if was_max:
        main.Maximized = True
    sc.doc.Views.ActiveView = main


def depth_from_meta(meta, out_bin, step=2, width=900):
    """One z-buffer snapshot of the user’s model from the capture camera, for sketch occlusion (tools/cm_sketch.py).

    Writes out_bin (float32, row-major, h x w at 1/step of the capture view) holding each pixel's depth along
    the camera direction (0 = nothing there), plus out_bin's .json header. Uses a temporary view with the
    capture's aspect in Shaded mode (a Wireframe z-buffer only holds edges). Costs well under a second."""
    import struct
    W, H = [int(v) for v in meta["view"]["size"]]
    view, restore, aerr = aspect_view("CM depth", W, H, width)
    try:
        vp = view.ActiveViewport
        vp.SetViewProjection(viewport_info_from_meta(meta, vp), True)
        shaded = Rhino.Display.DisplayModeDescription.FindByName("Shaded")
        vp.DisplayMode = shaded
        with HiddenMarkers():
            view.Redraw()
            zb = Rhino.Display.ZBufferCapture(vp)
            zb.SetDisplayMode(shaded.Id)
            for f in (zb.ShowCurves, zb.ShowIsocurves, zb.ShowMeshWires, zb.ShowPoints, zb.ShowText, zb.ShowAnnotations, zb.ShowLights):
                f(False)
            sz = vp.Size
            loc = rg.Point3d(*meta["camera"]["location"])
            d = rg.Vector3d(*meta["camera"]["direction"])
            d.Unitize()
            w, h = sz.Width // step, sz.Height // step
            vals = []
            for y in range(h):
                for x in range(w):
                    px, py = x * step + step // 2, y * step + step // 2
                    if zb.ZValueAt(px, py) >= 1.0:
                        vals.append(0.0)
                    else:
                        vals.append((zb.WorldPointAt(px, py) - loc) * d)
            hits = zb.HitCount()
            zb.Dispose()
    finally:
        close_aspect_view(view, restore)
    with open(out_bin, "wb") as fh:
        fh.write(struct.pack("<%df" % len(vals), *vals))
    write_json(os.path.splitext(out_bin)[0] + ".json",
               {"w": w, "h": h, "step": step, "capture_size": [W, H], "aspect_error": aerr, "hits": hits})
    return out_bin, w, h


def rhino_notice(text):
    Rhino.RhinoApp.WriteLine("ClaudeMark: " + text)
