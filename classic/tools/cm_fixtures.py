# -*- coding: utf-8 -*-
"""ClaudeMark light fixtures, modelled (IronPython 2.7, after cm_core.py and cm_render.py).

Light that comes "from thin air" reads as fake. A style's lights get bodies here: NURBS solids on a layer of the user’s,
with the emitting part (a lens, a diffuser) as a separate surface that cm_render's rig turns into a V-Ray mesh light.

    spot_fixture(lens, aim, mount_z, layer)     -> {"body": [ids], "lens": surface id, "source": point for the V-Ray spot}
    bottom_rails(layer_contains, toward)        -> the bottom rail of each balustrade leaf: ends, outward side, outer face
    led_trim(rail, layer)                        -> {"body": channel id, "emitter": diffuser surface id}
    up_hit(x, y, z, reach)                       -> the first surface straight above a point (what to hang from)
"""
import math
import System
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc

Z = rg.Vector3d.ZAxis


def _layer(path, colour=None):
    i = ensure_layer(path)
    if colour is not None:
        ly = sc.doc.Layers[i]
        ly.Color = colour
        ly.CommitChanges()
    return i


def _add(brep, li, name, tags=None):
    at = Rhino.DocObjects.ObjectAttributes()
    at.LayerIndex = li
    at.Name = name
    for k, v in (tags or {}).items():
        at.SetUserString(k, v)
    return sc.doc.Objects.AddBrep(brep, at)


def _box(origin, xaxis, yaxis, x0, x1, y0, y1, z0, z1):
    """A box on a horizontal frame, z intervals absolute. x intervals here are symmetric, so flipping x to keep the
    frame's z pointing up changes nothing else (a flipped frame built boxes downward)."""
    if rg.Vector3d.CrossProduct(xaxis, yaxis).Z < 0:
        xaxis = -xaxis
    pl = rg.Plane(origin, xaxis, yaxis)
    return rg.Box(pl, rg.Interval(x0, x1), rg.Interval(y0, y1), rg.Interval(z0, z1)).ToBrep()


def _cyl(base, axis, radius, length):
    a = rg.Vector3d(axis)
    a.Unitize()
    return rg.Cylinder(rg.Circle(rg.Plane(base, a), radius), length).ToBrep(True, True)


def up_hit(x, y, z, reach=9000):
    """Height of the first surface straight above (x, y, z) within reach, and its layer name (None if open sky)."""
    tol = sc.doc.ModelAbsoluteTolerance
    line = rg.Line(rg.Point3d(x, y, z), rg.Point3d(x, y, z + reach))
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.VisibleFilter = True
    best = None
    for o in sc.doc.Objects.GetObjectList(es):
        if not sc.doc.Layers[o.Attributes.LayerIndex].IsVisible:
            continue
        bb = o.Geometry.GetBoundingBox(False)
        if not (bb.Min.X - 1 <= x <= bb.Max.X + 1 and bb.Min.Y - 1 <= y <= bb.Max.Y + 1 and bb.Max.Z > z and bb.Min.Z < z + reach):
            continue
        g = o.Geometry
        if isinstance(g, rg.Extrusion):
            g = g.ToBrep()
        zs = []
        if isinstance(g, rg.Brep):
            ok, crvs, pts = rg.Intersect.Intersection.CurveBrep(line.ToNurbsCurve(), g, tol)
            zs = [p.Z for p in pts] if ok else []
        elif isinstance(g, rg.Mesh):
            zs = [p.Z for p in (rg.Intersect.Intersection.MeshLine(g, line) or [])]
        for hz in zs:
            if best is None or hz < best[0]:
                best = (hz, sc.doc.Layers[o.Attributes.LayerIndex].Name)
    return best


def spot_fixture(lens, aim, mount_z, layer, name="spot", radius=110.0, length=360.0):
    """A theatre-style spot hung from the structure above: black can (lens at its front), yoke with pivot knobs,
    drop rod and ceiling plate. The lens is its own disc (make it a visible mesh light); put the V-Ray spot at
    "source", just in front of the lens, so the beam leaves the fixture."""
    li = _layer(layer)
    p_lens, p_aim = rg.Point3d(*lens), rg.Point3d(*aim)
    a = p_aim - p_lens
    a.Unitize()
    s = rg.Vector3d.CrossProduct(a, Z)
    if s.Length < 1e-6:
        s = rg.Vector3d.XAxis
    s.Unitize()
    h = rg.Vector3d.CrossProduct(Z, s)          # horizontal, square to the pivot axis
    rear = p_lens - a * length
    centre = p_lens - a * (length / 2.0)
    tag = {"cm_fixture": name}
    body = []
    body.append(_add(_cyl(rear, a, radius, length), li, name + " can", tag))
    body.append(_add(_cyl(p_lens - a * 40.0, a, radius + 14.0, 40.0), li, name + " bezel", tag))
    body.append(_add(_cyl(rear - a * 30.0, a, radius * 0.55, 30.0), li, name + " rear cap", tag))
    top = centre.Z + radius + 150.0
    for sign in (1, -1):
        p = centre + s * (sign * (radius + 26.0))
        body.append(_add(_box(rg.Point3d(p.X, p.Y, 0), s, h, -6, 6, -24, 24, p.Z - 30.0, top), li, name + " yoke arm", tag))
        body.append(_add(_cyl(centre + s * (sign * (radius + 4.0)), s * sign, 22.0, 34.0), li, name + " pivot knob", tag))
    mid = rg.Point3d(centre.X, centre.Y, 0)
    body.append(_add(_box(mid, s, h, -(radius + 32.0), radius + 32.0, -24, 24, top, top + 12.0), li, name + " yoke bar", tag))
    if mount_z and mount_z > top + 20:
        body.append(_add(_cyl(rg.Point3d(centre.X, centre.Y, top + 12.0), Z, 16.0, mount_z - top - 12.0 + 5.0), li, name + " drop rod", tag))
        body.append(_add(_box(mid, s, h, -80, 80, -80, 80, mount_z - 12.0, mount_z), li, name + " ceiling plate", tag))
    disc = rg.Brep.CreatePlanarBreps(rg.Circle(rg.Plane(p_lens + a * 3.0, a), radius - 8.0).ToNurbsCurve(), sc.doc.ModelAbsoluteTolerance)[0]
    f = disc.Faces[0]
    n = f.NormalAt(f.Domain(0).Mid, f.Domain(1).Mid)
    if f.OrientationIsReversed:
        n = -n
    if n * a < 0:
        disc.Flip()
    lens_id = _add(disc, li, name + " lens", tag)
    src = p_lens + a * 25.0
    return {"body": [str(i) for i in body], "lens": str(lens_id), "source": [src.X, src.Y, src.Z],
            "direction": [a.X, a.Y, a.Z]}


def bottom_rails(layer_contains, toward):
    """The bottom rail of each balustrade leaf (objects named '... leaf N frame ...' on layers containing the text):
    plan ends p0/p1 at the rail's underside, the outward unit normal (the side facing `toward`) and the outer face."""
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.HiddenObjects = False
    leaves = {}
    for o in sc.doc.Objects.GetObjectList(es):
        path = sc.doc.Layers[o.Attributes.LayerIndex].FullPath
        if layer_contains not in path:
            continue
        nm = o.Name or ""
        if " leaf " not in nm:
            continue
        try:
            leaf = int(nm.split(" leaf ")[1].split()[0])
        except Exception:
            continue
        bb = o.Geometry.GetBoundingBox(True)
        if bb.Max.Z - bb.Min.Z > 120:
            continue                                   # verticals; rails are thin
        if leaf not in leaves or bb.Min.Z < leaves[leaf][1].Min.Z:
            leaves[leaf] = (o, bb)
    tp = rg.Point3d(toward[0], toward[1], 0)
    out = []
    for leaf in sorted(leaves):
        o, bb = leaves[leaf]
        g = o.Geometry
        if isinstance(g, rg.Extrusion):
            g = g.ToBrep()
        pts = [v.Location for v in g.Vertices]
        best = (0, None, None)
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                d = (pts[i].X - pts[j].X) ** 2 + (pts[i].Y - pts[j].Y) ** 2
                if d > best[0]:
                    best = (d, pts[i], pts[j])
        a, b = best[1], best[2]
        u = rg.Vector3d(b.X - a.X, b.Y - a.Y, 0)
        u.Unitize()
        n = rg.Vector3d(-u.Y, u.X, 0)
        mid = rg.Point3d((a.X + b.X) / 2.0, (a.Y + b.Y) / 2.0, 0)
        if (tp - mid) * n < 0:
            n = -n
        outer = max((rg.Point3d(p.X, p.Y, 0) - mid) * n for p in pts)
        out.append({"leaf": leaf, "p0": [a.X, a.Y], "p1": [b.X, b.Y], "n": [n.X, n.Y], "outer": outer,
                    "z": bb.Min.Z, "zt": bb.Max.Z})
    return out


def led_trim(rail, layer, name="trim", depth=24.0, height=22.0, inset=20.0):
    """An LED channel on the outer face of a bottom rail, its full length less an inset at each end, with the
    diffuser (a separate surface, 2 mm proud of the channel's outer face) for the rig to light."""
    li = _layer(layer)
    p0 = rg.Point3d(rail["p0"][0], rail["p0"][1], 0)
    p1 = rg.Point3d(rail["p1"][0], rail["p1"][1], 0)
    u = p1 - p0
    L = u.Length
    u.Unitize()
    n = rg.Vector3d(rail["n"][0], rail["n"][1], 0)
    mid = rg.Point3d((p0.X + p1.X) / 2.0, (p0.Y + p1.Y) / 2.0, 0) + n * rail["outer"]
    z0 = rail["z"]
    tag = {"cm_fixture": name}
    body = _add(_box(mid, u, n, -L / 2.0 + inset, L / 2.0 - inset, 0, depth, z0, z0 + height), li, name + " channel", tag)
    c = [mid + u * (-L / 2.0 + inset + 5) + n * (depth + 2), mid + u * (L / 2.0 - inset - 5) + n * (depth + 2)]
    pts = [rg.Point3d(c[0].X, c[0].Y, z0 + 4), rg.Point3d(c[1].X, c[1].Y, z0 + 4),
           rg.Point3d(c[1].X, c[1].Y, z0 + height - 4), rg.Point3d(c[0].X, c[0].Y, z0 + height - 4)]
    em = rg.Brep.CreateFromCornerPoints(pts[0], pts[1], pts[2], pts[3], sc.doc.ModelAbsoluteTolerance)
    f = em.Faces[0]
    nn = f.NormalAt(f.Domain(0).Mid, f.Domain(1).Mid)
    if f.OrientationIsReversed:
        nn = -nn
    if nn * n < 0:
        em.Flip()
    eid = _add(em, li, name + " diffuser", tag)
    return {"body": str(body), "emitter": str(eid)}
