# -*- coding: utf-8 -*-
"""ClaudeMark analyses and photo-match, Rhino side (IronPython 2.7). Load cm_core first:

    exec(open(r"<kit folder>\\tools\\cm_core.py").read())
    exec(open(r"<kit folder>\\tools\\cm_analyse.py").read())

Each function reads a capture (stamp), works on the visible model through the capture's own camera,
and writes a JSON that tools/cm_post.py turns into drawings (python cm_post.py <kind> <json> <outdir>).
Slow jobs are chunked: start them, then call step(seconds) until done, so no single MCP call runs long
(a long MCP call can take Rhino down:). The user’s view and sun are always
restored.

  section(stamp, stroke=None, flip=False, name="Section A")    -> job; section_step(job) until "done"
  sun_hours(stamp, dates=(...), step_min=30)                   -> job; job_step(job) until "done"
  shadow_study(stamp, dates=(...), hours=range(8, 19))         -> job; job_step(job) until "done"
  isovist(stamp, eye_mm=1600, rays=720)                        -> json path
  eye_level(stamp, lens=24.0, eye_mm=1600)                     -> list of (named view, png)
  photomatch_pairs(stamp)                                      -> pairs.json for cm_photomatch.py
  photomatch_apply(stamp, camera_json)                         -> named view + edges.png for the overlay
"""
import os
import math
import json
import time
import datetime

import System
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc

SMM = {"lat": 51.4769, "lon": -0.0005, "tz": 0.0}   # fallback site (Greenwich) when the model's Rhino sun has no location: set yours in Rhino's Sun panel
JOBS = sc.sticky.setdefault("cm_jobs", {})


def _meters_per_unit():
    return Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)


def _tol():
    return sc.doc.ModelAbsoluteTolerance


def _visible_objects():
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.VisibleFilter = True
    es.HiddenObjects = False
    out = []
    for obj in sc.doc.Objects.GetObjectList(es):
        if sc.doc.Layers[obj.Attributes.LayerIndex].IsVisible:
            out.append(obj)
    return out


def _geometry_with_xforms(obj, xform=None, depth=0):
    """(geometry, layer path) pairs with block instances expanded and transformed."""
    out = []
    if depth > 6:
        return out
    if isinstance(obj, Rhino.DocObjects.InstanceObject):
        x = obj.InstanceXform if xform is None else xform * obj.InstanceXform
        for o in obj.InstanceDefinition.GetObjects():
            out.extend(_geometry_with_xforms(o, x, depth + 1))
        return out
    g = obj.Geometry
    if isinstance(g, (rg.Brep, rg.Extrusion, rg.Mesh, rg.Surface, rg.SubD)):
        if xform is not None:
            g = g.Duplicate()
            g.Transform(xform)
        lp = sc.doc.Layers[obj.Attributes.LayerIndex].FullPath
        out.append((g, lp))
    return out


def _as_brep(g):
    if isinstance(g, rg.Brep):
        return g
    if isinstance(g, rg.Extrusion):
        return g.ToBrep()
    if isinstance(g, rg.Surface):
        return g.ToBrep()
    if isinstance(g, rg.SubD):
        return g.ToBrep(rg.SubDToBrepOptions.Default)
    return None


def _curve_pts(c, tol):
    pl = c.ToPolyline(0, 0, 0.1, 0.0, 0.0, tol * 4, 0.0, 0.0, True)
    if pl is None:
        return [c.PointAtStart, c.PointAtEnd]
    ok, poly = pl.TryGetPolyline()
    return list(poly) if ok else [c.PointAtStart, c.PointAtEnd]


def _plane_cuts(plane, objects):
    """Section curves of the objects with a plane: (points, closed, layer) lists."""
    tol = _tol()
    out = []
    for obj in objects:
        for g, lp in _geometry_with_xforms(obj):
            bb = g.GetBoundingBox(True)
            corners = bb.GetCorners()
            sides = set(1 if plane.DistanceTo(c) > 0 else -1 for c in corners)
            if len(sides) < 2:
                continue
            if isinstance(g, rg.Mesh):
                for pl in rg.Intersect.Intersection.MeshPlane(g, plane) or []:
                    pts = list(pl)
                    out.append((pts, pl.IsClosed, lp))
                continue
            brep = _as_brep(g)
            if brep is None:
                continue
            ok, curves, pts_ = rg.Intersect.Intersection.BrepPlane(brep, plane, tol)
            if not ok or not curves:
                continue
            joined = rg.Curve.JoinCurves(curves, tol * 10) or curves
            for c in joined:
                out.append((_curve_pts(c, tol), c.IsClosed, lp))
    return out


def _sun_object(date, hh, mm):
    """A Rhino Sun at the site (the user’s doc sun's place if set, else SMM) for a local date and time."""
    ds = sc.doc.RenderSettings.Sun
    s = Rhino.Render.Sun()
    lat, lon = ds.Latitude, ds.Longitude
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        lat, lon, tz = SMM["lat"], SMM["lon"], SMM["tz"]
    else:
        tz = ds.TimeZone
    s.Latitude, s.Longitude, s.TimeZone, s.North = lat, lon, tz, ds.North
    s.DaylightSavingOn = False   # standard time: hour counts don't care, and labels say which
    s.SetDateTime(datetime.datetime(date.year, date.month, date.day, hh, mm, 0), System.DateTimeKind.Local)
    return s


def _parse_date(d):
    if isinstance(d, datetime.date):
        return d
    return datetime.datetime.strptime(d, "%Y-%m-%d").date()


def _label_date(d):
    return "%d %s" % (d.day, d.strftime("%B"))


# ---------------------------------------------------------------- section

def _line_stroke(meta, index=None):
    cands = [(i, st) for i, st in enumerate(meta.get("strokes") or [])
             if st.get("kind") in ("arrow", "pen") and len(st.get("points") or []) >= 2]
    if index is not None:
        cands = [(i, st) for i, st in cands if i == index]
    if not cands:
        raise ValueError("no line or arrow on the capture to cut along")
    return cands[0]


def section(stamp, stroke=None, flip=False, name="Section A", depth=None):
    """Start a section along the capture's first line or arrow. Returns a job id for section_step()."""
    folder, meta = load_capture(stamp)
    cam = Cam(meta)
    scene = Scene(sc.doc)
    i, st = _line_stroke(meta, stroke)
    a_px, b_px = st["points"][0], st["points"][-1]

    def plan_point(px):
        # A section line drawn over a perspective is a line in plan: read its ends on the ground
        # (the construction plane), not on whatever wall happens to stand in front.
        p = cplane_point(meta, cam.ray(*px))
        if p is None:
            h = pick(cam, scene, px[0], px[1])
            p = h["point"] if h else None
        return p
    qa, qb = plan_point(a_px), plan_point(b_px)
    if qa is None or qb is None:
        raise ValueError("the line's ends hit nothing and miss the construction plane")
    z0 = scene.bbox.Min.Z if scene.bbox.IsValid else 0.0
    p0 = rg.Point3d(qa.X, qa.Y, z0)
    p1 = rg.Point3d(qb.X, qb.Y, z0)
    along = p1 - p0
    along.Z = 0
    if along.Length < _tol():
        raise ValueError("the line is too short in plan")
    along.Unitize()
    n = rg.Vector3d.CrossProduct(along, rg.Vector3d.ZAxis)
    n.Unitize()
    towards_cam = -cam.direction if cam.parallel else (cam.location - p0)
    if towards_cam * n > 0:
        n = -n                 # look away from the camera, as the user was looking
    if flip:
        n = -n
    right = rg.Vector3d.CrossProduct(n, rg.Vector3d.ZAxis)
    right.Unitize()
    cut_plane = rg.Plane(p0, right, rg.Vector3d.ZAxis)   # normal = right x Z = -n: points back at the viewer
    objs = _visible_objects()
    cuts = _plane_cuts(cut_plane, objs)

    def uv(p):
        d = p - p0
        return [round(d * right, 5), round(d * rg.Vector3d.ZAxis, 5)]
    cut_json = [{"layer": lp, "closed": bool(cl), "pts": [uv(p) for p in pts]} for pts, cl, lp in cuts if len(pts) > 1]
    # linework beyond: hidden-line drawing of what lies on the far side (n), looking along n
    bb = scene.bbox
    diag = bb.Diagonal.Length if bb.IsValid else 100.0
    vi = Rhino.DocObjects.ViewportInfo()
    vi.ChangeToParallelProjection(True)
    eye = p0 - n * diag
    vi.SetCameraLocation(eye)
    vi.SetCameraDirection(n)
    vi.SetCameraUp(rg.Vector3d.ZAxis)
    vi.SetFrustum(-diag, diag, -diag, diag, 0.1, diag * 4)
    prm = rg.HiddenLineDrawingParameters()
    prm.AbsoluteTolerance = _tol()
    prm.IncludeTangentEdges = False
    prm.IncludeHiddenCurves = False
    prm.Flatten = True
    prm.SetViewport(vi)
    for obj in objs:
        for g, lp in _geometry_with_xforms(obj):
            prm.AddGeometry(g, rg.Transform.Identity, lp)
    # the HLD keeps the side AWAY from its clip plane's normal
    prm.AddClippingPlane(rg.Plane(p0, -n))
    job = {"kind": "section", "stamp": stamp, "folder": folder, "name": name, "state": "running",
           "p0": [p0.X, p0.Y, p0.Z], "n": [n.X, n.Y, n.Z], "right": [right.X, right.Y, right.Z],
           "cut": cut_json, "line_px": [a_px, b_px], "result": None, "error": None, "depth": depth}

    def run():
        try:
            job["result"] = rg.HiddenLineDrawing.Compute(prm, True)
        except Exception as ex:
            job["error"] = str(ex)
        job["state"] = "computed"
    jid = "section-%s-%d" % (stamp, int(time.time()))
    JOBS[jid] = job
    System.Threading.Tasks.Task.Factory.StartNew(System.Action(run))
    # mark the cut in the model so the user can see where it is
    li = ensure_layer("ClaudeMark::Sections", System.Drawing.Color.FromArgb(240, 68, 56))
    at = Rhino.DocObjects.ObjectAttributes()
    at.LayerIndex = li
    at.Name = "%s (ClaudeMark %s)" % (name, stamp)
    sc.doc.Objects.AddLine(rg.Line(p0, p1), at)
    return jid


def section_step(jid):
    """Finish a section once its hidden-line drawing is done: writes section.json. Returns the state."""
    job = JOBS[jid]
    if job["state"] == "running":
        return "running"
    if job["state"] == "done":
        return "done: " + job["json"]
    if job["error"]:
        job["state"] = "failed"
        return "failed: " + job["error"]
    hld = job["result"]
    p0, n, right = rg.Point3d(*job["p0"]), rg.Vector3d(*job["n"]), rg.Vector3d(*job["right"])
    T = hld.WorldToHiddenLine
    def h(p):
        q = rg.Point3d(p)
        q.Transform(T)
        return q
    h0, hx, hz = h(p0), h(p0 + right), h(p0 + rg.Vector3d.ZAxis)
    ex, ez = hx - h0, hz - h0
    det = ex.X * ez.Y - ex.Y * ez.X
    def to_uv(q):
        dx, dy = q.X - h0.X, q.Y - h0.Y
        return [(dx * ez.Y - dy * ez.X) / det, (ex.X * dy - ex.Y * dx) / det]
    scene = Scene(sc.doc)
    beyond = []
    for seg in hld.Segments:
        if seg.SegmentVisibility != rg.HiddenLineDrawingSegment.Visibility.Visible:
            continue
        crv = seg.CurveGeometry
        if crv is None:
            continue
        pts2 = _curve_pts(crv, _tol())
        uvs = [to_uv(q) for q in pts2]
        mid = uvs[len(uvs) // 2]
        P = p0 + right * mid[0] + rg.Vector3d.ZAxis * mid[1]
        hit = scene.hit(rg.Ray3d(P - n * _tol() * 10, n))
        d = hit[0] if hit is not None else 0.0
        if job.get("depth") and d > job["depth"]:
            continue
        beyond.append({"d": round(d, 4), "pts": [[round(u, 5), round(v, 5)] for u, v in uvs]})
    folder, meta = load_capture(job["stamp"])
    cam = Cam(meta)
    (ax, ay), (bx, by) = job["line_px"]
    mid3 = rg.Point3d(p0)
    sx, sy, _ = cam.project(mid3)
    tx, ty, _ = cam.project(mid3 + n * (scene.bbox.Diagonal.Length * 0.05))
    L = math.hypot(tx - sx, ty - sy) or 1.0
    out = output_dir(meta, folder, job["stamp"])
    path = os.path.join(out, "section.json")
    write_json(path, {"name": job["name"], "title": job["name"], "unit_m": _meters_per_unit(),
                      "cut": job["cut"], "beyond": beyond, "capture": os.path.join(folder, "clean.png"),
                      "line_px": {"a": [ax, ay], "b": [bx, by], "look": [(tx - sx) / L, (ty - sy) / L]}})
    job["state"], job["json"] = "done", path
    job["result"] = None
    return "done: " + path


# ---------------------------------------------------------------- sun hours

def sun_hours(stamp, dates=("2027-06-21", "2026-12-21"), step_min=30, grid_px=None, max_samples=2500):
    """Start a sun-hours job on the surfaces inside the capture's loops and boxes. Then job_step(jid)."""
    folder, meta = load_capture(stamp)
    cam = Cam(meta)
    scene = Scene(sc.doc)
    regions = [region_px(st) for _, st in enumerate(meta.get("strokes") or [])
               if st.get("kind") == "box" or (st.get("kind") == "pen" and st.get("closed"))]
    if not regions:
        raise ValueError("draw a loop or a box around the surfaces to test")
    area = 0.0
    for poly in regions:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        area += (max(xs) - min(xs)) * (max(ys) - min(ys))
    g = grid_px or max(4.0, math.sqrt(area / float(max_samples)))
    samples = []
    for poly in regions:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        y = min(ys) + g / 2.0
        while y < max(ys):
            x = min(xs) + g / 2.0
            while x < max(xs):
                if point_in_poly(x, y, poly):
                    h = scene.hit(cam.ray(x, y))
                    if h is not None:
                        samples.append({"px": [round(x, 1), round(y, 1)], "p": h[1], "n": h[2], "hours": []})
                x += g
            y += g
    suns = []
    for d in dates:
        dd = _parse_date(d)
        dirs = []
        for k in range(int(24 * 60 / step_min)):
            mins = k * step_min + step_min / 2.0
            s = _sun_object(dd, int(mins // 60), int(mins % 60))
            if s.Altitude > 0.5:
                v = -s.Vector
                v.Unitize()
                dirs.append(v)
        suns.append((dd, dirs))
    jid = "sun-%s-%d" % (stamp, int(time.time()))
    JOBS[jid] = {"kind": "sun", "stamp": stamp, "folder": folder, "samples": samples, "suns": suns,
                 "step_h": step_min / 60.0, "next": 0, "grid": g, "state": "running"}
    return jid


def _sun_step(job, seconds):
    t0 = time.time()
    scene = job.setdefault("_scene", Scene(sc.doc))
    S = job["samples"]
    while job["next"] < len(S) and time.time() - t0 < seconds:
        s = S[job["next"]]
        p, nrm = s["p"], s["n"]
        for dd, dirs in job["suns"]:
            lit = 0
            for v in dirs:
                if nrm * v > 0 and not scene.blocked(p, v):
                    lit += 1
            s["hours"].append(round(lit * job["step_h"], 2))
        job["next"] += 1
    if job["next"] < len(S):
        return "running: %d of %d samples" % (job["next"], len(S))
    folder, meta = load_capture(job["stamp"])
    out = output_dir(meta, folder, job["stamp"])
    path = os.path.join(out, "sun.json")
    write_json(path, {"name": "Sun hours", "capture": os.path.join(folder, "clean.png"), "grid_px": job["grid"],
                      "dates": [_label_date(dd) for dd, _ in job["suns"]],
                      "samples": [{"px": s["px"], "hours": s["hours"], "w": 1.0} for s in S]})
    job["state"], job["json"] = "done", path
    job.pop("_scene", None)
    return "done: " + path


# ---------------------------------------------------------------- shadow study

def shadow_study(stamp, dates=("2027-06-21", "2027-03-21", "2026-12-21"), hours=range(8, 19), width=None):
    folder, meta = load_capture(stamp)
    frames = []
    for d in dates:
        dd = _parse_date(d)
        for hh in hours:
            if _sun_object(dd, hh, 0).Altitude > 0.5:
                frames.append((dd, hh))
    jid = "shadows-%s-%d" % (stamp, int(time.time()))
    W, H = [int(v) for v in meta["view"]["size"]]
    w = int(width or min(W, 1200))
    JOBS[jid] = {"kind": "shadows", "stamp": stamp, "folder": folder, "frames": frames, "done": [], "next": 0,
                 "size": [w, int(round(H * w / float(W)))], "state": "running"}
    return jid


def _shadow_step(job, seconds):
    folder, meta = load_capture(job["stamp"])
    out = output_dir(meta, folder, job["stamp"])
    ds = sc.doc.RenderSettings.Sun
    keep = (ds.Enabled, ds.ManualControlOn, ds.Latitude, ds.Longitude, ds.TimeZone, ds.DaylightSavingOn,
            ds.GetDateTime(System.DateTimeKind.Local), ds.Altitude, ds.Azimuth)
    sv = SavedView()
    vp = sv.view.ActiveViewport
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    t0 = time.time()
    try:
        vp.SetViewProjection(viewport_info_from_meta(meta, vp), True)
        while job["next"] < len(job["frames"]) and time.time() - t0 < seconds:
            dd, hh = job["frames"][job["next"]]
            s = _sun_object(dd, hh, 0)
            ds.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
            ds.Enabled = True
            ds.ManualControlOn = True
            ds.Altitude, ds.Azimuth = s.Altitude, s.Azimuth
            ds.EndChange()
            with HiddenMarkers():
                bmp = sv.view.CaptureToBitmap(System.Drawing.Size(job["size"][0], job["size"][1]), mode)
            p = os.path.join(out, "shadow %s %02d00.png" % (dd.isoformat(), hh))
            bmp.Save(p, System.Drawing.Imaging.ImageFormat.Png)
            bmp.Dispose()
            job["done"].append({"date": _label_date(dd), "time": "%02d:00" % hh, "path": p})
            job["next"] += 1
    finally:
        ds.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
        ds.Enabled, ds.ManualControlOn = keep[0], keep[1]
        ds.Latitude, ds.Longitude, ds.TimeZone, ds.DaylightSavingOn = keep[2], keep[3], keep[4], keep[5]
        if keep[1]:
            ds.Altitude, ds.Azimuth = keep[7], keep[8]
        else:
            ds.SetDateTime(keep[6], System.DateTimeKind.Local)
        ds.EndChange()
        sv.restore()
    if job["next"] < len(job["frames"]):
        return "running: %d of %d frames" % (job["next"], len(job["frames"]))
    path = os.path.join(out, "shadows.json")
    write_json(path, {"name": "Shadow study", "frames": job["done"]})
    job["state"], job["json"] = "done", path
    return "done: " + path


def job_step(jid, seconds=6.0):
    job = JOBS[jid]
    if job["state"] == "done":
        return "done: " + job["json"]
    if job["kind"] == "sun":
        return _sun_step(job, seconds)
    if job["kind"] == "shadows":
        return _shadow_step(job, seconds)
    if job["kind"] == "section":
        return section_step(jid)
    if job["kind"] == "occlusion":
        return _occlusion_step(job, seconds)
    raise ValueError("unknown job kind " + job["kind"])


# ---------------------------------------------------------------- isovist

def _floor_under(cam, scene, x, y):
    h = pick(cam, scene, x, y)
    if h is None:
        return None, "missed"
    p = h["point"]
    if h["on"] == "object" and abs(h["normal"].Z) < 0.7:
        # the pin landed on a wall: drop to whatever is below, else the construction plane
        down = scene.hit(rg.Ray3d(p + rg.Vector3d.ZAxis * _tol() * 10, -rg.Vector3d.ZAxis))
        if down is not None:
            return down[1], "dropped from a wall to the floor below"
        return p, "on a wall (nothing below)"
    return p, h["on"]


def isovist(stamp, eye_mm=1600.0, rays=720):
    folder, meta = load_capture(stamp)
    cam = Cam(meta)
    scene = Scene(sc.doc)
    pins = [st for st in meta.get("strokes") or [] if st.get("kind") == "pin"]
    if not pins:
        raise ValueError("drop a pin where the eye stands")
    eye_h = eye_mm * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
    R = scene.bbox.Diagonal.Length * 1.5
    li = ensure_layer("ClaudeMark::Isovists", System.Drawing.Color.FromArgb(46, 144, 250))
    res, overlays, notes = [], [], []
    for st in pins:
        floor, how = _floor_under(cam, scene, *st["points"][0])
        if floor is None:
            notes.append("pin %s: missed everything" % st.get("label"))
            continue
        eye = floor + rg.Vector3d.ZAxis * eye_h
        bb = scene.bbox
        poly, open_rays = [], 0
        for k in range(rays):
            a = 2 * math.pi * k / rays
            d = rg.Vector3d(math.cos(a), math.sin(a), 0)
            h = scene.hit(rg.Ray3d(eye, d), R)
            if h is None:
                # nothing in the way: the view runs to the edge of the model, not to infinity
                open_rays += 1
                ts = []
                if abs(d.X) > 1e-12:
                    ts.append(((bb.Max.X if d.X > 0 else bb.Min.X) - eye.X) / d.X)
                if abs(d.Y) > 1e-12:
                    ts.append(((bb.Max.Y if d.Y > 0 else bb.Min.Y) - eye.Y) / d.Y)
                q = eye + d * max(0.0, min(ts) if ts else R)
            else:
                q = h[1]
            poly.append(rg.Point3d(q.X, q.Y, floor.Z))
        crv = rg.PolylineCurve(poly + [poly[0]])
        amp = rg.AreaMassProperties.Compute(crv)
        at = Rhino.DocObjects.ObjectAttributes()
        at.LayerIndex = li
        at.Name = "Isovist pin %s (ClaudeMark %s)" % (st.get("label"), stamp)
        sc.doc.Objects.AddCurve(crv, at)
        res.append({"label": str(st.get("label")), "origin": [floor.X, floor.Y],
                    "poly": [[q.X, q.Y] for q in poly], "area": amp.Area if amp else 0.0,
                    "open_rays": open_rays, "landed": how})
        overlays.append({"label": str(st.get("label")), "origin_px": list(cam.project(floor)[:2]),
                         "poly_px": [list(cam.project(q)[:2]) for q in poly]})
        if open_rays:
            notes.append("pin %s: %d of %d rays found nothing (open to the edge of the model)" % (st.get("label"), open_rays, rays))
    # obstacles cut at the first pin's eye height
    obst = []
    if res:
        z = (floor.Z + eye_h)
        pl = rg.Plane(rg.Point3d(0, 0, z), rg.Vector3d.ZAxis)
        for pts, cl, lp in _plane_cuts(pl, _visible_objects()):
            obst.append([[p.X, p.Y] for p in pts])
    out = output_dir(meta, folder, stamp)
    path = os.path.join(out, "isovist.json")
    write_json(path, {"name": "Isovist", "unit_m": _meters_per_unit(), "eye_m": eye_mm / 1000.0,
                      "pins": res, "obstacles": obst, "capture": os.path.join(folder, "clean.png"),
                      "overlay_px": overlays, "notes": notes})
    sc.doc.Views.Redraw()
    return path


# ---------------------------------------------------------------- eye-level views

def eye_level(stamp, lens=24.0, eye_mm=1600.0):
    folder, meta = load_capture(stamp)
    cam = Cam(meta)
    scene = Scene(sc.doc)
    strokes = meta.get("strokes") or []
    pins = [st for st in strokes if st.get("kind") == "pin"]
    arrows = [st for st in strokes if st.get("kind") == "arrow"]
    if not pins:
        raise ValueError("drop a pin where the eye stands")
    eye_h = eye_mm * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
    out = output_dir(meta, folder, stamp)
    W, H = [int(v) for v in meta["view"]["size"]]
    made = []
    sv = SavedView()
    vp = sv.view.ActiveViewport
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    try:
        for k, st in enumerate(pins):
            floor, how = _floor_under(cam, scene, *st["points"][0])
            if floor is None:
                continue
            eye = floor + rg.Vector3d.ZAxis * eye_h
            d = None
            if arrows:
                px, py = st["points"][0]
                ar = min(arrows, key=lambda a: math.hypot(a["points"][0][0] - px, a["points"][0][1] - py))
                ta, tb = pick(cam, scene, *ar["points"][0]), pick(cam, scene, *ar["points"][-1])
                if ta and tb:
                    d = tb["point"] - ta["point"]
            if d is None and k + 1 < len(pins):
                nxt, _ = _floor_under(cam, scene, *pins[k + 1]["points"][0])
                if nxt is not None:
                    d = nxt - floor
            if d is None:
                d = rg.Vector3d(cam.direction)
            d.Z = 0
            if d.Length < 1e-9:
                d = rg.Vector3d(0, 1, 0)
            d.Unitize()
            vi = Rhino.DocObjects.ViewportInfo(vp)
            vi.ChangeToPerspectiveProjection(10.0 * eye_h, True, lens)
            vi.SetCameraLocation(eye)
            vi.SetCameraDirection(d)
            vi.SetCameraUp(rg.Vector3d.ZAxis)
            vi.TargetPoint = eye + d * 10.0 * eye_h
            vp.SetViewProjection(vi, True)
            vp.Camera35mmLensLength = lens
            name = "CM eye level %s pin %s" % (stamp, st.get("label"))
            old = sc.doc.NamedViews.FindByName(name)
            if old >= 0:
                sc.doc.NamedViews.Delete(old)
            sc.doc.NamedViews.Add(name, vp.Id)
            with HiddenMarkers():
                bmp = sv.view.CaptureToBitmap(System.Drawing.Size(W, H), mode)
            p = os.path.join(out, name + ".png")
            bmp.Save(p, System.Drawing.Imaging.ImageFormat.Png)
            bmp.Dispose()
            made.append((name, p, how))
    finally:
        sv.restore()
    return made


# ---------------------------------------------------------------- photo match

def photomatch_pairs(stamp, tol_px=10.0):
    """Pairs for cm_photomatch.py: each model match point snapped to the nearest visible corner."""
    folder, meta = load_capture(stamp)
    cam = Cam(meta)
    scene = Scene(sc.doc)
    strokes = meta.get("strokes") or []
    refs = dict((st["label"], st) for st in strokes if st.get("kind") == "ref")
    ms = [st for st in strokes if st.get("kind") == "match"]
    if not ms:
        raise ValueError("no photo-match points on the capture")
    label = ms[0].get("ref") or next((l for l, r in refs.items() if r.get("matches")), None)
    ref = refs.get(label)
    if ref is None or not ref.get("matches"):
        raise ValueError("the photo's match points are missing")
    photo = os.path.join(folder, ref.get("original") or ref["file"])
    img = System.Drawing.Image.FromFile(photo)
    W, H = img.Width, img.Height
    img.Dispose()
    pairs, notes = [], []
    for st in sorted(ms, key=lambda m: m.get("label", 0)):
        k = st.get("label", 0)
        if not (0 < k <= len(ref["matches"])):
            notes.append("model point %d has no photo point" % k)
            continue
        x, y = st["points"][0]
        s = snap(cam, scene, x, y, tol_px)
        if s is not None:
            p, how = s["point"], "snapped %.1f px to a corner" % s["px_error"]
        else:
            h = pick(cam, scene, x, y)
            if h is None:
                notes.append("model point %d hit nothing" % k)
                continue
            p, how = h["point"], "on a surface (no corner within %d px)" % tol_px
        pairs.append({"n": k, "uv": ref["matches"][k - 1], "xyz": [p.X, p.Y, p.Z], "how": how})
    out = output_dir(meta, folder, stamp)
    path = os.path.join(out, "pairs.json")
    write_json(path, {"image_size": [W, H], "pairs": pairs, "photo": photo, "ref": label, "notes": notes})
    return path


def photo_edges(c, out_json):
    """Visible model edges as seen by the solved photo camera, in photo pixels (a hidden-line drawing).

    The hidden-line output is mapped to pixels by an affine fitted on projected scene points; the fit's
    residual on points it wasn't fitted to is reported, so a bad mapping can't pass silently."""
    W, H = c["image_size"]
    f = c["focal_px"]
    loc, d, up = rg.Point3d(*c["location"]), rg.Vector3d(*c["direction"]), rg.Vector3d(*c["up"])
    scene = Scene(sc.doc)
    near = max(_tol() * 100, scene.bbox.Diagonal.Length * 1e-4)
    vi = Rhino.DocObjects.ViewportInfo()
    vi.SetScreenPort(0, int(W), int(H), 0, 0, 0)
    vi.ChangeToPerspectiveProjection(10.0, True, 24.0 * f / min(W, H))
    vi.SetCameraLocation(loc)
    vi.SetCameraDirection(d)
    vi.SetCameraUp(up)
    far = scene.bbox.Diagonal.Length * 4 + (scene.bbox.Center - loc).Length
    vi.SetFrustum(-near * W / 2.0 / f, near * W / 2.0 / f, -near * H / 2.0 / f, near * H / 2.0 / f, near, far)
    prm = rg.HiddenLineDrawingParameters()
    prm.AbsoluteTolerance = _tol()
    prm.IncludeTangentEdges = False
    prm.IncludeHiddenCurves = False
    prm.Flatten = True
    prm.SetViewport(vi)
    for obj in _visible_objects():
        lp_ = sc.doc.Layers[obj.Attributes.LayerIndex].FullPath
        if lp_ == "ClaudeMark" or lp_.startswith("ClaudeMark::"):   # my section lines and isovists
            continue
        for g, lp in _geometry_with_xforms(obj):
            prm.AddGeometry(g, rg.Transform.Identity, lp)
    hld = rg.HiddenLineDrawing.Compute(prm, True)
    T = hld.WorldToHiddenLine
    rightv = rg.Vector3d.CrossProduct(d, up)

    def pinhole(p):
        rel = p - loc
        zc = rel * d
        return (W / 2.0 + f * (rel * rightv) / zc, H / 2.0 - f * (rel * up) / zc) if zc > near else None
    pts = []
    for cpt in scene.bbox.GetCorners():
        for k in (0.25, 0.5, 0.75):
            p = scene.bbox.Center + (cpt - scene.bbox.Center) * k
            px = pinhole(p)
            if px is not None:
                q = rg.Point3d(p)
                q.Transform(T)
                pts.append(((q.X, q.Y), px))
    fit, test = pts[0::2], pts[1::2]
    # least squares affine px = A * hl + b, from the fit half; residual on the test half
    import clr
    sxx = [[0.0] * 3 for _ in range(3)]
    sxu = [0.0] * 3
    sxv = [0.0] * 3
    for (hx, hy), (u, v) in fit:
        row = (hx, hy, 1.0)
        for i in range(3):
            for j in range(3):
                sxx[i][j] += row[i] * row[j]
            sxu[i] += row[i] * u
            sxv[i] += row[i] * v
    m = rg.Matrix(3, 3)
    for i in range(3):
        for j in range(3):
            m[i, j] = sxx[i][j]
    m.Invert(1e-14)
    a = [sum(m[i, j] * sxu[j] for j in range(3)) for i in range(3)]
    b = [sum(m[i, j] * sxv[j] for j in range(3)) for i in range(3)]
    to_px = lambda x, y: (a[0] * x + a[1] * y + a[2], b[0] * x + b[1] * y + b[2])
    resid = max(math.hypot(to_px(hx, hy)[0] - u, to_px(hx, hy)[1] - v) for (hx, hy), (u, v) in test) if test else None
    segs = []
    for seg in hld.Segments:
        if seg.SegmentVisibility != rg.HiddenLineDrawingSegment.Visibility.Visible or seg.CurveGeometry is None:
            continue
        segs.append([[round(c_, 2) for c_ in to_px(q.X, q.Y)] for q in _curve_pts(seg.CurveGeometry, _tol())])
    write_json(out_json, {"image_size": [W, H], "segments": segs, "mapping_residual_px": resid})
    return out_json


def photomatch_render_start(stamp, camera_json, proposal_ids, max_width=2400, sun_from=None):
    """Render the proposal alone in V-Ray from the solved camera.

    Opens a view with the photo's exact aspect, hides every other visible object (and remembers exactly
    which), sets V-Ray's core output to a file and starts the render. It runs on after this returns:
    wait for the PNG, then call photomatch_render_finish(state), which shows exactly those objects again,
    closes the view and clears the output path. sun_from: an EXIF "YYYY:MM:DD HH:MM:SS" to set the sun."""
    import sys as _sys
    vray_dir = r"C:\Program Files\Chaos\V-Ray\V-Ray for Rhinoceros"
    if vray_dir not in _sys.path:
        _sys.path.append(vray_dir)
    import rhVRay as vray
    folder, meta = load_capture(stamp)
    with open(camera_json) as fh:
        c = json.load(fh)
    W, H = c["image_size"]
    k = min(1.0, max_width / float(W))
    rw, rh = int(round(W * k)), int(round(H * k))
    out = output_dir(meta, folder, stamp)
    png = "montage render %s.png" % datetime.datetime.now().strftime("%H%M%S")
    keep = set(str(i) for i in proposal_ids)
    hidden = []
    for obj in _visible_objects():
        if str(obj.Id) not in keep and obj.ObjectType != Rhino.DocObjects.ObjectType.Light:
            if sc.doc.Objects.Hide(obj.Id, True):
                hidden.append(str(obj.Id))
    view, restore, aspect_err = aspect_view("CM montage", W, H)
    vp = view.ActiveViewport
    f = c["focal_px"]
    lens = 24.0 * f / min(W, H)
    loc, d = rg.Point3d(*c["location"]), rg.Vector3d(*c["direction"])
    vi = Rhino.DocObjects.ViewportInfo(vp)
    vi.ChangeToPerspectiveProjection(10.0, True, lens)
    vi.SetCameraLocation(loc)
    vi.SetCameraDirection(d)
    vi.SetCameraUp(rg.Vector3d(*c["up"]))
    vi.TargetPoint = loc + d * 10.0
    vp.SetViewProjection(vi, True)
    vp.Camera35mmLensLength = lens
    vp.DisplayMode = Rhino.Display.DisplayModeDescription.FindByName("Shaded")
    sc.doc.Views.ActiveView = view
    sun_keep = None
    if sun_from:
        ds = sc.doc.RenderSettings.Sun
        sun_keep = (ds.Enabled, ds.ManualControlOn, ds.Altitude, ds.Azimuth)
        dt = datetime.datetime.strptime(sun_from, "%Y:%m:%d %H:%M:%S")
        s = _sun_object(dt.date(), dt.hour, dt.minute)
        ds.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
        ds.Enabled, ds.ManualControlOn = True, True
        ds.Altitude, ds.Azimuth = s.Altitude, s.Azimuth
        ds.EndChange()
    o = vray.Scene.Plugin("/SettingsOutput")
    with vray.Scene.Transaction():
        o.Param("img_width").Value = rw
        o.Param("img_height").Value = rh
        o.Param("img_dir").Value = out + "\\"
        o.Param("img_file").Value = png
    vray.Scene.Refresh()
    vray.Render(vray.RenderModes.RM_PRODUCTION, vray.RenderEngines.RE_CPU, 0)
    state = {"view": view, "restore": restore, "hidden": hidden, "png": os.path.join(out, png),
             "sun_keep": sun_keep, "size": [rw, rh], "aspect_error": aspect_err}
    sc.sticky["cm_montage_state"] = state
    return state["png"], len(hidden), aspect_err


def photomatch_render_finish(state=None):
    import sys as _sys
    vray_dir = r"C:\Program Files\Chaos\V-Ray\V-Ray for Rhinoceros"
    if vray_dir not in _sys.path:
        _sys.path.append(vray_dir)
    import rhVRay as vray
    state = state or sc.sticky.pop("cm_montage_state", None)
    if state is None:
        return "nothing to finish"
    shown = 0
    for sid in state["hidden"]:
        if sc.doc.Objects.Show(System.Guid(sid), True):
            shown += 1
    o = vray.Scene.Plugin("/SettingsOutput")
    with vray.Scene.Transaction():
        o.Param("img_dir").Value = ""
        o.Param("img_file").Value = ""
    vray.Scene.Refresh()
    if state.get("sun_keep"):
        ds = sc.doc.RenderSettings.Sun
        ds.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
        ds.Enabled, ds.ManualControlOn, ds.Altitude, ds.Azimuth = state["sun_keep"]
        ds.EndChange()
    try:
        close_aspect_view(state["view"], state["restore"])
    except Exception:
        pass
    sc.doc.Views.Redraw()
    return "shown %d of %d hidden objects; view closed; output path cleared" % (shown, len(state["hidden"]))


def montage_occlusion(stamp, camera_json, proposal_ids, render_png):
    """Start a job that masks out the proposal pixels that existing context hides from the photo camera.

    For every pixel the proposal covers in the render (alpha > 0) a ray through the solved camera finds
    the first surface; if it belongs to the context, that pixel is hidden. Run job_step(jid) until done;
    it writes <render>_mask.png (white = proposal visible) for cm_post.py montage."""
    import clr
    clr.AddReference("System.Drawing")
    from System.Drawing.Imaging import ImageLockMode, PixelFormat
    from System.Runtime.InteropServices import Marshal
    with open(camera_json) as fh:
        c = json.load(fh)
    bmp = System.Drawing.Bitmap(render_png)
    rw, rh = bmp.Width, bmp.Height
    rect = System.Drawing.Rectangle(0, 0, rw, rh)
    data = bmp.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb)
    buf = System.Array.CreateInstance(System.Byte, data.Stride * rh)
    Marshal.Copy(data.Scan0, buf, 0, buf.Length)
    stride = data.Stride
    bmp.UnlockBits(data)
    bmp.Dispose()
    todo = [(x, y) for y in range(rh) for x in range(rw) if buf[y * stride + x * 4 + 3] > 0]
    W, H = c["image_size"]
    jid = "occlusion-%s-%d" % (stamp, int(time.time()))
    JOBS[jid] = {"kind": "occlusion", "png": render_png, "size": [rw, rh], "todo": todo, "next": 0,
                 "hidden": set(), "proposal": set(str(i) for i in proposal_ids), "fr": c["focal_px"] * rw / float(W),
                 "loc": c["location"], "d": c["direction"], "up": c["up"], "state": "running"}
    return jid, len(todo)


def _occlusion_step(job, seconds):
    import clr
    from System.Drawing.Imaging import ImageLockMode, PixelFormat
    from System.Runtime.InteropServices import Marshal
    t0 = time.time()
    scene = job.setdefault("_scene", Scene(sc.doc))
    loc, d, up = rg.Point3d(*job["loc"]), rg.Vector3d(*job["d"]), rg.Vector3d(*job["up"])
    right = rg.Vector3d.CrossProduct(d, up)
    rw, rh = job["size"]
    fr = job["fr"]
    todo = job["todo"]
    while job["next"] < len(todo) and time.time() - t0 < seconds:
        x, y = todo[job["next"]]
        ray_d = d + right * ((x + 0.5 - rw / 2.0) / fr) - up * ((y + 0.5 - rh / 2.0) / fr)
        ray_d.Unitize()
        h = scene.hit(rg.Ray3d(loc, ray_d))
        if h is not None and h[3] not in job["proposal"]:
            job["hidden"].add(job["next"])
        job["next"] += 1
    if job["next"] < len(todo):
        return "running: %d of %d proposal pixels" % (job["next"], len(todo))
    mask = System.Drawing.Bitmap(rw, rh, PixelFormat.Format32bppArgb)
    rect = System.Drawing.Rectangle(0, 0, rw, rh)
    data = mask.LockBits(rect, ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb)
    buf = System.Array.CreateInstance(System.Byte, data.Stride * rh)
    for k, (x, y) in enumerate(todo):
        if k in job["hidden"]:
            continue
        i = y * data.Stride + x * 4
        buf[i] = buf[i + 1] = buf[i + 2] = buf[i + 3] = 255
    Marshal.Copy(buf, 0, data.Scan0, buf.Length)
    mask.UnlockBits(data)
    out = os.path.splitext(job["png"])[0] + "_mask.png"
    mask.Save(out, System.Drawing.Imaging.ImageFormat.Png)
    mask.Dispose()
    job["state"], job["json"] = "done", out
    job.pop("_scene", None)
    return "done: %s (%d of %d proposal pixels hidden by context)" % (out, len(job["hidden"]), len(todo))


def photomatch_apply(stamp, camera_json, preview_width=1600):
    """Put the solved camera into a named view and capture model edges at the photo's aspect for the
    overlay check. Uses a temporary floating view with the photo's aspect, so the field of view is exact."""
    folder, meta = load_capture(stamp)
    with open(camera_json) as fh:
        c = json.load(fh)
    W, H = c["image_size"]
    out = output_dir(meta, folder, stamp)
    pw = int(preview_width)
    ph = int(round(pw * H / float(W)))
    tmp, restore, aspect_err = aspect_view("CM photo match", W, H)
    name = "CM photo match %s" % stamp
    edges = os.path.join(out, "edges.png")
    check = None
    try:
        vp = tmp.ActiveViewport
        f = c["focal_px"]
        lens = 24.0 * f / min(W, H)          # Rhino's lens spans 24 mm across the view's shorter side
        loc = rg.Point3d(*c["location"])
        d = rg.Vector3d(*c["direction"])
        vi = Rhino.DocObjects.ViewportInfo(vp)
        vi.ChangeToPerspectiveProjection(10.0, True, lens)
        vi.SetCameraLocation(loc)
        vi.SetCameraDirection(d)
        vi.SetCameraUp(rg.Vector3d(*c["up"]))
        vi.TargetPoint = loc + d * 10.0
        vp.SetViewProjection(vi, True)
        vp.Camera35mmLensLength = lens
        old = sc.doc.NamedViews.FindByName(name)
        if old >= 0:
            sc.doc.NamedViews.Delete(old)
        sc.doc.NamedViews.Add(name, vp.Id)
        # Model edges for the overlay check come from the hidden-line engine, not a display-mode capture:
        # Pen and Technical captures drew hidden lines or lost edges here.
        edges = photo_edges(c, os.path.join(out, "edges.json"))
        # registration check: a point ahead of the camera through the view vs the solved pinhole
        Mv = vp.GetTransform(Rhino.DocObjects.CoordinateSystem.World, Rhino.DocObjects.CoordinateSystem.Screen)
        q = loc + d * 10.0 + rg.Vector3d(*c["up"]) * 2.0
        v = [sum(Mv[i, j] * cc for j, cc in enumerate((q.X, q.Y, q.Z, 1.0))) for i in range(4)]
        sx, sy = v[0] / v[3] * W / float(vp.Size.Width), v[1] / v[3] * H / float(vp.Size.Height)
        rel = q - loc
        zc = rel * d
        upv = rg.Vector3d(*c["up"])
        rightv = rg.Vector3d.CrossProduct(d, upv)
        px = W / 2.0 + f * (rel * rightv) / zc
        py = H / 2.0 - f * (rel * upv) / zc
        check = math.hypot(sx - px, sy - py)
    finally:
        close_aspect_view(tmp, restore)
    return name, edges, {"aspect_error": aspect_err, "registration_px_at_photo_size": check}
