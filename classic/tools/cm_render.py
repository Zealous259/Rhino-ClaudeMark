# -*- coding: utf-8 -*-
"""ClaudeMark render styles, Rhino side (IronPython 2.7, after cm_core.py):

    exec(open(r"<kit folder>\\tools\\cm_core.py").read())
    exec(open(r"<kit folder>\\tools\\cm_render.py").read())
    d = style_begin(stamp)                       # snapshot the user’s V-Ray settings + sun (once per capture)
    style_light(stamp, style)                    # sun + V-Ray sun/sky for one style: ITS OWN MCP CALL
    png = style_preview(stamp, "A", vropt)       # LoadSettings + render from the capture camera, returns at once
    style_preview_end()                          # once the PNG exists
    style_restore(stamp)                         # the user’s settings and sun back
    style_commit(stamp, style, vropt, name)      # the picked style for real: settings, sun, named view, render

V-Ray picks up a sun change on Rhino's next idle, so a render started in the same call as style_light can
render the old sun (seen 2026-10-18): set the light, return, then preview.
LoadSettings MERGES: plugins the loaded file doesn't name stay in the scene (a render element added once stays).
Previews only ever add an unreferenced TexSky; style_commit adds the Light Mix element on purpose.
"""
import os
import sys
import json
import datetime
import math

import System
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc

VRAY_DIR = r"C:\Program Files\Chaos\V-Ray\V-Ray for Rhinoceros"
SUN_PARAMS = {"model": "sky_model", "turbidity": "turbidity", "ozone": "ozone", "intensity": "intensity_multiplier",
              "size": "size_multiplier", "filter": "filter_color", "water_vapour": "water_vapour"}
SITE = {"lat": -37.832, "lon": 144.957, "tz": 10.0}
SUN_KEEP = ["sky_model", "turbidity", "ozone", "intensity_multiplier", "size_multiplier", "filter_color", "water_vapour",
            "horiz_illum", "invisible", "clouds_on", "clouds_density", "clouds_variety", "clouds_cirrus_amount",
            "clouds_ground_shadows", "altitude"]


def _vray():
    if VRAY_DIR not in sys.path:
        sys.path.append(VRAY_DIR)
    import rhVRay
    return rhVRay


def _style_dir(stamp):
    folder, meta = load_capture(stamp)
    d = os.path.join(output_dir(meta, folder, stamp), "styles")
    if not os.path.isdir(d):
        os.makedirs(d)
    return folder, meta, d


def _sun_state():
    ds = sc.doc.RenderSettings.Sun
    st = {"Enabled": ds.Enabled, "ManualControlOn": ds.ManualControlOn, "Altitude": ds.Altitude,
          "Azimuth": ds.Azimuth, "Latitude": ds.Latitude, "Longitude": ds.Longitude, "TimeZone": ds.TimeZone,
          "DaylightSavingOn": ds.DaylightSavingOn, "North": ds.North,
          "DateTime": ds.GetDateTime(System.DateTimeKind.Local).ToString("yyyy-MM-dd HH:mm:ss")}
    p = _vray().Scene.Plugin("/Rhino Document Sun")
    vr = {}
    if p:
        for k in SUN_KEEP:
            try:
                v = p.Param(k).Value
            except Exception:
                continue
            if hasattr(v, "__iter__") and not isinstance(v, basestring):
                vr[k] = [float(c) for c in v]
            elif isinstance(v, bool):
                vr[k] = v
            elif isinstance(v, (int, long)):
                vr[k] = int(v)
            else:
                try:
                    vr[k] = float(v)          # .NET Single isn't JSON serialisable
                except Exception:
                    vr[k] = str(v)
    st["vray_sun"] = vr
    return st


def _set_vray_sun(values):
    vray = _vray()
    p = vray.Scene.Plugin("/Rhino Document Sun")
    if not p or not values:
        return
    with vray.Scene.Transaction():
        for k, v in values.items():
            try:
                if isinstance(v, (list, tuple)):
                    v = System.Array[System.Single]([float(c) for c in v])
                p.Param(k).Value = v
            except Exception as ex:
                Rhino.RhinoApp.WriteLine("ClaudeMark: sun param %s not set (%s)" % (k, ex))


def style_begin(stamp):
    """Snapshot the user’s V-Ray settings and sun before any style touches them. Once per capture: a second call keeps
    the first snapshot, so a crashed run can never overwrite the user’s real settings with a style's."""
    folder, meta, d = _style_dir(stamp)
    before = os.path.join(d, "before.vropt")
    if not os.path.exists(before):
        if not _vray().Scene.SaveSettings(before):
            raise Exception("V-Ray SaveSettings failed; nothing changed")
    if not os.path.exists(os.path.join(d, "before_sun.json")):
        tmp = os.path.join(d, ".before_sun.json")         # renamed only once complete: a failed dump leaves no file
        write_json(tmp, _sun_state())
        os.rename(tmp, os.path.join(d, "before_sun.json"))
    return d


def style_light(stamp, style):
    """Set the sun and V-Ray's sun and sky for one style. style["sun"] is resolved (cm_style.py resolve): a date
    and standard time at the site, an altitude and azimuth, or off. Returns the sun Rhino ended up with."""
    folder, meta, d = _style_dir(stamp)
    sun = style.get("sun") or {}
    ds = sc.doc.RenderSettings.Sun
    ds.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
    try:
        if sun.get("off"):
            ds.Enabled = False
        elif sun.get("date"):
            site = style.get("site") or SITE
            ds.Enabled, ds.ManualControlOn = True, False
            ds.Latitude, ds.Longitude, ds.TimeZone = site["lat"], site["lon"], site["tz"]
            ds.DaylightSavingOn = False             # times are standard time; labels carry daylight time
            dt = datetime.datetime.strptime(sun["date"] + " " + sun["time"], "%Y-%m-%d %H:%M")
            ds.SetDateTime(System.DateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, 0), System.DateTimeKind.Local)
        elif "alt" in sun and "az" in sun:
            ds.Enabled, ds.ManualControlOn = True, True
            ds.Altitude, ds.Azimuth = float(sun["alt"]), float(sun["az"])
    finally:
        ds.EndChange()
    sky = style.get("sky") or {}
    with open(os.path.join(d, "before_sun.json")) as fh:
        vals = dict(json.load(fh).get("vray_sun") or {})       # start from the user’s sun, so styles don't leak into each other
    vals.update(dict((SUN_PARAMS[k], v) for k, v in sky.items() if k in SUN_PARAMS))
    cl = sky.get("clouds")
    vals["clouds_on"] = bool(cl)
    if cl:
        vals.update({"clouds_density": float(cl.get("density", 0.5)), "clouds_variety": float(cl.get("variety", 0.3)),
                     "clouds_cirrus_amount": float(cl.get("cirrus", 0.2))})
    vals.update(sky.get("raw") or {})
    _set_vray_sun(vals)
    return {"enabled": ds.Enabled, "alt": round(ds.Altitude, 2), "az": round(ds.Azimuth, 2)}


def _camera_view(meta, title):
    W, H = [int(v) for v in meta["view"]["size"]]
    view, restore, err = aspect_view(title, W, H)
    vp = view.ActiveViewport
    vp.SetViewProjection(viewport_info_from_meta(meta, vp), True)
    vp.DisplayMode = Rhino.Display.DisplayModeDescription.FindByName("Shaded")
    sc.doc.Views.ActiveView = view
    view.Redraw()
    return view, restore, err


def _render(out_dir, name, w, h, minutes):
    vray = _vray()
    o = vray.Scene.Plugin("/SettingsOutput")
    smp = vray.Scene.Plugin("/SettingsImageSampler")
    if "cm_style_keep" not in sc.sticky:       # the user’s size and time limit, put back by style_preview_end
        sc.sticky["cm_style_keep"] = (o.Param("img_width").Value, o.Param("img_height").Value,
                                      smp.Param("progressive_maxTime").Value)
    with vray.Scene.Transaction():
        o.Param("img_width").Value = int(w)
        o.Param("img_height").Value = int(h)
        o.Param("img_dir").Value = out_dir.rstrip("\\") + "\\"
        o.Param("img_file").Value = name
        if minutes:
            smp.Param("progressive_maxTime").Value = float(minutes)
    vray.Scene.Refresh()
    vray.Render(vray.RenderModes.RM_PRODUCTION, vray.RenderEngines.RE_CPU, 0)


def _render_later(out_dir, name, w, h, minutes, delay_ms=400):
    """Start the render from a one-shot UI timer, AFTER this MCP call has returned. On a big model rhVRay.Render
    blocks while V-Ray exports the scene (about a minute for SMM Building), which times the MCP out (2026-10-18)."""
    import System.Windows.Forms as wf
    timer = wf.Timer()
    timer.Interval = int(delay_ms)

    def tick(sender, e):
        timer.Stop()
        try:
            _render(out_dir, name, w, h, minutes)
        except Exception as ex:
            Rhino.RhinoApp.WriteLine("ClaudeMark: render didn't start (%s)" % ex)
        finally:
            sc.sticky.pop("cm_render_timer", None)
    timer.Tick += tick
    sc.sticky["cm_render_timer"] = (timer, tick)       # a strong reference: a collected delegate never fires
    timer.Start()


def style_preview(stamp, label, vropt, long_side=960, minutes=1.0):
    """Load the style's settings and start a quick V-Ray render from the capture's camera. Returns the PNG path at
    once; wait for it (the .denoiser.png is the clean pass), then call style_preview_end()."""
    folder, meta, d = _style_dir(stamp)
    if not os.path.exists(os.path.join(d, "before.vropt")):
        raise Exception("style_begin(stamp) first: no snapshot of the user’s settings yet")
    vray = _vray()
    if not vray.Scene.LoadSettings(vropt):
        raise Exception("LoadSettings failed: " + vropt)
    W, H = [int(v) for v in meta["view"]["size"]]
    k = long_side / float(max(W, H))
    w, h = int(round(W * k)), int(round(H * k))
    prev = os.path.join(d, "previews")
    if not os.path.isdir(prev):
        os.makedirs(prev)
    old = sc.sticky.pop("cm_style_view", None)
    if old:
        try:
            close_aspect_view(old[0], old[1])
        except Exception:
            pass
    view, restore, err = _camera_view(meta, "CM style " + label)
    sc.sticky["cm_style_view"] = (view, restore)
    for ext in (".png", ".denoiser.png", ".effectsResult.png"):
        p = os.path.join(prev, label + ext)
        if os.path.exists(p):
            os.remove(p)
    _render_later(prev, label + ".png", w, h, minutes)
    return os.path.join(prev, label + ".png")


def style_preview_end():
    """Close the preview view and clear the output path (it persists in the user’s settings otherwise)."""
    vray = _vray()
    o = vray.Scene.Plugin("/SettingsOutput")
    keep = sc.sticky.pop("cm_style_keep", None)
    with vray.Scene.Transaction():
        o.Param("img_dir").Value = ""
        o.Param("img_file").Value = ""
        if keep:
            o.Param("img_width").Value, o.Param("img_height").Value = keep[0], keep[1]
            vray.Scene.Plugin("/SettingsImageSampler").Param("progressive_maxTime").Value = keep[2]
    vray.Scene.Refresh()
    old = sc.sticky.pop("cm_style_view", None)
    if old:
        close_aspect_view(old[0], old[1])
    return "closed"


def _restore_sun(st):
    ds = sc.doc.RenderSettings.Sun
    ds.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
    try:
        ds.Latitude, ds.Longitude, ds.TimeZone = st["Latitude"], st["Longitude"], st["TimeZone"]
        ds.DaylightSavingOn, ds.North = st["DaylightSavingOn"], st["North"]
        dt = datetime.datetime.strptime(st["DateTime"], "%Y-%m-%d %H:%M:%S")
        ds.SetDateTime(System.DateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second), System.DateTimeKind.Local)
        ds.ManualControlOn = st["ManualControlOn"]
        if st["ManualControlOn"]:
            ds.Altitude, ds.Azimuth = st["Altitude"], st["Azimuth"]
        ds.Enabled = st["Enabled"]
    finally:
        ds.EndChange()
    _set_vray_sun(st.get("vray_sun") or {})


def style_restore(stamp):
    """The user’s settings and sun back as they were at style_begin."""
    folder, meta, d = _style_dir(stamp)
    try:
        style_preview_end()
    except Exception:
        pass
    before = os.path.join(d, "before.vropt")
    ok = _vray().Scene.LoadSettings(before)
    with open(os.path.join(d, "before_sun.json")) as fh:
        _restore_sun(json.load(fh))
    _vray().Scene.Refresh()
    return "restored settings (%s) and sun" % ok


def style_commit(stamp, style, vropt, name, long_side=1920, minutes=4.0, render=True):
    """Set the picked style up for real: its settings (with Light Mix, so the user can rebalance lights in the VFB and
    Transfer to Scene), a named view from the capture camera, the settings saved as <name>.vropt, and a render.
    Call style_light(stamp, style) in the MCP call BEFORE this one. Returns the render's PNG path (or the folder)."""
    folder, meta, d = _style_dir(stamp)
    vray = _vray()
    if not vray.Scene.LoadSettings(vropt):
        raise Exception("LoadSettings failed: " + vropt)
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    final = os.path.join(d, "final")
    if not os.path.isdir(final):
        os.makedirs(final)
    view, restore, err = _camera_view(meta, "CM style " + safe)
    vname = "CM style " + safe
    i = sc.doc.NamedViews.FindByName(vname)
    if i >= 0:
        sc.doc.NamedViews.Delete(i)
    sc.doc.NamedViews.Add(vname, view.ActiveViewportID)
    vray.Scene.SaveSettings(os.path.join(final, safe + ".vropt"))
    if not render:
        close_aspect_view(view, restore)
        return final
    W, H = [int(v) for v in meta["view"]["size"]]
    k = long_side / float(max(W, H))
    sc.sticky["cm_style_view"] = (view, restore)
    for ext in (".png", ".denoiser.png", ".effectsResult.png"):
        p = os.path.join(final, safe + ext)
        if os.path.exists(p):
            os.remove(p)
    _render_later(final, safe + ".png", int(round(W * k)), int(round(H * k)), minutes)
    return os.path.join(final, safe + ".png")


# ---------------------------------------------------------------- temporary light rigs (mesh lights)
#
# A style can bring its own light sources (styles.json "rig"): strips and panels as planar NURBS surfaces on
# ClaudeMark::style lights, made into V-Ray mesh lights one group at a time. rhVRay can't create lights, so each
# group needs the MCP's run_command("_-vrayLightMesh _Enter") between rig_build and rig_params. Mesh lights, not Rhino lights: they're geometry on a VISIBLE layer, so V-Ray's layer
# re-sync leaves them on and the Rendered viewport doesn't flood.

RIG_LAYER = "ClaudeMark::style lights"


def _rig_surface(p0, p1, p2, p3, want_normal):
    pts = [rg.Point3d(*p) for p in (p0, p1, p2, p3)]
    b = rg.Brep.CreateFromCornerPoints(pts[0], pts[1], pts[2], pts[3], sc.doc.ModelAbsoluteTolerance)
    if b is None:
        raise Exception("rig surface failed at %s" % (p0,))
    f = b.Faces[0]
    u, v = f.Domain(0).Mid, f.Domain(1).Mid
    n = f.NormalAt(u, v)
    if f.OrientationIsReversed:
        n = -n
    if n * rg.Vector3d(*want_normal) < 0:
        b.Flip()
    return b


def _strip_corners(a, b, width, normal):
    a, b, n = rg.Vector3d(*a), rg.Vector3d(*b), rg.Vector3d(*normal)
    u = b - a
    u.Unitize()
    n = n - u * (n * u)
    n.Unitize()
    w = rg.Vector3d.CrossProduct(n, u)
    w.Unitize()
    h = w * (width / 2.0)
    c = [a - h, b - h, b + h, a + h]
    return [(p.X, p.Y, p.Z) for p in c], (n.X, n.Y, n.Z)


def rig_build(stamp, rig, group):
    """Make one group's emitters as surfaces on ClaudeMark::style lights and leave them SELECTED for
    run_command("_-vrayLightMesh _Enter"). A group: {"group", "colour", "luminance", "invisible",
    "strips": [{"from", "to", "width", "normal"}], "panels": [{"corners": [4 points], "normal"}]}."""
    folder, meta, d = _style_dir(stamp)
    g = [x for x in rig if x["group"] == group][0]
    li = ensure_layer(RIG_LAYER)
    at = Rhino.DocObjects.ObjectAttributes()
    at.LayerIndex = li
    at.Name = "CM rig " + group
    at.SetUserString("cm_rig", group)
    sc.doc.Objects.UnselectAll()
    ids = []
    for s in g.get("strips") or []:
        c, n = _strip_corners(s["from"], s["to"], float(s.get("width", 150)), s["normal"])
        ids.append(sc.doc.Objects.AddBrep(_rig_surface(c[0], c[1], c[2], c[3], n), at))
    for p in g.get("panels") or []:
        c = p["corners"]
        ids.append(sc.doc.Objects.AddBrep(_rig_surface(c[0], c[1], c[2], c[3], p["normal"]), at))
    for i in ids:
        sc.doc.Objects.Select(i, True)
    path = os.path.join(d, "rig_ids.json")
    known = {}
    if os.path.exists(path):
        with open(path) as fh:
            known = json.load(fh)
    known[group] = [str(i) for i in ids]
    write_json(path, known)
    sc.doc.Views.Redraw()
    return [str(i) for i in ids]


def rig_params(stamp, rig, group, scale=1.0, enabled=True):
    """After _-vrayLightMesh: set the group's mesh light (luminance units, colour, visibility, one-sided)."""
    folder, meta, d = _style_dir(stamp)
    vray = _vray()
    g = [x for x in rig if x["group"] == group][0]
    with open(os.path.join(d, "rig_ids.json")) as fh:
        ids = json.load(fh)[group]
    pl = vray.Scene.PluginFromObject(ids[0])
    if pl is None:
        raise Exception("no V-Ray light on %s: run _-vrayLightMesh _Enter with the group selected" % group)
    c = g["colour"]
    with vray.Scene.Transaction():
        for k, v in (("units", 2), ("intensity", float(g["luminance"]) * scale),
                     ("color", System.Array[System.Single]([float(c[0]), float(c[1]), float(c[2]), 1.0])),
                     ("invisible", bool(g.get("invisible", False))), ("doubleSided", False),
                     ("ignoreLightNormals", False), ("affectReflections", True), ("enabled", bool(enabled))):
            try:
                pl.Param(k).Value = v
            except Exception as ex:
                Rhino.RhinoApp.WriteLine("ClaudeMark: rig %s param %s not set (%s)" % (group, k, ex))
    vray.Scene.Refresh()
    sc.doc.Objects.UnselectAll()
    return "%s: %s, %d emitters, %s cd/m2" % (group, pl.Name, len(ids), float(g["luminance"]) * scale)


def rig_scale(stamp, rig, scales):
    """Rebalance groups between previews without rebuilding: scales = {group: multiplier} (0 = off)."""
    out = []
    for g in rig:
        s = float(scales.get(g["group"], 1.0))
        out.append(rig_params(stamp, rig, g["group"], s if s > 0 else 1.0, enabled=s > 0))
    return out


def rig_remove(stamp):
    """Delete the rig's surfaces. Their mesh-light plugins stay in V-Ray's scene (there's no delete call), so they
    are switched off first."""
    folder, meta, d = _style_dir(stamp)
    path = os.path.join(d, "rig_ids.json")
    if not os.path.exists(path):
        return "no rig"
    vray = _vray()
    with open(path) as fh:
        known = json.load(fh)
    n = 0
    for group, ids in known.items():
        pl = vray.Scene.PluginFromObject(ids[0]) if ids else None
        if pl is not None:
            with vray.Scene.Transaction():
                pl.Param("enabled").Value = False
        for i in ids:
            if sc.doc.Objects.Delete(System.Guid(i), True):
                n += 1
    vray.Scene.Refresh()
    os.remove(path)
    return "removed %d rig surfaces" % n


def rig_spots_build(stamp, rig, group):
    """Spot lights for a rig group {"group", "spots": [{"at": [x,y,z], "aim": [x,y,z], "cone_deg", "penumbra_deg"}],
    "colour", "lumens", "softness_mm"}: Rhino spotlights, left SELECTED for run_command("_-vrayLight _Convert"),
    then rig_spots_params. V-Ray ignores a plain Rhino light, and the converted light floods the Rendered VIEWPORT
    (display only) because V-Ray writes huge numbers into Rhino's intensity."""
    folder, meta, d = _style_dir(stamp)
    g = [x for x in rig if x["group"] == group][0]
    li = ensure_layer(RIG_LAYER)
    at = Rhino.DocObjects.ObjectAttributes()
    at.LayerIndex = li
    at.Name = "CM rig " + group
    at.SetUserString("cm_rig", group)
    sc.doc.Objects.UnselectAll()
    ids = []
    for s in g["spots"]:
        loc, aim = rg.Point3d(*s["at"]), rg.Point3d(*s["aim"])
        L = rg.Light()
        L.LightStyle = rg.LightStyle.WorldSpot
        L.Location = loc
        L.Direction = aim - loc
        L.SpotAngleRadians = math.radians(float(s.get("cone_deg", 24)) / 2.0)
        L.HotSpot = 0.6
        L.Diffuse = System.Drawing.Color.White
        L.Name = "CM spot " + group
        idx = sc.doc.Lights.Add(L, at)
        ids.append(sc.doc.Lights[idx].Id)
    for i in ids:
        sc.doc.Objects.Select(i, True)
    path = os.path.join(d, "rig_ids.json")
    known = {}
    if os.path.exists(path):
        with open(path) as fh:
            known = json.load(fh)
    known[group] = [str(i) for i in ids]
    write_json(path, known)
    return [str(i) for i in ids]


def rig_spots_params(stamp, rig, group, scale=1.0):
    """After _-vrayLight _Convert: each spot is its own LightSpot; set lumens, colour, cone, penumbra, softness."""
    folder, meta, d = _style_dir(stamp)
    vray = _vray()
    g = [x for x in rig if x["group"] == group][0]
    with open(os.path.join(d, "rig_ids.json")) as fh:
        ids = json.load(fh)[group]
    c = g["colour"]
    names = []
    for i, s in zip(ids, g["spots"]):
        pl = vray.Scene.PluginFromObject(i)
        if pl is None:
            raise Exception("no V-Ray light on %s: run _-vrayLight _Convert with the spots selected" % i)
        with vray.Scene.Transaction():
            for k, v in (("units", 1), ("intensity", float(g["lumens"]) * scale),
                         ("color", System.Array[System.Single]([float(c[0]), float(c[1]), float(c[2]), 1.0])),
                         ("coneAngle", math.radians(float(s.get("cone_deg", 24)))),
                         ("penumbraAngle", math.radians(float(s.get("penumbra_deg", 8)))),
                         ("shadowRadius", float(g.get("softness_mm", 150))), ("decay", 2.0), ("enabled", True)):
                try:
                    pl.Param(k).Value = v
                except Exception as ex:
                    Rhino.RhinoApp.WriteLine("ClaudeMark: spot param %s not set (%s)" % (k, ex))
        names.append(pl.Name)
    vray.Scene.Refresh()
    sc.doc.Objects.UnselectAll()
    return names


def rig_light_names(stamp):
    """Every rig group's V-Ray plugin name(s), for the fog's light list (written into the .vropt, never set live)."""
    folder, meta, d = _style_dir(stamp)
    vray = _vray()
    with open(os.path.join(d, "rig_ids.json")) as fh:
        known = json.load(fh)
    out = {}
    for group, ids in known.items():
        names = []
        for i in ids:
            pl = vray.Scene.PluginFromObject(i)
            if pl is not None and pl.Name not in names:
                names.append(pl.Name)
        out[group] = names
    return out


def rig_adopt(stamp, group, ids):
    """Register existing surfaces (a fixture's lens, an LED diffuser) as a rig group and leave them SELECTED for
    run_command("_-vrayLightMesh _Enter"); then rig_params(stamp, rig, group) as for built groups."""
    folder, meta, d = _style_dir(stamp)
    path = os.path.join(d, "rig_ids.json")
    known = {}
    if os.path.exists(path):
        with open(path) as fh:
            known = json.load(fh)
    known[group] = [str(i) for i in ids]
    write_json(path, known)
    sc.doc.Objects.UnselectAll()
    for i in ids:
        sc.doc.Objects.Select(System.Guid(str(i)), True)
    return len(ids)


def move_spot(light_id, source, direction):
    """Move a converted V-Ray spot by editing its Rhino light (V-Ray reads placement from it)."""
    lo = sc.doc.Lights.FindId(System.Guid(str(light_id)))
    lt = lo.LightGeometry
    lt.Location = rg.Point3d(*source)
    d = rg.Vector3d(*direction)
    d.Unitize()
    lt.Direction = d * 1000.0
    return sc.doc.Lights.Modify(System.Guid(str(light_id)), lt)
