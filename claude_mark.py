#! python 2
# -*- coding: utf-8 -*-
"""
ClaudeMark 4: capture the active Rhino view, mark it up in a Liquid Glass window, send it to Claude Code.

Run from the ClaudeMark alias:
    ! _-RunPythonScript "<kit folder>\\claude_mark.py"

The window is a WebView2 page (ui/index.html, ui/app.js, ui/app.css) hosted in an Eto dialog; this file is
the Rhino side: the capture, the camera and selection record, Rhino's picks under the marks, presets, the
send, and the paste into Claude Code. Send writes inbox/<stamp>/ with clean.png, marked.png, ref*.png and
meta.json. The UserPromptSubmit hook (hook/rhino_markup_inbox.py in this folder) attaches it to the next
prompt typed in Claude Code and moves it to sent/. The schema of meta.json is in README.md.

tools/cm_reply.py loads this file with CLAUDEMARK_NO_UI set and uses measure_text() and compose() to draw
Claude's crits, so both keep their signatures.

IronPython 2.7 (the "#! python 2" line): no f-strings. If WebView2 cannot start, the classic window
(classic/claude_mark.py) opens instead.
"""
import os
import re
import copy
import json
import json.encoder
import math
import time
import shutil
import base64
import datetime

# IronPython's str is already unicode, yet its json .decode('utf-8')s any string holding \x80-\xff, so a layer
# name with a middle dot in the selection or picks killed the send mid meta.json. Never take that branch.
json.encoder.HAS_UTF8 = re.compile(r"(?!)")

import clr
import System
import Rhino
import scriptcontext as sc
import Eto.Forms as forms
import Eto.Drawing as drawing

VERSION = "4.1"
HOME = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile)

def _kit_root():
    """The ClaudeMark folder: where this script is, else the usual places (Desktop\\Rhino-ClaudeMark, then the old
    ~/.claude/rhino-markup)."""
    f = globals().get("__file__")
    if f:
        d = os.path.dirname(os.path.abspath(f))
        if os.path.basename(d).lower() == "classic":
            d = os.path.dirname(d)
        if os.path.isfile(os.path.join(d, "claude_mark.py")):
            return d
    desk = System.Environment.GetFolderPath(System.Environment.SpecialFolder.DesktopDirectory)
    for d in (os.path.join(desk, "Rhino-ClaudeMark"), os.path.join(HOME, ".claude", "rhino-markup")):
        if os.path.isfile(os.path.join(d, "claude_mark.py")):
            return d
    return os.path.join(desk, "Rhino-ClaudeMark")


KIT = _kit_root()
ROOT = (globals().get("CLAUDEMARK_TEST_ROOT") or os.environ.get("CLAUDEMARK_ROOT") or KIT)
INBOX = os.path.join(ROOT, "inbox")
OUTBOX = os.path.join(ROOT, "outbox")        # Claude's replies (crits drawn on the user’s view); ClaudeReply opens them
QUEUE = os.path.join(ROOT, "queue")          # captures added to a batch; Send moves them into the inbox
SETTINGS_PATH = os.path.join(ROOT, "claudemark_settings.json")
UI_DIR = os.path.join(ROOT, "ui")
CACHE = os.path.join(ROOT, ".cache")
CLASSIC = os.path.join(KIT, "classic", "claude_mark.py")
COSMOS_HOST = "cosmos.cm"                      # and https://cosmos.cm/ to the user’s Chaos Cosmos Packages folder (previews)
HOST = "cm.app"                               # the WebView maps https://cm.app/ to ROOT

COLOURS = [("red", (255, 69, 58)), ("yellow", (255, 204, 0)), ("green", (48, 209, 88)), ("blue", (10, 132, 255)),
           ("purple", (191, 90, 242)), ("white", (255, 255, 255)), ("black", (28, 28, 30))]
WIDTHS = [("Thin", 3), ("Medium", 6), ("Thick", 11)]
TEXT_PX = {3: 22.0, 6: 30.0, 11: 42.0}   # note text height in image px, by the chosen line width
MATCH_RGB = (56, 215, 255)               # photo-match points, the same on the photo and the model
CLAUDE_RGB = (217, 119, 87)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".heif")
NUMBERED = ("box", "ellipse", "cloud", "pin")

EXAMPLES_URL = "https://www.google.com/imghp"
EXAMPLES_SPLIT = 0.58   # share of the screen ClaudeMark keeps when Chrome docks beside it

# ---- task sidebar: what kind of job this is, and the presets that say how the user likes it done
PRESETS_DIR = os.path.join(ROOT, "presets")
# Chaos Cosmos downloads: Documents, or OneDrive\Documents when Documents is redirected to OneDrive
COSMOS = next((p for p in (os.path.join(HOME, "Documents", "Chaos Cosmos", "Packages"),
                           os.path.join(HOME, "OneDrive", "Documents", "Chaos Cosmos", "Packages"))
               if os.path.isdir(p)), os.path.join(HOME, "Documents", "Chaos Cosmos", "Packages"))
CATEGORIES = [("general", "General", "sparkle"), ("model", "Model", "cube"), ("fix", "Fix", "fix"), ("materials", "Materials", "swatch"),
              ("lighting", "Lighting", "sun"), ("render", "Render", "camera"),
              ("analyse", "Analyse", "chart"), ("critique", "Critique", "crit")]
PRESET_CATS = [c for c, _, _ in CATEGORIES if c != "general"]
ALL_CATS = [c for c, _, _ in CATEGORIES]   # the user’s saved setups can be for any task, General too
TOOLKIT = os.path.join(ROOT, "tools")   # Claude's scripts for these jobs; tools/TOOLS.md says how to run them
TASK_TEXT = {
    "general": ("Mark it up and say what you want.", "Ask Claude about this view"),
    "model": ("What should Claude do here?  Refer to marks by number and references (R1).",
              "e.g.  add a window like R1 between pins 1 and 4, sill at 900"),
    "fix": ("Mark each error; a word is enough: gap, hole, missing glass.",
            "e.g.  gap here; this window is missing its glass and frame detail"),
    "materials": ("Which surfaces get what?  Mark them, or drop material photos onto them.",
                  "e.g.  timber on box 1, concrete below, glass in the openings"),
    "lighting": ("Time of day, mood, and where the light comes from.",
                 "e.g.  late afternoon from the west, warm glow from the gallery inside"),
    "render": ("Which view, and what is it for?",
               "e.g.  this view for the A3 board, eye level, people in the yard"),
    "analyse": ("A line for a section, a box for sun, a pin for what can be seen.",
                "e.g.  section along the line looking north; sun hours on box 2 in June"),
    "critique": ("What are you claiming? The critic needs something asserted to attack.",
                 "e.g.  the canopy reads as one gesture from the street"),
}
DROP_TEXT = {"model": "images, preset .json", "materials": "photos, .vrmat, preset .json",
             "lighting": "photos, .hdr or .exr, preset .json", "render": "renders you like, .vrscene, .json",
             "analyse": "a drawing style to copy, .json", "critique": "a precedent to test against, .json"}
REPLY_TEXT = ("Answer Claude: agree, push back, or mark what it missed.",
              "e.g.  C2 is wrong, the column sits behind the screen; C4 yes, fix it")
OPTIONS_TEXT = ("Pick an option: flip with the arrow keys or the letters, mark changes on it, send.",
                "e.g.  B, but drop the north end to 4 m and keep A's roof line")
STYLE_TEXT = ("Pick a look: flip with the arrow keys or the letters, mark tweaks on it, send. It gets set up for real.",
              "e.g.  A, but warmer here and with B's sky; less haze")
RENDER_NOTE = ("Set everything up over the MCP, then render it with V-Ray yourself: set /SettingsOutput "
               "img_dir + img_file, call rhVRay.Render, wait for the file, then clear the path (notes/RHINO-VRAY-NOTES.md section "
               "vray-render-over-mcp). It renders the active viewport, so restore the named view first. Save "
               "where this preset says, otherwise in a Renders folder beside the .3dm. If the user’s VFB colour "
               "filters are on, the .denoiser.png is the clean image.")
SITE = "the site (the model's Rhino sun location; ask for it if that isn't set)"

# 4.1 (5 Nov 2026): the built-in presets are gone. What each one told Claude is now an option, a setup or a
# library item in the sidebar (ui/app.js, TASKS), and the page writes the brief. Their old files in presets/ are
# skipped when presets load (builtin, and not in this list).
BUILTIN_PRESETS = []

BUILTIN_BY_SLUG = dict((p["slug"], p) for p in BUILTIN_PRESETS)

MAX_PICKS = 80          # Rhino picks per send, across all strokes
PICK_BUDGET_S = 3.0     # stop picking after this long; the camera is saved anyway
SELECTION_LIMIT = 200
PY_OWNED = ("hidden_presets", "save_dir", "window")   # settings the page never overwrites


# ---------------------------------------------------------------- theme (for the images drawn here: Claude's crits)

def rgb(hex_, a=255):
    hex_ = hex_.lstrip("#")
    return drawing.Color.FromArgb(int(hex_[0:2], 16), int(hex_[2:4], 16), int(hex_[4:6], 16), a)


ACCENT = rgb("#D97757")      # Claude's own marks
PANEL_HI = rgb("#2A2C30")
WHITE = drawing.Colors.White
FONT_UI = "Segoe UI Variable Text"


def font(size, bold=False, family=FONT_UI):
    style = drawing.FontStyle.Bold if bold else drawing.FontStyle.None
    try:
        return drawing.Font(family, size, style)
    except Exception:
        return drawing.Font(drawing.SystemFont.Bold if bold else drawing.SystemFont.Default, size)


def round_rect(x, y, w, h, r):
    return drawing.GraphicsPath.GetRoundRect(drawing.RectangleF(x, y, w, h), r)


def pen(col, w, cap=True):
    p = drawing.Pen(col, w)
    if cap:
        p.LineCap = drawing.PenLineCap.Round
        p.LineJoin = drawing.PenLineJoin.Round
    return p


def text_centered(g, f, col, cx, cy, s):
    size = g.MeasureString(f, s)
    g.DrawText(f, drawing.SolidBrush(col), cx - size.Width / 2.0, cy - size.Height / 2.0, s)


# ---------------------------------------------------------------- small helpers

def _r(v, nd=4):
    return round(float(v), nd)


def _pt(p):
    return [_r(p.X), _r(p.Y), _r(p.Z)]


def log(msg):
    """A running log in .cache/claudemark.log: start-up times, page errors, failures."""
    try:
        if not os.path.isdir(CACHE):
            os.makedirs(CACHE)
        path = os.path.join(CACHE, "claudemark.log")
        if os.path.exists(path) and os.path.getsize(path) > 512 * 1024:
            os.remove(path)
        with open(path, "a") as f:
            f.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_settings():
    s = {"tool": "box", "colour": 0, "width": 1, "jump": True, "save_dir": ""}
    try:
        with open(SETTINGS_PATH) as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f)
    except Exception:
        pass


def layer_path(doc, obj):
    try:
        return doc.Layers[obj.Attributes.LayerIndex].FullPath
    except Exception:
        return ""


def obj_record(doc, obj):
    return {"id": str(obj.Id), "type": str(obj.ObjectType),
            "name": obj.Attributes.Name or "", "layer": layer_path(doc, obj)}


def live_object_count(doc):
    """Objects really in the document. doc.Objects.Count also counts deleted table slots (it read 15,998
    for a document holding 3,200), so it is the wrong number to hand Claude."""
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.DeletedObjects = False
    es.NormalObjects = True
    es.LockedObjects = True
    es.HiddenObjects = True
    try:
        return int(doc.Objects.ObjectCount(es))
    except Exception:
        try:
            return len(list(doc.Objects.GetObjectList(es)))
        except Exception:
            return doc.Objects.Count


# ---------------------------------------------------------------- capture-time record

def camera_record(vp):
    cam = {
        "projection": "parallel" if vp.IsParallelProjection else "perspective",
        "location": _pt(vp.CameraLocation),
        "target": _pt(vp.CameraTarget),
        "direction": _pt(vp.CameraDirection),
        "up": _pt(vp.CameraUp),
        "lens_35mm": _r(vp.Camera35mmLensLength, 3),
        "two_point": bool(vp.IsTwoPointPerspectiveProjection),   # a shifted frustum V-Ray only honours on 2-pt views
    }
    res = vp.GetFrustum()
    if res[0]:
        cam["frustum"] = dict(zip(["left", "right", "bottom", "top", "near", "far"],
                                  [_r(v, 6) for v in res[1:7]]))
    xf = vp.GetTransform(Rhino.DocObjects.CoordinateSystem.World,
                         Rhino.DocObjects.CoordinateSystem.Screen)
    cam["world_to_screen"] = [[float(xf[i, j]) for j in range(4)] for i in range(4)]
    return cam


def selection_record(doc):
    sel = []
    for obj in doc.Objects.GetSelectedObjects(False, False):
        if len(sel) >= SELECTION_LIMIT:
            break
        sel.append(obj_record(doc, obj))
    # Sub-object selections (a face or an edge) don't show up in GetSelectedObjects.
    sub = []
    t0 = time.time()
    for obj in doc.Objects:
        if time.time() - t0 > 0.5:
            break
        if obj.IsSelected(True) == 3:
            rec = obj_record(doc, obj)
            rec["components"] = ["%s#%d" % (ci.ComponentIndexType, ci.Index)
                                 for ci in obj.GetSelectedSubObjects()]
            sub.append(rec)
    return sel, sub


def clipping_planes_record(doc, vp):
    out = []
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.ObjectTypeFilter = Rhino.DocObjects.ObjectType.ClipPlane
    es.HiddenObjects = True
    for obj in doc.Objects.GetObjectList(es):
        try:
            geo = obj.ClippingPlaneGeometry
            if vp.Id not in list(geo.ViewportIds()):
                continue
            pl = geo.Plane
            out.append({"id": str(obj.Id), "hidden": obj.IsHidden,
                        "origin": _pt(pl.Origin), "normal_kept_side": _pt(pl.ZAxis)})
        except Exception:
            pass
    return out


def history_tail(n=15):
    try:
        lines = [l for l in Rhino.RhinoApp.CommandHistoryWindowText.splitlines() if l.strip()]
        return lines[-n:]
    except Exception:
        return []


def gather_meta(doc, view, w, h):
    vp = view.ActiveViewport
    sel, sub = selection_record(doc)
    rect = view.ScreenRectangle
    return {
        "tool": "ClaudeMark",
        "version": 4,
        "captured": datetime.datetime.now().isoformat(),
        "captured_unix": time.time(),
        "rhino_pid": System.Diagnostics.Process.GetCurrentProcess().Id,
        "doc": {
            "path": doc.Path or "",
            "name": doc.Name or "Untitled",
            "modified": doc.Modified,
            "units": str(doc.ModelUnitSystem),
            "object_count": live_object_count(doc),
            "current_layer": doc.Layers.CurrentLayer.FullPath,
        },
        "view": {
            "name": vp.Name,
            "viewport_id": str(vp.Id),
            "display_mode": vp.DisplayMode.EnglishName,
            "size": [w, h],
            "maximized": view.Maximized,
            "screen_rect": [rect.X, rect.Y, rect.Width, rect.Height],
        },
        "image": {"clean": "clean.png", "marked": "marked.png",
                  "pixels": "image px = viewport px, origin top-left, y down"},
        "camera": camera_record(vp),
        "cplane": {"origin": _pt(vp.ConstructionPlane().Origin),
                   "x": _pt(vp.ConstructionPlane().XAxis),
                   "y": _pt(vp.ConstructionPlane().YAxis)},
        "selection": sel,
        "subobject_selection": sub,
        "clipping_planes": clipping_planes_record(doc, vp),
        "history_tail": history_tail(),
    }


# ---------------------------------------------------------------- what is under a stroke

def pick_at(doc, view, x, y, selected=()):
    """The nearest visible object under viewport pixel (x, y), or None.

    Rhino's own pick finds candidates, but it skips objects that are already selected, so
    those are passed in and ray-tested too. A ray from the near plane decides which one is
    in front. GetFrustumLine runs far -> near, so the ray goes To -> From.
    """
    vp = view.ActiveViewport
    xi, yi = int(round(x)), int(round(y))
    pc = Rhino.Input.Custom.PickContext()
    pc.View = view
    pc.PickStyle = Rhino.Input.Custom.PickStyle.PointPick
    pc.PickMode = Rhino.Input.Custom.PickMode.Shaded
    pc.SetPickTransform(vp.GetPickTransform(xi, yi))
    pc.UpdateClippingPlanes()
    refs = doc.Objects.PickObjects(pc) or []
    candidates = [(ref.Object(), ref) for ref in refs] + [(obj, None) for obj in selected]
    if not candidates:
        return None
    ok, line = vp.GetFrustumLine(float(xi), float(yi))
    if not ok:
        return None
    origin = line.To
    d = line.From - line.To
    d.Unitize()
    ray = Rhino.Geometry.Ray3d(origin, d)
    best = None
    for obj, ref in candidates:
        if obj is None:
            continue
        t = None
        meshes = obj.GetMeshes(Rhino.Geometry.MeshType.Render) or []
        for m in meshes:
            tm = Rhino.Geometry.Intersect.Intersection.MeshRay(m, ray)
            if tm >= 0 and (t is None or tm < t):
                t = tm
        if t is None:
            # Curves, points, blocks: fall back to Rhino's own selection point.
            if ref is None:
                continue
            sp = ref.SelectionPoint()
            if not sp.IsValid:
                continue
            t = (sp - origin) * d
        if best is None or t < best[0]:
            best = (t, obj)
    if best is None:
        return None
    t, obj = best
    hit = obj_record(doc, obj)
    hit["point"] = _pt(origin + d * t)
    return hit


def stroke_samples(st):
    """Pixels worth picking for a stroke: its meaningful points, plus a few along it."""
    pts = st["points"]
    kind = st["kind"]
    if kind == "pin":
        return [("pin", pts[0])]
    if kind == "ref":
        return [("ref", pts[0])]
    if kind == "match":
        return [("match", pts[0])]
    if kind == "text":
        (x, y), (tw, th) = pts[0], st.get("size") or [0, 0]
        return [("centre", [x + tw / 2.0, y + th / 2.0]), ("anchor", pts[0])]
    if kind == "arrow":
        return [("tip", pts[-1]), ("tail", pts[0])]
    if kind == "measure":
        return [("start", pts[0]), ("end", pts[-1])]
    if kind in ("box", "cloud"):
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        st["closed"] = True
        return [("centre", [(x0 + x1) / 2.0, (y0 + y1) / 2.0]), ("corner", [x0, y0]), ("corner", [x1, y0]),
                ("corner", [x1, y1]), ("corner", [x0, y1])]
    if kind == "ellipse":
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        st["closed"] = True
        return [("centre", [cx, cy]), ("edge", [cx, min(y0, y1)]), ("edge", [max(x0, x1), cy]),
                ("edge", [cx, max(y0, y1)]), ("edge", [min(x0, x1), cy])]
    out = [("start", pts[0])]
    if len(pts) > 1:
        out.append(("end", pts[-1]))
        n = min(6, len(pts) - 2)
        for k in range(1, n + 1):
            out.append(("along", pts[int(k * (len(pts) - 1) / float(n + 1))]))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        gap = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if diag > 20 and gap < 0.25 * diag:
            st["closed"] = True
            out.append(("centre", [sum(xs) / len(xs), sum(ys) / len(ys)]))
    return out


def _matrix(camera):
    M = Rhino.Geometry.Transform(1.0)
    for i in range(4):
        for j in range(4):
            M[i, j] = camera["world_to_screen"][i][j]
    return M


def unproject(camera, x, y, z):
    """World point at screen pixel (x, y) and screen depth z (0 near .. 1 far) of a saved camera."""
    ok, Mi = _matrix(camera).TryGetInverse()
    if not ok:
        return None
    v = [sum(Mi[i, j] * c for j, c in enumerate((x, y, z, 1.0))) for i in range(4)]
    return Rhino.Geometry.Point3d(v[0] / v[3], v[1] / v[3], v[2] / v[3])


def screen_depth(camera, pt):
    M = _matrix(camera)
    v = [sum(M[i, j] * c for j, c in enumerate((pt.X, pt.Y, pt.Z, 1.0))) for i in range(4)]
    return v[2] / v[3]


def pick_with_camera(doc, camera, x, y):
    """Like pick_at, but through a saved camera (its world_to_screen matrix) instead of the live view,
    so a reply opened later still picks what was under the mark when the capture was taken."""
    a, b = unproject(camera, x, y, 0.0), unproject(camera, x, y, 1.0)
    if a is None:
        return None
    d = b - a
    d.Unitize()
    loc = Rhino.Geometry.Point3d(*camera["location"])
    origin = a + d * ((loc - a) * d) if camera.get("projection") == "parallel" else loc
    ray = Rhino.Geometry.Ray3d(origin, d)
    line = Rhino.Geometry.Line(origin, origin + d * 1e7)
    best = None
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.VisibleFilter = True
    es.HiddenObjects = False
    for obj in doc.Objects.GetObjectList(es):
        ok, _ = Rhino.Geometry.Intersect.Intersection.LineBox(line, obj.Geometry.GetBoundingBox(True), 0.0)
        if not ok:
            continue
        for m in obj.GetMeshes(Rhino.Geometry.MeshType.Render) or []:
            t = Rhino.Geometry.Intersect.Intersection.MeshRay(m, ray)
            if t >= 0 and (best is None or t < best[0]):
                best = (t, obj)
    if best is None:
        return None
    hit = obj_record(doc, best[1])
    hit["point"] = _pt(ray.PointAt(best[0]))
    return hit


def camera_matches(camera, vp, tol=1e-3):
    """True when the live viewport still has the saved camera, so live picks are valid."""
    try:
        live = camera_record(vp)["world_to_screen"]
        ref = camera["world_to_screen"]
        scale = max(abs(v) for row in ref for v in row) or 1.0
        return all(abs(live[i][j] - ref[i][j]) <= tol * scale for i in range(4) for j in range(4))
    except Exception:
        return False


def pick_hints(doc, view, strokes, camera=None):
    hints = []
    t0 = time.time()
    count = 0
    selected = list(doc.Objects.GetSelectedObjects(False, False))[:SELECTION_LIMIT]
    for i, st in enumerate(strokes):
        for role, p in stroke_samples(st):
            if count >= MAX_PICKS or time.time() - t0 > PICK_BUDGET_S:
                return hints
            count += 1
            try:
                hit = pick_with_camera(doc, camera, p[0], p[1]) if camera else pick_at(doc, view, p[0], p[1], selected)
            except Exception as ex:
                hit = {"error": str(ex)}
            hints.append({"stroke": i, "kind": st["kind"], "label": st.get("label"),
                          "role": role, "px": [_r(p[0], 1), _r(p[1], 1)], "hit": hit})
    return hints


UNIT_ABBR = {"Millimeters": "mm", "Centimeters": "cm", "Meters": "m", "Kilometers": "km", "Inches": "in",
             "Feet": "ft", "Yards": "yd", "Miles": "mi", "Microns": "um"}


def fmt_length(d, units):
    """A distance in model units as a person would say it: 4.21 m, 860 mm, 12' 6\"."""
    u = str(units)
    if u == "Millimeters":
        return "%.2f m" % (d / 1000.0) if d >= 1000 else "%d mm" % round(d)
    if u == "Centimeters":
        return "%.2f m" % (d / 100.0) if d >= 100 else "%.1f cm" % d
    if u == "Meters":
        return "%.2f m" % d if d >= 1 else "%d mm" % round(d * 1000)
    if u == "Feet":
        ft = int(d)
        inch = (d - ft) * 12.0
        return "%d' %.1f\"" % (ft, inch)
    if u == "Inches":
        return "%d' %.1f\"" % (int(d // 12), d % 12) if d >= 12 else "%.2f in" % d
    return "%.3g %s" % (d, UNIT_ABBR.get(u, u.lower()))


# ---------------------------------------------------------------- reference images

def load_reference(source, work, n):
    """Copy a dropped or pasted image into the capture folder as refN.png. Returns (path, bitmap)."""
    return load_image(source, os.path.join(work, "ref%d.png" % n))


def http_get(url, timeout=12):
    """Download a URL the way a browser would. Returns (bytes, content type).
    Raises with a reason the user can read in a toast, never .NET's own wording."""
    clr.AddReference("System.Net.Http")
    from System.Net.Http import HttpClient
    client = HttpClient()
    client.Timeout = System.TimeSpan.FromSeconds(timeout)
    # Image hosts often refuse requests that don't look like the browser the image came from.
    client.DefaultRequestHeaders.TryAddWithoutValidation(
        "User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/130.0.0.0 Safari/537.36")
    # Formats Windows always decodes come first: a CDN that negotiates would otherwise answer with AVIF.
    client.DefaultRequestHeaders.TryAddWithoutValidation(
        "Accept", "image/png,image/jpeg,image/gif;q=0.9,image/webp;q=0.8,image/*;q=0.7,*/*;q=0.5")
    try:
        r = client.GetAsync(url).Result
    except Exception as ex:
        text = str(ex).lower()
        raise Exception("the site didn't answer in time" if ("cancel" in text or "time" in text) else "couldn't reach the site")
    code = int(r.StatusCode)
    if code >= 400:
        raise Exception(("the site refused the download (%d)" if code < 500 else "the site had an error (%d)") % code)
    ctype = ""
    try:
        ctype = str(r.Content.Headers.ContentType.MediaType or "")
    except Exception:
        pass
    return r.Content.ReadAsByteArrayAsync().Result, ctype


def http_bytes(url, timeout=12):
    return http_get(url, timeout)[0]


def reason(err):
    """A short cause for a failed image, for a toast."""
    s = str(err or "").replace("One or more errors occurred.", "").strip()
    if s.startswith("(") and s.endswith(")"):   # .NET wraps the inner message in brackets
        s = s[1:-1].strip()
    return (s or "unknown error")[:120]


def load_image(source, out):
    """Decode an image source and save it as a PNG at `out` (at most 2000 px). Returns (out, bitmap).
    `source` is a local path, an http(s) URL, a data: URI, raw bytes (System.Byte[]) or an Eto image.
    A URL that turns out to be a web page raises, so the caller can try the next one."""
    if isinstance(source, basestring) and source.lower().startswith("data:image"):
        img = drawing.Bitmap(System.IO.MemoryStream(System.Convert.FromBase64String(source.split(",", 1)[1])))
    elif isinstance(source, basestring) and source.lower().startswith(("http://", "https://")):
        data, ctype = http_get(source)
        try:
            img = drawing.Bitmap(System.IO.MemoryStream(data))
        except Exception:
            if "html" in ctype or ctype.startswith("text"):
                raise Exception("that link is a web page, not an image")
            raise Exception("Windows can't open that image type (%s)" % (ctype or "unknown"))
    elif isinstance(source, basestring):
        if not os.path.isfile(source):
            raise Exception("the file isn't there any more")
        try:
            img = drawing.Bitmap(source)
        except Exception:
            ext = os.path.splitext(source)[1].lower()
            if ext in (".heic", ".heif", ".avif", ".webp"):
                raise Exception("Windows can't open %s files here; save it as JPG or PNG first" % ext)
            raise Exception("that file is damaged or isn't really an image")
    elif isinstance(source, System.Array):
        img = drawing.Bitmap(System.IO.MemoryStream(source))
    else:
        img = source
    w, h = img.Size.Width, img.Size.Height
    k = min(1.0, 2000.0 / max(w, h))
    bmp = drawing.Bitmap(max(1, int(w * k)), max(1, int(h * k)), drawing.PixelFormat.Format32bppRgba)
    g = drawing.Graphics(bmp)
    try:
        g.ImageInterpolation = drawing.ImageInterpolation.High
        g.DrawImage(img, 0.0, 0.0, float(bmp.Size.Width), float(bmp.Size.Height))
    finally:
        g.Dispose()
    bmp.Save(out, drawing.ImageFormat.Png)
    return out, bmp


def _imgurl(url):
    """Google Images result links carry the real image as ?imgurl=...; return it, or None."""
    import urllib
    if "imgurl=" not in url:
        return None
    return urllib.unquote(url.split("imgurl=", 1)[1].split("&", 1)[0])


def expand_alternatives(alts):
    """Browser drag sources, best first, with the real image inside a Google result link added."""
    out = []
    for a in alts or []:
        if not isinstance(a, basestring) or not a:
            continue
        real = _imgurl(a) if a.startswith("http") else None
        for x in ([real] if real else []) + [a]:
            if x not in out:
                out.append(x)
    return out


def clipboard_sources():
    """What the clipboard holds that could be an image: (files, alternatives)."""
    files, alts = [], []
    cb = forms.Clipboard()
    try:
        if cb.ContainsImage and cb.Image is not None:
            alts.append(cb.Image)
    except Exception:
        pass
    try:
        if cb.ContainsUris:
            for u in cb.Uris:
                if u.IsFile and u.LocalPath.lower().endswith(IMAGE_EXTS):
                    files.append(u.LocalPath)
                elif u.Scheme in ("http", "https"):
                    alts.append(u.AbsoluteUri)
    except Exception:
        pass
    try:
        html = cb.Html if cb.ContainsHtml else ""
        for src in re.findall(r'<img[^>]+src="((?:https?://|data:image)[^"]+)"', html or ""):
            alts.append(src.replace("&amp;", "&"))
    except Exception:
        pass
    try:
        text = (cb.Text or "").strip() if cb.ContainsText else ""
        if text.lower().startswith(("http://", "https://")):
            alts.append(text.split()[0])
    except Exception:
        pass
    return files, alts


# ---------------------------------------------------------------- docking Chrome beside the window

def _win32():
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    u = ctypes.windll.user32
    u.FindWindowExW.restype = ctypes.c_void_p
    u.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    return ctypes, u, RECT


def find_chrome():
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        p = os.path.join(os.environ.get(key, ""), "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(p):
            return p
    return None


def windows_of_class(cls, process=None):
    """Visible, titled top-level windows of a class, topmost first: [(hwnd, title)]."""
    ctypes, u, RECT = _win32()
    from ctypes import wintypes
    out = []
    h = u.FindWindowExW(None, None, cls, None)
    while h:
        hv = ctypes.c_void_p(h)
        n = u.GetWindowTextLengthW(hv)
        if u.IsWindowVisible(hv) and n > 0:
            ok = True
            if process:
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(hv, ctypes.byref(pid))
                try:
                    ok = System.Diagnostics.Process.GetProcessById(pid.value).ProcessName.lower() == process
                except Exception:
                    ok = False
            if ok:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hv, buf, n + 1)
                out.append((h, buf.value))
        h = u.FindWindowExW(None, hv, cls, None)
    return out


def chrome_windows():
    """Visible, titled top-level windows belonging to chrome.exe (Spotify and Edge share the class)."""
    return [h for h, _ in windows_of_class(u"Chrome_WidgetWin_1", "chrome")]


def _physical_pixels(u):
    """Switch this thread to per-monitor v2 DPI awareness, so every rectangle is in physical pixels.

    DWM's frame bounds are always physical, but GetWindowRect and SetWindowPos follow the thread's
    DPI mode, and Rhino's UI thread is not per-monitor aware: on the user’s 150 % screen the two disagreed
    by half, and Find examples threw both windows off screen. Returns the context to restore."""
    import ctypes
    try:
        f = u.SetThreadDpiAwarenessContext
        f.restype = ctypes.c_void_p
        f.argtypes = [ctypes.c_void_p]
        return f(ctypes.c_void_p(-4))   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except Exception:
        return None


def _restore_pixels(u, old):
    import ctypes
    if old:
        try:
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
        except Exception:
            pass


def screen_of(hwnd, physical):
    """(work area, monitor) of the screen a window is on, each (x, y, w, h): in physical pixels, or in
    this thread's own pixels, which Windows scales on a screen whose DPI differs from the system's."""
    ctypes, u, RECT = _win32()

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]
    old = _physical_pixels(u) if physical else None
    try:
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        u.GetMonitorInfoW(ctypes.c_void_p(u.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)), ctypes.byref(mi))
        return tuple((r.left, r.top, r.right - r.left, r.bottom - r.top) for r in (mi.rcWork, mi.rcMonitor))
    finally:
        _restore_pixels(u, old)


def work_area(hwnd):
    """The work area (screen minus taskbar) of the monitor a window is on, in physical pixels."""
    return screen_of(hwnd, True)[0]


def window_insets(hwnd):
    """The invisible resize border Windows 10/11 pads a window with, (left, top, right, bottom), in
    physical pixels: DWM's frame bounds (the visible edge) are always physical, so both are read that way."""
    ctypes, u, RECT = _win32()
    old = _physical_pixels(u)
    try:
        hv = ctypes.c_void_p(hwnd)
        outer, frame = RECT(), RECT()
        u.GetWindowRect(hv, ctypes.byref(outer))
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hv, 9, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:
            return (0, 0, 0, 0)
        ins = (frame.left - outer.left, frame.top - outer.top, outer.right - frame.right, outer.bottom - frame.bottom)
        return ins if all(0 <= v <= 40 for v in ins) else (0, 0, 0, 0)
    finally:
        _restore_pixels(u, old)


def place_window(hwnd, x, y, w, h, own=False):
    """Move a top-level window so its VISIBLE frame fills (x, y, w, h).

    own=False: another program's window (Chrome is per-monitor DPI aware): physical pixels.
    own=True: a window of Rhino's UI thread, which is only system DPI aware: this thread's own pixels.
    Each is placed in its own coordinates. Placing the ClaudeMark window in physical pixels left it
    6 px off screen and 71 px too tall on the user’s 150 % screen, its note box under the taskbar.
    """
    ctypes, u, RECT = _win32()
    hv = ctypes.c_void_p(hwnd)
    u.ShowWindow(hv, 9)  # SW_RESTORE: a maximised window ignores SetWindowPos
    l, t, r, b = window_insets(hwnd)
    if own:
        mon_p, mon_o = screen_of(hwnd, True)[1], screen_of(hwnd, False)[1]
        k = float(mon_p[2]) / mon_o[2] if mon_o[2] else 1.0   # physical pixels per own pixel
        l, t, r, b = l / k, t / k, r / k, b / k
    old = None if own else _physical_pixels(u)
    try:
        u.SetWindowPos(hv, None, int(round(x - l)), int(round(y - t)), int(round(w + l + r)), int(round(h + t + b)),
                       0x0004 | 0x0040)
    finally:
        _restore_pixels(u, old)


def window_alive(hwnd):
    if not hwnd:
        return False
    ctypes, u, RECT = _win32()
    return bool(u.IsWindow(ctypes.c_void_p(hwnd))) and bool(u.IsWindowVisible(ctypes.c_void_p(hwnd)))


# ---------------------------------------------------------------- Claude Code windows (where the paste goes)

SPINNER = u"\u2733\u273b\u2736\u273d\u2722\u00b7*\u25d0\u25d1\u25d2\u25d3\u2802\u2810\u2808\u2801\u2804\u2820\u2880\u2840\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f "


def terminal_windows():
    """Windows Terminal and console windows, topmost first. One WindowsTerminal process hosts every
    window, so its MainWindowHandle is an arbitrary one of them: list them all, by title."""
    out = []
    for cls, kind in ((u"CASCADIA_HOSTING_WINDOW_CLASS", "Windows Terminal"), (u"ConsoleWindowClass", "Console")):
        try:
            for h, title in windows_of_class(cls):
                clean = title.lstrip(SPINNER).strip() or title
                claude = title[:1] in SPINNER.strip() or "claude" in title.lower()
                out.append({"hwnd": int(h), "title": clean, "raw": title, "claude": bool(claude), "kind": kind})
        except Exception:
            pass
    return out


def choose_terminal(terms, settings):
    """The window the paste goes to: the one the user chose last time (by title), else the topmost Claude Code
    window, else the topmost terminal."""
    want = (settings or {}).get("target_title")
    for t in terms:
        if want and t["title"] == want:
            return t["hwnd"]
    for t in terms:
        if t["claude"]:
            return t["hwnd"]
    return terms[0]["hwnd"] if terms else None


# ---------------------------------------------------------------- drawing (Claude's crits, and the fallback burn-in)

HALO = drawing.Color.FromArgb(0, 0, 0, 76)


def _colour(st):
    r, g, b = st["rgb"]
    return drawing.Color.FromArgb(r, g, b)


def _arrow_shape(a, b, w, s):
    dx, dy = b.X - a.X, b.Y - a.Y
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    head = min(max(4.2 * w, 15.0 * s), 0.6 * length)
    bx, by = b.X - ux * head, b.Y - uy * head
    half = 0.56 * head
    tri = [b, drawing.PointF(bx - uy * half, by + ux * half), drawing.PointF(bx + uy * half, by - ux * half)]
    return drawing.PointF(bx, by), System.Array[drawing.PointF](tri)


def text_layout(g, st, s):
    """Font, lines, line height, text width and height, and padding of a note at scale s."""
    px = st.get("font_px", 30.0) * s
    f = font(max(4.0, px * 0.75), True)   # Eto sizes fonts in points
    lines = (st.get("text") or "").split("\n")
    sizes = [g.MeasureString(f, ln or " ") for ln in lines]
    lh = max(sz.Height for sz in sizes)
    return f, lines, lh, max(sz.Width for sz in sizes), lh * len(lines), 0.42 * px, 0.2 * px


_MEASURE = []


def measure_text(st):
    """Store the note's box size in image px (st["size"]), for hit tests and picks."""
    if not _MEASURE:
        _MEASURE.append(drawing.Bitmap(4, 4, drawing.PixelFormat.Format32bppRgba))
    g = drawing.Graphics(_MEASURE[0])
    try:
        _, _, _, tw, th, padx, pady = text_layout(g, st, 1.0)
    finally:
        g.Dispose()
    st["size"] = [round(tw + 2 * padx, 1), round(th + 2 * pady, 1)]


def draw_tag(g, x, y, text, px, s, ink=WHITE, bg=None, edge=None, centre=False):
    f = font(max(4.0, px * s * 0.75), True)
    ts = g.MeasureString(f, text)
    padx, pady = 0.55 * px * s, 0.3 * px * s
    w, h = ts.Width + 2 * padx, ts.Height + 2 * pady
    if centre:
        x, y = x - w / 2.0, y - h / 2.0
    r = min(h / 2.0, 9.0 * s)
    g.FillPath(drawing.Color.FromArgb(0, 0, 0, 70), round_rect(x + 1.5 * s, y + 2.5 * s, w, h, r))
    g.FillPath(bg or drawing.Color.FromArgb(18, 18, 22, 214), round_rect(x, y, w, h, r))
    if edge is not None:
        g.DrawPath(pen(edge, max(1.0, 1.5 * s)), round_rect(x, y, w, h, r))
    g.DrawText(f, drawing.SolidBrush(ink), x + padx, y + pady, text)
    return w, h


def draw_match(g, x, y, label, s):
    """A photo-match point: a ring with a gapped crosshair, so the exact point stays visible."""
    col = drawing.Color.FromArgb(*MATCH_RGB)
    rad = max(6.0, 11.0 * s)
    for c, wdt in ((drawing.Color.FromArgb(0, 0, 0, 150), 3.5), (col, 1.6)):
        g.DrawEllipse(pen(c, wdt), x - rad, y - rad, 2 * rad, 2 * rad)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            g.DrawLine(pen(c, wdt), x + dx * rad * 0.35, y + dy * rad * 0.35, x + dx * rad * 1.5, y + dy * rad * 1.5)
    draw_tag(g, x + rad * 1.1, y - rad * 1.1 - 22 * s, str(label), 13.0, s, ink=col,
             bg=drawing.Color.FromArgb(8, 40, 52, 230))   # Eto's FromArgb is (r, g, b, alpha)


def _ink_on(c):
    return drawing.Colors.Black if (0.299 * c.Rb + 0.587 * c.Gb + 0.114 * c.Bb) / 255.0 > 0.62 else WHITE


def _badge(g, st, col, s, ox, oy):
    """The number on a box, ellipse or cloud, on its top-left corner, and its comment beside it."""
    (x0, y0), (x1, y1) = st["points"][0], st["points"][-1]
    x, y, w, h = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
    if st["kind"] == "ellipse":
        cx, cy = x + w / 2.0 - (w / 2.0) * 0.7071, y + h / 2.0 - (h / 2.0) * 0.7071
    else:
        cx, cy = x, y
    cx, cy = ox + cx * s, oy + cy * s
    r = (11 + st.get("width", 6) * 0.35) * s
    g.FillEllipse(drawing.Color.FromArgb(0, 0, 0, 80), cx - r + 1.0 * s, cy - r + 1.6 * s, 2 * r, 2 * r)
    g.FillEllipse(col, cx - r, cy - r, 2 * r, 2 * r)
    g.DrawEllipse(pen(WHITE, max(1.0, 1.8 * s)), cx - r, cy - r, 2 * r, 2 * r)
    text_centered(g, font(max(4.0, r * 0.8), True), _ink_on(col), cx, cy, str(st.get("label", "")))
    if st.get("comment"):
        draw_tag(g, cx + r + 6 * s, cy - 13 * s, st["comment"][:90], 15.0, s)


def _cloud_path(x, y, w, h, s):
    path = drawing.GraphicsPath()
    r = min(max(min(w, h) / 5.0, 6 * s), 24 * s)
    per = 2 * (w + h)
    n = max(8, int(round(per / (r * 1.9))))

    def perim(d):
        d = d % per
        if d < w:
            return (x + d, y)
        d -= w
        if d < h:
            return (x + w, y + d)
        d -= h
        if d < w:
            return (x + w - d, y + h)
        d -= w
        return (x, y + h - d)
    pts = [perim(per * i / float(n)) for i in range(n)]
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        d = math.hypot(dx, dy) or 1.0
        nx, ny = dy / d, -dx / d
        c = ((p[0] + q[0]) / 2.0 + nx * d * 0.62, (p[1] + q[1]) / 2.0 + ny * d * 0.62)
        c1 = drawing.PointF(p[0] + 2.0 / 3.0 * (c[0] - p[0]), p[1] + 2.0 / 3.0 * (c[1] - p[1]))
        c2 = drawing.PointF(q[0] + 2.0 / 3.0 * (c[0] - q[0]), q[1] + 2.0 / 3.0 * (c[1] - q[1]))
        path.AddBezier(drawing.PointF(p[0], p[1]), c1, c2, drawing.PointF(q[0], q[1]))
    path.CloseFigure()
    return path


def draw_stroke(g, st, s, ox, oy, bitmaps=None):
    """Draw one mark stored in image px, scaled by s and offset by (ox, oy)."""
    if st.get("by") == "claude":
        st = dict(st, rgb=[ACCENT.Rb, ACCENT.Gb, ACCENT.Bb])   # Claude's own marks, in Claude orange
    col = _colour(st) if "rgb" in st else ACCENT
    w = max(1.0, st.get("width", 6) * s)
    halo_w = w + max(2.0, 3.0 * s)
    pts = [drawing.PointF(ox + p[0] * s, oy + p[1] * s) for p in st["points"]]
    kind = st["kind"]
    if kind in ("pen", "highlight"):
        c = drawing.Color.FromArgb(col.Rb, col.Gb, col.Bb, 97) if kind == "highlight" else col
        if len(pts) == 1:
            if kind == "pen":
                g.FillEllipse(HALO, pts[0].X - halo_w / 2, pts[0].Y - halo_w / 2, halo_w, halo_w)
            g.FillEllipse(c, pts[0].X - w / 2, pts[0].Y - w / 2, w, w)
            return
        arr = System.Array[drawing.PointF](pts)
        if kind == "pen":
            g.DrawLines(pen(HALO, halo_w), arr)
        g.DrawLines(pen(c, w), arr)
    elif kind in ("box", "ellipse", "cloud"):
        a, b = pts[0], pts[-1]
        x, y = min(a.X, b.X), min(a.Y, b.Y)
        bw, bh = abs(b.X - a.X), abs(b.Y - a.Y)
        if kind == "box":
            g.DrawRectangle(pen(HALO, halo_w), x, y, bw, bh)
            g.DrawRectangle(pen(col, w), x, y, bw, bh)
        elif kind == "ellipse":
            g.DrawEllipse(pen(HALO, halo_w), x, y, bw, bh)
            g.DrawEllipse(pen(col, w), x, y, bw, bh)
        else:
            g.DrawPath(pen(HALO, halo_w), _cloud_path(x, y, bw, bh, s))
            g.DrawPath(pen(col, w), _cloud_path(x, y, bw, bh, s))
        if st.get("label") is not None:
            _badge(g, st, col, s, ox, oy)
    elif kind in ("arrow", "measure"):
        a, b = pts[0], pts[-1]
        if math.hypot(b.X - a.X, b.Y - a.Y) < 1:
            return
        if kind == "arrow":
            base, tri = _arrow_shape(a, b, w, s)
            g.DrawLine(pen(HALO, halo_w), a, base)
            g.DrawPolygon(pen(HALO, max(2.0, 3.0 * s)), tri)
            g.DrawLine(pen(col, w), a, base)
            g.FillPolygon(col, tri)
            if st.get("comment"):
                draw_tag(g, a.X + 8 * s, a.Y - 13 * s, st["comment"][:90], 15.0, s)
        else:
            lw = max(1.5, w * 0.6)
            g.DrawLine(pen(HALO, lw + 3 * s), a, b)
            g.DrawLine(pen(col, lw), a, b)
            L = math.hypot(b.X - a.X, b.Y - a.Y)
            nx, ny = -(b.Y - a.Y) / L * 9 * s, (b.X - a.X) / L * 9 * s
            for p in (a, b):
                g.DrawLine(pen(col, lw), p.X - nx, p.Y - ny, p.X + nx, p.Y + ny)
            text = (st.get("measure") or {}).get("text")
            if text:
                draw_tag(g, (a.X + b.X) / 2.0, (a.Y + b.Y) / 2.0, text, 15.0, s, edge=col, centre=True)
    elif kind == "pin":
        c = pts[0]
        rad = (12.0 + st.get("width", 6)) * s
        g.FillEllipse(drawing.Color.FromArgb(0, 0, 0, 70), c.X - rad + 1.5 * s, c.Y - rad + 2.5 * s, 2 * rad, 2 * rad)
        g.FillEllipse(col, c.X - rad, c.Y - rad, 2 * rad, 2 * rad)
        g.DrawEllipse(pen(WHITE, max(1.0, 2.2 * s)), c.X - rad, c.Y - rad, 2 * rad, 2 * rad)
        label = str(st.get("label", ""))
        text_centered(g, font(max(5.0, rad * (0.62 if len(label) > 2 else 0.76)), True), _ink_on(col), c.X, c.Y, label)
        if st.get("comment"):
            draw_tag(g, c.X + rad + 6 * s, c.Y - 13 * s, st["comment"][:90], 15.0, s)
    elif kind == "ref":
        c = pts[0]
        rw, rh = st["size"][0] * s, st["size"][1] * s
        x, y = c.X - rw / 2.0, c.Y - rh / 2.0
        pad = max(2.0, 5.0 * s)
        g.FillRectangle(drawing.Color.FromArgb(0, 0, 0, 90), x - pad + 3 * s, y - pad + 5 * s, rw + 2 * pad, rh + 2 * pad)
        g.FillRectangle(WHITE, x - pad, y - pad, rw + 2 * pad, rh + 2 * pad)
        bmp = (bitmaps or {}).get(st["label"])
        if bmp is not None:
            g.ImageInterpolation = drawing.ImageInterpolation.High
            g.DrawImage(bmp, x, y, rw, rh)
        else:
            g.FillRectangle(PANEL_HI, x, y, rw, rh)
        chip = st["label"] + ("   %d match points" % len(st["matches"]) if st.get("matches") else "")
        draw_tag(g, x - pad, y - pad - 30 * s, chip, 14.0, s)
    elif kind == "match":
        draw_match(g, pts[0].X, pts[0].Y, st.get("label", ""), s)
    elif kind == "text":
        # The user’s colour on a dark tag, so a note reads over any part of the capture.
        x, y = pts[0].X, pts[0].Y
        f, lines, lh, tw, th, padx, pady = text_layout(g, st, s)
        bw, bh = tw + 2 * padx, th + 2 * pady
        rad = min(bh / 2.0, max(3.0, 0.3 * st.get("font_px", 30.0) * s))
        dark = (0.299 * col.Rb + 0.587 * col.Gb + 0.114 * col.Bb) / 255.0 < 0.2
        g.FillPath(drawing.Color.FromArgb(0, 0, 0, 70), round_rect(x + 1.5 * s, y + 2.5 * s, bw, bh, rad))
        g.FillPath(drawing.Color.FromArgb(255, 255, 255, 230) if dark else drawing.Color.FromArgb(18, 18, 22, 210),
                   round_rect(x, y, bw, bh, rad))
        g.DrawPath(pen(drawing.Color.FromArgb(col.Rb, col.Gb, col.Bb, 150), max(1.0, 1.5 * s)), round_rect(x, y, bw, bh, rad))
        for i, ln in enumerate(lines):
            g.DrawText(f, drawing.SolidBrush(col), x + padx, y + pady + i * lh, ln)


def compose(clean_path, strokes, out_path, w, h, bitmaps=None):
    """Burn the marks into a copy of the capture at full resolution."""
    src = drawing.Bitmap(clean_path)
    out = drawing.Bitmap(w, h, drawing.PixelFormat.Format32bppRgb)
    g = drawing.Graphics(out)
    try:
        g.ImageInterpolation = drawing.ImageInterpolation.None
        g.DrawImage(src, 0.0, 0.0, float(w), float(h))
        g.AntiAlias = True
        for st in strokes:
            draw_stroke(g, st, 1.0, 0.0, 0.0, bitmaps)
    finally:
        g.Dispose()
    out.Save(out_path, drawing.ImageFormat.Png)
    return out


# ---------------------------------------------------------------- presets

def clean_name(stem):
    """'fd845e0_3dh_Larch_Smoked_Matte_002_15_94cm' -> 'Larch Smoked Matte 002'."""
    s = stem.split("_3dh_")[-1]
    s = re.sub(r"_\d+k_lin_srgb$|_lin_srgb$", "", s)
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"(\s\d+(\s\d+)?\s?cm|\s\d+(\.\d+)?cm)$", "", s)
    return " ".join(s.split())[:40] or "Preset"


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "preset")[:48]


def _cosmos_package(rel):
    """Resolve 'Materials/Wood/Oak_..' to its Cosmos package: (files, preview png) or ([], None)."""
    import glob
    hits = glob.glob(os.path.join(COSMOS, rel.replace("/", os.sep) + "_*"))
    if not hits:
        return [], None
    pkg = hits[0]
    files = sorted(glob.glob(os.path.join(pkg, "*.vrmat")) + glob.glob(os.path.join(pkg, "*.exr")) +
                   glob.glob(os.path.join(pkg, "*.hdr")))
    previews = sorted(glob.glob(os.path.join(pkg, "Previews", "*.png")) + glob.glob(os.path.join(pkg, "Previews", "*.jpg")))
    return files, (previews[0] if previews else None)


def install_presets(settings):
    """Write the built-in presets that aren't on disk yet. The user’s edits to existing files are kept."""
    hidden = set(settings.get("hidden_presets", []))
    for p in BUILTIN_PRESETS:
        folder = os.path.join(PRESETS_DIR, p["category"])
        path = os.path.join(folder, p["slug"] + ".json")
        pid = p["category"] + "/" + p["slug"] + ".json"
        if os.path.exists(path) or pid in hidden:
            continue
        if not os.path.isdir(folder):
            os.makedirs(folder)
        rec = dict((k, v) for k, v in p.items() if k not in ("slug", "cosmos", "cosmos_thumb"))
        rec["builtin"] = True
        rec["order"] = BUILTIN_PRESETS.index(p)
        with open(path, "w") as f:
            json.dump(rec, f, indent=1)


def resolve_cosmos(rec, slug):
    """Built-in presets name a Cosmos package; find its files and preview now, not only at install, so a
    package downloaded after the first run is picked up (they were frozen empty before)."""
    b = BUILTIN_BY_SLUG.get(slug)
    if not b or not rec.get("builtin"):
        return
    if "cosmos" in b and not rec.get("files"):
        files, preview = _cosmos_package(b["cosmos"])
        if files:
            rec["files"] = files
        if preview and not rec.get("thumb"):
            rec["thumb"] = preview
    if "cosmos_thumb" in b and not rec.get("thumb"):
        preview = _cosmos_package(b["cosmos_thumb"])[1]
        if preview:
            rec["thumb"] = preview


def load_presets(settings):
    """All presets, by category: built-ins in their order, then the user’s own oldest first."""
    hidden = set(settings.get("hidden_presets", []))
    out = dict((c, []) for c in ALL_CATS)
    for cat in ALL_CATS:
        folder = os.path.join(PRESETS_DIR, cat)
        if not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            if not fn.lower().endswith(".json"):
                continue
            pid = cat + "/" + fn
            if pid in hidden:
                continue
            try:
                with open(os.path.join(folder, fn)) as f:
                    rec = json.load(f)
            except Exception:
                continue
            if rec.get("builtin") and fn[:-5] not in BUILTIN_BY_SLUG:
                continue   # a built-in from before 4.1: its job is an option in the sidebar now
            rec["id"] = pid
            rec["category"] = cat
            rec["_mtime"] = os.path.getmtime(os.path.join(folder, fn))
            resolve_cosmos(rec, fn[:-5])
            out[cat].append(rec)
        out[cat].sort(key=lambda r: (0, r.get("order", 0)) if r.get("builtin") else (1, r["_mtime"]))
    return out


def preset_from_source(source, category, library=False):
    """Turn a dropped file or browser image into a saved preset. Returns (preset id, category)."""
    is_path = isinstance(source, basestring) and not source.lower().startswith(("http://", "https://", "data:"))
    ext = os.path.splitext(source)[1].lower() if is_path else ""
    if category == "general":
        category = "materials"
    if ext == ".json":
        with open(source) as f:
            rec = json.load(f)
        category = rec.get("category", category)
        rec["builtin"] = False
    elif ext == ".vrmat":
        category = "materials"
        name = clean_name(os.path.splitext(os.path.basename(source))[0])
        rec = {"name": name, "summary": "Your V-Ray material (.vrmat), on the marked surfaces",
               "instructions": "Import this V-Ray material with -_vrayImportMaterial and assign it with "
                               "obj.RenderMaterial to the marked or selected objects.",
               "files": [source], "icon": "swatch"}
    elif ext in (".hdr", ".exr"):
        category = "lighting"
        stem = os.path.splitext(os.path.basename(source))[0]
        name = clean_name(stem.split("_", 1)[-1] if "_" in stem and len(stem.split("_", 1)[0]) == 8 else stem)
        rec = {"name": name, "summary": "Your HDRI, as a V-Ray dome light",
               "instructions": "Light the scene with this HDRI on a V-Ray dome light; rotate it so the sun suits "
                               "the marked view and expose for the building.",
               "files": [source], "icon": "sun"}
    elif ext in (".vrscene", ".vropt"):
        category = "render"
        name = clean_name(os.path.splitext(os.path.basename(source))[0])
        rec = {"name": name, "summary": "Your V-Ray render settings",
               "instructions": "Load the render settings from this file. " + RENDER_NOTE,
               "files": [source], "icon": "camera"}
    else:
        if category == "model":
            category = "materials"
        if is_path:
            name = clean_name(os.path.splitext(os.path.basename(source))[0])
        elif isinstance(source, basestring) and source.startswith("http"):
            host = source.split("/")[2].lower()
            for pre in ("www.", "images.", "img.", "cdn."):
                host = host[len(pre):] if host.startswith(pre) else host
            name = "Web image, " + host
        else:
            name = "Pasted image"
        name = name[:40]
        what = {"materials": "Make a V-Ray material that matches this photo (colour, pattern scale, roughness) "
                             "and apply it to the marked or selected objects.",
                "lighting": "Light the scene to match the mood of this image: time of day, sun direction, colour "
                            "temperature and contrast.",
                "render": "Match the look of this image: framing, lens, exposure and post look. " + RENDER_NOTE,
                "analyse": "Draw the analysis in the graphic style of this image: line weights, tones, colour "
                           "and how it is labelled.",
                "critique": "Use this image as the precedent to test the marked design against: where the design "
                            "does what the precedent does, and where it doesn't."}
        summary = {"materials": "Match this photo's material", "lighting": "Match this image's light",
                   "render": "Match this image's look", "analyse": "Draw it like this image",
                   "critique": "Test it against this precedent"}
        rec = {"name": name, "summary": summary.get(category, summary["render"]),
               "instructions": what.get(category, what["render"])}
        if isinstance(source, basestring) and source.startswith("http"):
            rec["source"] = source[:500]
        folder = os.path.join(PRESETS_DIR, category, "files")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        img_path = os.path.join(folder, "%s-%d.png" % (_slug(name), int(time.time())))
        load_image(source, img_path)
        rec["files"] = [img_path]
        rec["thumb"] = img_path
    if ext in (".vrmat", ".hdr", ".exr"):
        import glob
        previews = sorted(glob.glob(os.path.join(os.path.dirname(source), "Previews", "*.png")))
        if previews:
            rec["thumb"] = previews[0]
    rec["category"] = category
    rec["builtin"] = False
    if library or ext in (".vrmat", ".hdr", ".exr"):
        rec["kind"] = "library"
    folder = os.path.join(PRESETS_DIR, category)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    fn = "%s.json" % _slug(rec.get("name", "preset"))
    while os.path.exists(os.path.join(folder, fn)):
        fn = "%s-%d.json" % (_slug(rec.get("name", "preset")), int(time.time() * 1000) % 100000)
    with open(os.path.join(folder, fn), "w") as f:
        json.dump(rec, f, indent=1)
    return category + "/" + fn, category


def thumb_url(path, size=96):
    """A 96 px copy of a preview image in .cache/thumbs, as a URL the page can load (Cosmos previews are
    1-2 MB each and may live outside the kit folder)."""
    if not path or not os.path.exists(path):
        return None
    import hashlib
    key = hashlib.md5(("%s|%s|%d" % (path, os.path.getmtime(path), size)).encode("utf-8")).hexdigest()[:16]
    folder = os.path.join(CACHE, "thumbs")
    out = os.path.join(folder, key + ".png")
    if not os.path.exists(out):
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
            src = drawing.Bitmap(path)
            k = float(size) / max(1, min(src.Size.Width, src.Size.Height))
            w, h = max(1, int(src.Size.Width * k)), max(1, int(src.Size.Height * k))
            bmp = drawing.Bitmap(w, h, drawing.PixelFormat.Format32bppRgba)
            g = drawing.Graphics(bmp)
            try:
                g.ImageInterpolation = drawing.ImageInterpolation.High
                g.DrawImage(src, 0.0, 0.0, float(w), float(h))
            finally:
                g.Dispose()
            bmp.Save(out, drawing.ImageFormat.Png)
            src.Dispose()
        except Exception:
            return None
    return "https://%s/.cache/thumbs/%s.png" % (HOST, key)


def presets_for_page(settings):
    out = {}
    for cat, recs in load_presets(settings).items():
        rows = []
        for r in recs:
            row = dict((k, v) for k, v in r.items() if not k.startswith("_") and k != "thumb")
            if r.get("thumb"):
                row["thumb_url"] = thumb_url(r["thumb"])
            rows.append(row)
        out[cat] = rows
    return out


def find_preset(pid, settings):
    for recs in load_presets(settings).values():
        for r in recs:
            if r["id"] == pid:
                return dict((k, v) for k, v in r.items() if not k.startswith("_"))
    return None


# ---------------------------------------------------------------- the sidebar's library and environment (4.1)

# Cosmos keeps materials and HDRIs as <kind>/<group>/<package>, people as 3D_Models/People/<package>
COSMOS_KINDS = {"materials": ("Materials", 2, (".vrmat",)), "hdris": ("HDRIs", 2, ("_light.vrmat", ".vrmat", ".hdr", ".exr")),
                "people": ("3D_Models/People", 1, (".vrmesh", ".vrscene", ".vrmat"))}


def cosmos_name(pkg):
    """'Pine_Matte_300cm_b94e1ea8' -> ('Pine Matte', '3 m'): the hash goes, the tile size is kept apart."""
    stem = re.sub(r"_[0-9a-f]{6,8}$", "", pkg)
    tile = ""
    m = re.search(r"_((?:0|[1-9]\d*)_\d{2}|\d+)cm$", stem)   # 37_04cm is 37.04 cm; 002_180cm is item 002 at 180 cm
    if m:
        cm = float(m.group(1).replace("_", "."))
        tile = ("%g m" % (cm / 100.0)) if cm >= 100 else ("%g cm" % cm)
        stem = stem[:m.start()]
    return " ".join(stem.replace("_", " ").split()) or pkg, tile


def _packages(kind):
    sub, depth, exts = COSMOS_KINDS[kind]
    base = os.path.join(COSMOS, *sub.split("/"))
    out = []
    if not os.path.isdir(base):
        return out
    groups = [("", base)] if depth == 1 else [(g, os.path.join(base, g)) for g in sorted(os.listdir(base))]
    for group, gdir in groups:
        if not os.path.isdir(gdir):
            continue   # Cosmos keeps a cpkg_copy.db beside the groups
        for pkg in sorted(os.listdir(gdir)):
            pdir = os.path.join(gdir, pkg)
            if os.path.isdir(pdir):
                out.append((sub, group, pkg, pdir, exts))
    return out


def cosmos_library(kind):
    items = []
    for sub, group, pkg, pdir, exts in _packages(kind):
        try:
            names = os.listdir(pdir)
        except Exception:
            continue
        pick = None
        for ext in exts:   # in order of preference: a ready dome light before a bare HDRI
            hits = sorted(f for f in names if f.lower().endswith(ext))
            if hits:
                pick = hits[0]
                break
        if not pick:
            continue
        name, tile = cosmos_name(pkg)
        rel = "/".join(x for x in (sub, group, pkg) if x)
        item = {"id": "cosmos:" + rel, "name": name, "group": (group or sub.split("/")[-1]).replace("_", " "),
                "tile": tile, "file": os.path.join(pdir, pick)}
        try:
            pics = sorted(f for f in os.listdir(os.path.join(pdir, "Previews")) if f.lower().endswith((".png", ".jpg", ".jpeg")))
        except Exception:
            pics = []
        if pics:
            item["thumb"] = "https://%s/%s" % (COSMOS_HOST, "/".join(System.Uri.EscapeDataString(x) for x in rel.split("/") + ["Previews", pics[0]]))
        items.append(item)
    return items


def cosmos_counts():
    out = {"root": COSMOS}
    for kind in COSMOS_KINDS:
        try:
            out[kind] = len(_packages(kind))
        except Exception:
            out[kind] = 0
    return out


def vray_name():
    """'V-Ray 7' from the installed plug-in's version, or None when V-Ray isn't installed."""
    try:
        for kv in Rhino.PlugIns.PlugIn.GetInstalledPlugIns():
            name = str(kv.Value)
            if name.lower().startswith("v-ray for rhino") and "import" not in name.lower():
                info = Rhino.PlugIns.PlugIn.GetPlugInInfo(kv.Key)
                m = re.match(r"(\d+)", str(info.Version) if info is not None else "")
                return "V-Ray %s" % m.group(1) if m else "V-Ray"
    except Exception:
        pass
    return None


def env_info(doc):
    """What the sidebar says things run in: Rhino, V-Ray, the units, the site, the named views, where renders go."""
    env = {"rhino": "Rhino %s" % Rhino.RhinoApp.ExeVersion, "vray": vray_name(), "cosmos": cosmos_counts()}
    try:
        env["units"] = str(doc.ModelUnitSystem)
        env["layer"] = doc.Layers.CurrentLayer.FullPath
    except Exception:
        pass
    env["doc_dir"] = os.path.dirname(doc.Path) if doc.Path else ""
    env["render_dir"] = os.path.join(env["doc_dir"], "Renders") if env["doc_dir"] else ""
    try:
        env["named_views"] = [nv.Name for nv in doc.NamedViews][:60]
    except Exception:
        env["named_views"] = []
    try:
        sun = doc.Lights.Sun
        lat, lon = round(sun.Latitude, 4), round(sun.Longitude, 4)
        default = abs(lat - 47.6) < 0.05 and abs(lon + 122.3) < 0.05   # Rhino's out-of-the-box location (Seattle)
        env["sun"] = {"lat": lat, "lon": lon, "enabled": bool(sun.Enabled), "set": not default}
    except Exception:
        env["sun"] = None
    return env


# ---------------------------------------------------------------- terminal

SENDKEYS_SPECIAL = set("+^%~(){}[]")


def prompt_text(note):
    """The note as safe prompt text: one line, no leading command characters, no @ mentions."""
    text = " ".join((note or "").replace("@", " at ").split())
    if text[:1] in ("/", "!", "#"):
        text = "Note: " + text
    return text


def sendkeys_escape(text):
    return "".join("{%s}" % ch if ch in SENDKEYS_SPECIAL else ch for ch in text)


def set_clipboard_image(png_path):
    """Put an image on the clipboard as a plain Windows bitmap (CF_DIB)."""
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import Clipboard
    img = System.Drawing.Bitmap(png_path)
    try:
        Clipboard.SetImage(img)
    finally:
        img.Dispose()


def paste_into_claude(note, png_paths, hwnd=None, settings=None):
    """Bring the chosen Claude Code window forward and put the request and the image(s) into its prompt.

    Keys are sent only once that window is confirmed as the foreground window, so nothing is ever typed
    into the wrong program. Claude Code pastes images with Alt+V on Windows. The prompt is left unsent:
    the user reads it, adds to it, presses Enter."""
    import ctypes
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import SendKeys
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    if not hwnd or not user32.IsWindow(ctypes.c_void_p(hwnd)):
        terms = terminal_windows()
        hwnd = choose_terminal(terms, settings)
        if not hwnd:
            return "no Claude Code window was found, so nothing was typed"
    hv = ctypes.c_void_p(hwnd)
    if user32.IsIconic(hv):
        user32.ShowWindow(hv, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hv)
    for _ in range(40):
        if (user32.GetForegroundWindow() or 0) == hwnd:
            break
        time.sleep(0.05)
    else:
        return "the terminal did not come forward, so nothing was typed"
    time.sleep(0.2)
    text = prompt_text(note)
    if text:
        SendKeys.SendWait(sendkeys_escape(text + " "))
    pngs = list(png_paths)[-6:]
    for k, png in enumerate(pngs):
        if (user32.GetForegroundWindow() or 0) != hwnd:
            return "the terminal lost focus after %d of %d images" % (k, len(pngs))
        set_clipboard_image(png)
        SendKeys.SendWait("%v")
        if k < len(pngs) - 1:
            time.sleep(1.5)   # Claude Code reads the clipboard after the key; let it before the next image
    return "pasted into Claude Code; check it and press Enter there"


# ---------------------------------------------------------------- send, batch, drafts

def preset_tag(task, presets):
    """'[Lighting: Golden hour, Sunset HDRI | Render: Final 4K]' for the front of the typed prompt."""
    labels = dict((c, l) for c, l, _ in CATEGORIES)
    parts = []
    for cat, _, _ in CATEGORIES:
        names = [p["name"] for p in presets if p.get("category") == cat]
        if names:
            parts.append("%s: %s" % (labels[cat], ", ".join(names)))
    if not parts:
        return "" if task in (None, "", "general", "model") else "[%s]" % labels.get(task, task)
    return "[%s]" % " | ".join(parts)


def write_marked(work, b64, strokes, under, w, h):
    """marked.png: the page's own render of what the user saw (exactly the user’s marks), else burned in here."""
    out = os.path.join(work, "marked.png")
    if b64:
        try:
            System.IO.File.WriteAllBytes(out, System.Convert.FromBase64String(b64))
            return out
        except Exception as ex:
            print("ClaudeMark: the page's image could not be written (%s); drawing it here instead." % ex)
    bitmaps = {}
    for st in strokes:
        if st.get("kind") == "ref" and st.get("file"):
            try:
                bitmaps[st["label"]] = drawing.Bitmap(os.path.join(work, st["file"]))
            except Exception:
                pass
    compose(os.path.join(work, "clean.png"), list(under) + list(strokes), out, w, h, bitmaps)
    return out


def send(doc, view, work, meta, strokes, note, w, h, b64=None, task="general", presets=(), under=(), camera=None,
         dest=None):
    """Write marked.png and meta.json, then publish the folder to the inbox (or the batch queue).
    `under` are marks drawn beneath the user’s (Claude's crit, when answering a reply). `camera` is a saved camera
    to pick with instead of the live view (a reply's or a draft's capture camera)."""
    meta["task"] = task
    meta["presets"] = list(presets)
    write_marked(work, b64, strokes, under, w, h)
    used = set(st["file"] for st in strokes if st["kind"] == "ref" and st.get("file"))
    used |= set(st["original"] for st in strokes if st["kind"] == "ref" and st.get("original"))
    for f in os.listdir(work):
        if f.startswith("ref") and f not in used:
            try:
                os.remove(os.path.join(work, f))
            except Exception:
                pass
    for f in ("state.json", "capture.json"):
        try:
            os.remove(os.path.join(work, f))
        except Exception:
            pass
    meta["note"] = note
    meta["strokes"] = strokes
    meta["picks"] = pick_hints(doc, view, strokes, camera)
    with open(os.path.join(work, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    dest = dest or INBOX
    if not os.path.isdir(dest):
        os.makedirs(dest)
    final = os.path.join(dest, os.path.basename(work).lstrip("."))
    os.rename(work, final)
    try:
        set_clipboard_image(os.path.join(final, "marked.png"))
    except Exception:
        pass
    return final


def queued():
    """Captures waiting in the batch, oldest first: [(folder, meta)]."""
    if not os.path.isdir(QUEUE):
        return []
    out = []
    for n in sorted(os.listdir(QUEUE)):
        p = os.path.join(QUEUE, n, "meta.json")
        if n.startswith(".") or not os.path.isfile(p):
            continue
        try:
            with open(p) as f:
                out.append((os.path.join(QUEUE, n), json.load(f)))
        except Exception:
            pass
    return out


def release_queue():
    """Move the batch into the inbox, so the next prompt attaches all of it. Returns the new folders."""
    moved = []
    for folder, meta in queued():
        if time.time() - float(meta.get("captured_unix", time.time())) > 5.5 * 3600:
            meta["captured_unix"] = time.time() - 60   # keep a long session's batch from going stale
            try:
                with open(os.path.join(folder, "meta.json"), "w") as f:
                    json.dump(meta, f, indent=1)
            except Exception:
                pass
        dest = os.path.join(INBOX, os.path.basename(folder))
        if not os.path.isdir(INBOX):
            os.makedirs(INBOX)
        try:
            os.rename(folder, dest)
            moved.append(dest)
        except Exception:
            pass
    return moved


def drafts(exclude=None):
    """Unsent markups left behind (a crash, or 'keep as draft'): inbox/.<stamp>/ holding state.json."""
    out = []
    if not os.path.isdir(INBOX):
        return out
    now = time.time()
    for n in sorted(os.listdir(INBOX), reverse=True):
        folder = os.path.join(INBOX, n)
        if not n.startswith(".") or folder == exclude or not os.path.isdir(folder):
            continue
        st_path = os.path.join(folder, "state.json")
        age = now - os.path.getmtime(folder)
        if not os.path.isfile(st_path) or not os.path.isfile(os.path.join(folder, "capture.json")):
            if age > 14 * 86400:
                shutil.rmtree(folder, True)   # a crash's leftovers, a fortnight old
            continue
        if age > 7 * 86400:
            continue
        try:
            with open(st_path) as f:
                state = json.load(f)
        except Exception:
            continue
        marks = len([s for s in state.get("strokes") or [] if s.get("kind") != "match"])
        if not marks and not (state.get("note") or "").strip():
            continue
        when = datetime.datetime.fromtimestamp(os.path.getmtime(st_path))
        out.append({"stamp": n.lstrip("."), "folder": folder, "marks": marks, "note": (state.get("note") or "")[:80],
                    "when": when.strftime("%H:%M") if when.date() == datetime.date.today() else when.strftime("%d %b %H:%M")})
    return out


def pending_reply():
    """The newest unanswered reply in outbox/: (folder, the capture it answers) or None."""
    if not os.path.isdir(OUTBOX):
        return None
    names = sorted(n for n in os.listdir(OUTBOX)
                   if not n.startswith(".") and os.path.isfile(os.path.join(OUTBOX, n, "reply.json")))
    if not names:
        return None
    folder = os.path.join(OUTBOX, names[-1])
    try:
        with open(os.path.join(folder, "reply.json")) as f:
            return folder, json.load(f).get("reply_to", "?")
    except Exception:
        return None


def archive_reply(folder):
    answered = os.path.join(OUTBOX, "answered")
    if not os.path.isdir(answered):
        os.makedirs(answered)
    shutil.move(folder, os.path.join(answered, os.path.basename(folder)))


def url_for(path):
    """https://cm.app/... for a file under ROOT."""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    import urllib
    return "https://%s/%s" % (HOST, "/".join(urllib.quote(part) for part in rel.split("/")))


# ---------------------------------------------------------------- the window

class GlassDialog(forms.Dialog[bool]):
    """The markup window: a WebView2 page (ui/) in an Eto dialog, talking to this script over
    postMessage. The page asks; the handlers below answer (do_<type>)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.result = {"action": "cancel"}
        self.blobs = {}
        self.allow_close = False
        self.ready = False
        self.failed = False
        self.core = None
        self.t0 = time.time()
        self.t_ready = None
        self.shots = []
        self.chrome_hwnd = None
        self.chrome_before = None
        self.chrome_wait = None
        self.dock = None
        self.tests = list(ctx.get("tests") or [])
        info = ctx["info"]
        self.Title = u"ClaudeMark  \u00b7  %s  \u00b7  %s  \u00b7  %s" % (info.get("doc"), info.get("view"), info.get("display"))
        self.Resizable = True
        self.Padding = drawing.Padding(0)
        self.BackgroundColor = drawing.Color.FromArgb(18, 18, 22)
        self.MinimumSize = drawing.Size(900, 600)
        self.wv = forms.WebView()
        self.Content = self.wv
        self.wv2 = self.wv.ControlObject
        try:
            self.wv2.DefaultBackgroundColor = System.Drawing.Color.FromArgb(255, 18, 18, 22)
        except Exception:
            pass
        self.wv2.CoreWebView2InitializationCompleted += self.on_init
        self.ClientSize = self.initial_size()
        self.Shown += self.on_shown
        self.Closing += self.on_closing
        self.watch = forms.UITimer()
        self.watch.Interval = 0.25
        self.watch.Elapsed += self.on_watch
        self.chrome_timer = forms.UITimer()
        self.chrome_timer.Interval = 0.15
        self.chrome_timer.Elapsed += self.on_chrome_timer

    def initial_size(self):
        s = self.ctx["settings"].get("window")
        try:
            area = forms.Screen.FromPoint(forms.Mouse.Position).WorkingArea
        except Exception:
            area = forms.Screen.PrimaryScreen.WorkingArea
        if s and s[0] <= area.Width and s[1] <= area.Height:
            return drawing.Size(int(s[0]), int(s[1]))
        iw, ih = float(self.ctx["w"]), float(self.ctx["h"])
        k = min(1.0, (area.Width * 0.94 - 48) / iw, (area.Height * 0.92 - 210) / ih)
        return drawing.Size(int(max(1000, min(area.Width * 0.96, iw * k + 48))), int(max(680, min(area.Height * 0.94, ih * k + 210))))

    # -- WebView2 plumbing

    def on_init(self, sender, e):
        try:
            if not e.IsSuccess:
                self.fail("WebView2 did not start (%s)" % e.InitializationException)
                return
            clr.AddReference("Microsoft.Web.WebView2.Core")
            from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind
            core = self.wv2.CoreWebView2
            self.core = core
            st = core.Settings
            for name, value in (("AreBrowserAcceleratorKeysEnabled", False), ("IsZoomControlEnabled", False),
                                ("IsPinchZoomEnabled", False), ("IsSwipeNavigationEnabled", False),
                                ("IsStatusBarEnabled", False), ("AreDefaultContextMenusEnabled", True)):
                try:
                    setattr(st, name, value)
                except Exception:
                    pass
            core.SetVirtualHostNameToFolderMapping(HOST, ROOT, CoreWebView2HostResourceAccessKind.Allow)
            if os.path.isdir(COSMOS):
                try:   # the library shows Cosmos previews straight from disk; the page falls back to made thumbnails
                    core.SetVirtualHostNameToFolderMapping(COSMOS_HOST, COSMOS, CoreWebView2HostResourceAccessKind.Allow)
                except Exception as ex:
                    log("cosmos mapping failed: %s" % ex)
            core.WebMessageReceived += self.on_message
            core.NavigationCompleted += self.on_nav
            ver = int(max(os.path.getmtime(os.path.join(UI_DIR, f)) for f in ("index.html", "app.js", "app.css")))
            log("init ok %.2fs, %s, root %s" % (time.time() - self.t0, core.Environment.BrowserVersionString, ROOT))
            core.Navigate("https://%s/ui/index.html?v=%d" % (HOST, ver))
        except Exception:
            import traceback
            self.fail(traceback.format_exc())

    def fail(self, why):
        log("FAIL " + str(why))
        print("ClaudeMark: the new window could not start, so the classic one opens.")
        print("ClaudeMark: reason: %s (log: %s)" % (str(why).strip().splitlines()[-1][:300] if str(why).strip() else "?",
                                                     os.path.join(CACHE, "claudemark.log")))
        self.failed = True
        self.allow_close = True
        self.result = {"action": "fallback"}
        self.Close(False)

    def on_nav(self, sender, e):
        log("navigation %s, status %s, %.2fs" % ("ok" if e.IsSuccess else "FAILED", e.WebErrorStatus, time.time() - self.t0))

    def on_shown(self, sender, e):
        self.watch.Start()
        try:
            self.wv.Focus()
        except Exception:
            pass
        self.dark_title(True)

    def on_watch(self, sender, e):
        if not self.ready and time.time() - self.t0 > 12 and not self.failed:
            self.watch.Stop()
            self.fail("the page did not answer within 12 s")
            return
        if self.ready and self.tests:
            self.run_tests()

    def on_closing(self, sender, e):
        if self.allow_close or not self.ready:
            # the close box always works before the page is up; after that the page asks first if the user marked
            self.watch.Stop()
            self.chrome_timer.Stop()
            self.remember_size()
            return
        e.Cancel = True
        self.emit("confirm_close", {})

    def remember_size(self):
        try:
            s = load_settings()
            s["window"] = [self.ClientSize.Width, self.ClientSize.Height]
            save_settings(s)
        except Exception:
            pass

    def finish(self, result, ok):
        """Close after the current WebView2 event has returned: closing the window from inside its own
        message handler, then answering a disposed WebView, is how an exception would reach Rhino."""
        self.result = result
        self.allow_close = True

        def close():
            try:
                self.Close(ok)
            except Exception:
                pass
        forms.Application.Instance.AsyncInvoke(System.Action(close))

    def emit(self, event, data):
        if self.core is None or self.allow_close:
            return
        d = dict(data)
        d["event"] = event
        try:
            self.core.PostWebMessageAsJson(json.dumps(d))
        except Exception:
            pass

    def reply(self, mid, ok, result=None, error=None):
        if self.core is None or mid is None or self.allow_close:
            return
        msg = {"id": mid, "ok": ok}
        if ok:
            msg["result"] = result
        else:
            msg["error"] = error
        try:
            self.core.PostWebMessageAsJson(json.dumps(msg))
        except Exception as ex:
            print("ClaudeMark: could not answer the page (%s)." % ex)

    def on_message(self, sender, e):
        try:
            s = e.TryGetWebMessageAsString()
        except Exception:
            return
        if s.startswith("BLOB:"):
            _, key, data = s.split(":", 2)
            self.blobs[key] = data
            return
        if s.startswith("DRAFT:"):
            self.write_draft(s[6:])
            return
        mid = None
        try:
            msg = json.loads(s)
            mid = msg.get("id")
            files = []
            try:
                objs = e.AdditionalObjects
                if objs is not None:
                    for i in range(objs.Count):
                        path = getattr(objs[i], "Path", None)
                        if path:
                            files.append(path)
            except Exception:
                pass
            handler = getattr(self, "do_" + msg.get("type", ""), None)
            if handler is None:
                self.reply(mid, False, error="unknown request " + msg.get("type", ""))
                return
            result = handler(msg.get("payload") or {}, files)
            self.reply(mid, True, result)
        except Exception as ex:
            import traceback
            log("handler %s failed: %s" % (s[:80], traceback.format_exc()))
            print("ClaudeMark: %s failed (%s)." % (s[:40], str(ex)[:200]))
            self.reply(mid, False, error=str(ex)[:200])

    def hwnd(self):
        """This window's native handle. Eto draws it with WPF, whose assembly IronPython only sees once
        referenced (version 3 got the reference by accident, from building its own WPF controls)."""
        clr.AddReference("PresentationFramework")
        from System.Windows.Interop import WindowInteropHelper
        return WindowInteropHelper(self.ControlObject).Handle.ToInt64()

    def dark_title(self, dark):
        try:
            import ctypes
            val = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(self.hwnd()), 20, ctypes.byref(val), 4)
        except Exception as ex:
            log("dark title bar: %s" % ex)

    # -- automated checks (CLAUDEMARK_TEST): run page scripts and save what the window shows

    def run_tests(self):
        # captures finish on the UI thread, so they are polled here, never waited on
        for item in list(self.shots):
            task, fs = item
            if task.IsCompleted:
                fs.Close()
                self.shots.remove(item)
        now = time.time()
        while self.tests and now >= self.tests[0][0] + self.t_ready:
            at, kind, arg = self.tests[0]
            if kind == "close" and self.shots:
                return   # wait for the captures to land
            self.tests.pop(0)
            if kind == "js":
                self.core.ExecuteScriptAsync(arg)
            elif kind == "shot":
                clr.AddReference("Microsoft.Web.WebView2.Core")
                from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                fs = System.IO.File.Create(arg)
                self.shots.append((self.core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs), fs))
            elif kind == "close":
                self.finish({"action": "cancel"}, False)
                return
            elif kind == "send":
                self.core.ExecuteScriptAsync("window.CMTest.send('%s')" % arg)
                return

    # -- what the page can ask for

    def do_boot(self, p, files):
        log("boot asked at %.2fs" % (time.time() - self.t0))
        return self.ctx["boot"]

    def do_ready(self, p, files):
        self.ready = True
        self.t_ready = time.time()
        log("ready in %.2fs (%s)" % (time.time() - self.t0, self.ctx["mode"]))
        return {}

    def do_log(self, p, files):
        log("page: " + (p.get("msg") or "")[:4000])
        print("ClaudeMark page: %s" % (p.get("msg") or "")[:300])
        return {}

    def do_theme(self, p, files):
        self.dark_title(bool(p.get("dark")))
        return {}

    def pick(self, x, y):
        doc, view = sc.doc, sc.doc.Views.ActiveView
        cam = self.ctx.get("camera")
        if cam is not None and not camera_matches(cam, view.ActiveViewport):
            return pick_with_camera(doc, cam, x, y)
        return pick_at(doc, view, x, y, self.ctx.get("selected") or ())

    def do_pick(self, p, files):
        t = time.time()
        try:
            hit = self.pick(float(p["x"]), float(p["y"]))
        except Exception as ex:
            hit = {"error": str(ex)[:120]}
        return {"hit": hit, "ms": int((time.time() - t) * 1000)}

    def do_measure(self, p, files):
        doc = sc.doc
        a, b = p["a"], p["b"]
        ha, hb = self.pick(a[0], a[1]), self.pick(b[0], b[1])
        cam = self.ctx["meta"]["camera"]
        units = str(doc.ModelUnitSystem)
        if ha and hb and ha.get("point") and hb.get("point"):
            pa, pb = Rhino.Geometry.Point3d(*ha["point"]), Rhino.Geometry.Point3d(*hb["point"])
            method = "surface"
        elif cam.get("projection") == "parallel":
            # a plan or an elevation: measure on the plane through the camera target
            z = screen_depth(cam, Rhino.Geometry.Point3d(*cam["target"]))
            pa, pb = unproject(cam, a[0], a[1], z), unproject(cam, b[0], b[1], z)
            method = "plane"
            if pa is None or pb is None:
                return {"text": "no depth here", "method": "none", "units": units}
        else:
            return {"text": "no model under an end", "method": "none", "units": units}
        d = pa.DistanceTo(pb)
        return {"value": round(d, 4), "units": units, "text": fmt_length(d, units), "method": method,
                "a": _pt(pa), "b": _pt(pb),
                "a_hit": (ha or {}).get("name") or (ha or {}).get("type"), "b_hit": (hb or {}).get("name") or (hb or {}).get("type")}

    def new_refs(self, sources, at, n, quiet_until_last=True):
        """Save each image source as refN.png in the capture folder; return what the page needs."""
        work = self.ctx["work"]
        refs, err = [], None
        for k, src in enumerate(sources):
            label_n = n + len(refs)
            try:
                path, bmp = load_reference(src, work, label_n)
            except Exception as ex:
                err = ex
                continue
            rec = {"label": "R%d" % label_n, "file": os.path.basename(path), "url": url_for(path) + "?t=%d" % int(time.time() * 1000),
                   "source": (src if isinstance(src, basestring) and not src.startswith("data:") else "pasted image")[:500],
                   "at": [at[0] + 40 * len(refs), at[1] + 40 * len(refs)]}
            if isinstance(src, basestring) and os.path.isfile(src):
                # Keep the original too: full resolution and EXIF (date, lens) for photo matching and montage.
                try:
                    orig = "ref%d_original%s" % (label_n, os.path.splitext(src)[1].lower())
                    shutil.copyfile(src, os.path.join(work, orig))
                    rec["original"] = orig
                except Exception:
                    pass
            refs.append(rec)
        return refs, err

    def refs_from_bytes(self, items, at, n):
        """Images that came over as bytes (a dropped file with no path on disk, or the mock page).
        Each is written out under its own extension first, so new_refs keeps it as the original too."""
        work = self.ctx["work"]
        pairs = []
        for i, b in enumerate(items):
            name = b.get("name") or "image.png"
            ext = os.path.splitext(name)[1].lower()
            tmp = os.path.join(work, ".drop%d%s" % (i, ext if ext in IMAGE_EXTS else ".png"))
            System.IO.File.WriteAllBytes(tmp, System.Convert.FromBase64String(b["data"]))
            pairs.append((tmp, name))
        try:
            refs, err = self.new_refs([t for t, _ in pairs], at, n)
        finally:
            for t, _ in pairs:
                try:
                    os.remove(t)
                except Exception:
                    pass
        names = dict(pairs)
        for r in refs:
            if r.get("source") in names:
                r["source"] = "dropped image " + names[r["source"]]
        return refs, err

    def do_ref(self, p, files):
        at, n = p.get("at") or [self.ctx["w"] / 2.0, self.ctx["h"] / 2.0], int(p.get("n") or 1)
        paths = [f for f in files if f.lower().endswith(IMAGE_EXTS)]
        err = None
        if paths:
            refs, err = self.new_refs(paths[:6], at, n)
            if refs:
                return {"refs": refs}
        if p.get("b64"):
            refs, err = self.refs_from_bytes(p["b64"][:6], at, n)
            if refs:
                return {"refs": refs}
        for alt in expand_alternatives(p.get("alts")):
            refs, err = self.new_refs([alt], at, n)
            if refs:
                return {"refs": refs}
        return {"error": "Couldn't get that image" + (": " + reason(err) if err else "")}

    def do_paste(self, p, files):
        at, n = p.get("at") or [self.ctx["w"] / 2.0, self.ctx["h"] / 2.0], int(p.get("n") or 1)
        cfiles, alts = clipboard_sources()
        err = None
        if cfiles:
            refs, err = self.new_refs(cfiles[:6], at, n)
            if refs:
                return {"refs": refs}
        for alt in [a for a in alts if not isinstance(a, basestring)] + expand_alternatives([a for a in alts if isinstance(a, basestring)]):
            refs, err = self.new_refs([alt], at, n)
            if refs:
                return {"refs": refs}
        return {"error": ("Couldn't get that image: " + reason(err)) if err else "No image on the clipboard"}

    def do_browse_image(self, p, files):
        d = forms.OpenFileDialog()
        d.Title = "Add a reference image"
        d.MultiSelect = True
        d.Filters.Add(forms.FileFilter("Images", *IMAGE_EXTS))
        if d.ShowDialog(self) != forms.DialogResult.Ok:
            return {"cancelled": True}
        names = list(d.Filenames) or [d.FileName]
        refs, err = self.new_refs(names[:6], p.get("at") or [0, 0], int(p.get("n") or 1))
        self.wv.Focus()
        return {"refs": refs} if refs else {"error": "Couldn't read that image (%s)" % str(err)[:80]}

    def presets_result(self, pid, cat):
        s = load_settings()
        rec = find_preset(pid, s) or {}
        return {"presets": presets_for_page(s), "id": pid, "category": cat, "name": rec.get("name", "")}

    def do_library(self, p, files):
        kind = p.get("kind") or "materials"
        if kind not in COSMOS_KINDS:
            raise Exception("no library called %s" % kind)
        return {"items": cosmos_library(kind), "root": COSMOS}

    def do_thumbs(self, p, files):
        """Preview thumbnails for library tiles, a few at a time (made once, then cached in .cache/thumbs)."""
        urls, done = {}, []
        root = os.path.normcase(os.path.abspath(COSMOS))
        t0 = time.time()
        for iid in (p.get("ids") or [])[:16]:
            if not iid.startswith("cosmos:"):
                done.append(iid)
                continue
            pdir = os.path.abspath(os.path.join(COSMOS, *iid[7:].split("/")))
            if not os.path.normcase(pdir).startswith(root + os.sep):
                done.append(iid)
                continue
            prev = os.path.join(pdir, "Previews")
            try:
                pics = sorted(f for f in os.listdir(prev) if f.lower().endswith((".png", ".jpg", ".jpeg")))
            except Exception:
                pics = []
            if pics:
                u = thumb_url(os.path.join(prev, pics[0]), 192)
                if u:
                    urls[iid] = u
            done.append(iid)
            if time.time() - t0 > 1.5:
                break
        return {"urls": urls, "done": done}

    def do_preset_save(self, p, files):
        """Save the options as a setup of the user’s own: presets/<task>/<name>.json, kind setup."""
        task = p.get("task") or "general"
        if task not in ALL_CATS:
            raise Exception("no task called %s" % task)
        name = " ".join((p.get("name") or "").split())[:40] or "Setup"
        rec = {"name": name, "summary": (p.get("summary") or "")[:120], "kind": p.get("kind") or "setup",
               "options": p.get("options") or {}, "category": task, "builtin": False}
        folder = os.path.join(PRESETS_DIR, task)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        fn = "%s.json" % _slug(name)
        while os.path.exists(os.path.join(folder, fn)):
            fn = "%s-%d.json" % (_slug(name), int(time.time() * 1000) % 100000)
        with open(os.path.join(folder, fn), "w") as f:
            json.dump(rec, f, indent=1)
        return {"presets": presets_for_page(load_settings()), "id": task + "/" + fn}

    def do_preset_update(self, p, files):
        pid = p.get("id") or ""
        path = os.path.abspath(os.path.join(PRESETS_DIR, pid.replace("/", os.sep)))
        if ".." in pid or not path.lower().endswith(".json") or not os.path.isfile(path):
            raise Exception("no preset called %s" % pid)
        with open(path) as f:
            rec = json.load(f)
        if rec.get("builtin"):
            raise Exception("built-in presets can't be changed")
        for k in ("name", "summary", "options"):
            if p.get(k) is not None:
                rec[k] = p[k] if k != "name" else " ".join(p[k].split())[:40]
        with open(path, "w") as f:
            json.dump(rec, f, indent=1)
        return {"presets": presets_for_page(load_settings())}

    def do_choose_folder(self, p, files):
        d = forms.SelectFolderDialog()
        d.Title = p.get("title") or "Choose a folder"
        start = p.get("start") or ""
        if start and os.path.isdir(start):
            d.Directory = start
        ok = d.ShowDialog(self) == forms.DialogResult.Ok
        self.wv.Focus()
        return {"path": d.Directory} if ok and d.Directory else {"cancelled": True}

    def do_preset_add(self, p, files):
        task = p.get("task") or "materials"
        err = None
        sources = list(files)
        for b in p.get("b64") or []:
            tmp = os.path.join(CACHE, "drop")
            if not os.path.isdir(tmp):
                os.makedirs(tmp)
            path = os.path.join(tmp, re.sub(r"[^\w.\-]", "_", b.get("name") or "image.png"))
            System.IO.File.WriteAllBytes(path, System.Convert.FromBase64String(b["data"]))
            sources.append(path)
        sources += expand_alternatives(p.get("alts"))
        for src in sources:
            try:
                pid, cat = preset_from_source(src, task, bool(p.get("library")))
                return self.presets_result(pid, cat)
            except Exception as ex:
                err = ex
        raise Exception("Couldn't make a preset from that (%s)" % str(err)[:80])

    def do_preset_browse(self, p, files):
        d = forms.OpenFileDialog()
        task = p.get("task") or "materials"
        if p.get("library") and task == "lighting":
            d.Title = "Add an HDRI to your library"
            d.Filters.Add(forms.FileFilter("HDRIs and V-Ray dome lights", ".hdr", ".exr", ".vrmat"))
        elif p.get("library"):
            d.Title = "Add a material to your library"
            d.Filters.Add(forms.FileFilter("V-Ray materials and photos", ".vrmat", ".png", ".jpg", ".jpeg", ".webp", ".heic"))
        else:
            d.Title = "Add a preset"
            d.Filters.Add(forms.FileFilter("Presets, materials, HDRIs, images", ".json", ".vrmat", ".hdr", ".exr", ".vrscene",
                                           ".vropt", ".png", ".jpg", ".jpeg", ".webp"))
        if d.ShowDialog(self) != forms.DialogResult.Ok:
            self.wv.Focus()
            return {"cancelled": True}
        pid, cat = preset_from_source(d.FileName, task, bool(p.get("library")))
        self.wv.Focus()
        return self.presets_result(pid, cat)

    def do_preset_remove(self, p, files):
        s = load_settings()
        rec = find_preset(p["id"], s)
        if rec and rec.get("builtin"):
            hidden = s.setdefault("hidden_presets", [])
            if p["id"] not in hidden:
                hidden.append(p["id"])
            save_settings(s)
        elif rec:
            try:
                os.remove(os.path.join(PRESETS_DIR, p["id"].replace("/", os.sep)))
            except Exception:
                pass
        return {"presets": presets_for_page(load_settings())}

    def do_preset_edit(self, p, files):
        path = os.path.join(PRESETS_DIR, p["id"].replace("/", os.sep))
        System.Diagnostics.Process.Start("notepad.exe", '"%s"' % path)
        return {}

    def do_settings(self, p, files):
        s = load_settings()
        for k, v in (p.get("settings") or {}).items():
            if k not in PY_OWNED:
                s[k] = v
        save_settings(s)
        self.ctx["settings"] = s
        return {}

    def do_terminals(self, p, files):
        terms = terminal_windows()
        return {"terminals": terms, "target": choose_terminal(terms, load_settings())}

    def do_save_png(self, p, files):
        data = self.blobs.get(p.get("blob") or "marked")
        d = forms.SaveFileDialog()
        d.Title = "Save the marked image"
        d.Filters.Add(forms.FileFilter("PNG image", ".png"))
        s = load_settings()
        folder = s.get("save_dir") or os.path.join(
            System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyPictures), "ClaudeMark")
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except Exception:
                pass
        try:
            d.Directory = System.Uri(folder)
        except Exception:
            pass
        d.FileName = "ClaudeMark %s.png" % datetime.datetime.now().strftime("%Y-%m-%d %H%M%S")
        if d.ShowDialog(self) != forms.DialogResult.Ok:
            self.wv.Focus()
            return {"cancelled": True}
        path = d.FileName
        if not path.lower().endswith(".png"):
            path += ".png"
        System.IO.File.WriteAllBytes(path, System.Convert.FromBase64String(data))
        s["save_dir"] = os.path.dirname(path)
        save_settings(s)
        self.wv.Focus()
        return {"path": path, "name": os.path.basename(path)}

    def do_copy_png(self, p, files):
        data = self.blobs.get(p.get("blob") or "marked")
        tmp = os.path.join(CACHE, "copy.png")
        if not os.path.isdir(CACHE):
            os.makedirs(CACHE)
        System.IO.File.WriteAllBytes(tmp, System.Convert.FromBase64String(data))
        set_clipboard_image(tmp)
        return {}

    def do_examples(self, p, files):
        self.open_examples()
        return {}

    def do_open_folder(self, p, files):
        os.startfile(ROOT)
        return {}

    def do_queue_clear(self, p, files):
        for folder, meta in queued():
            shutil.rmtree(folder, True)
        return {"queue": []}

    def do_reply_dismiss(self, p, files):
        folder = os.path.join(OUTBOX, p["id"])
        if os.path.isdir(folder):
            archive_reply(folder)
        return {}

    def do_reply_open(self, p, files):
        self.do_settings(p, files)
        self.finish({"action": "open_reply"}, False)
        return {}

    def do_draft_open(self, p, files):
        self.do_settings(p, files)
        self.finish({"action": "open_draft", "stamp": p["stamp"]}, False)
        return {}

    def do_draft_discard(self, p, files):
        folder = os.path.join(INBOX, "." + p["stamp"])
        if os.path.isdir(folder) and folder != self.ctx["work"]:
            shutil.rmtree(folder, True)
        return {}

    def do_cancel(self, p, files):
        self.do_settings(p, files)
        self.finish({"action": "cancel"}, False)
        return {}

    def do_send(self, p, files):
        self.do_settings(p, files)
        p["png"] = self.blobs.get(p.get("blob") or "marked")
        self.finish(dict(p, action="send" if p.get("mode") != "queue" else "queue"), True)
        return {}

    def write_draft(self, text):
        work = self.ctx["work"]
        if not os.path.isdir(work):
            return
        try:
            with open(os.path.join(work, "state.json"), "w") as f:
                f.write(text)
        except Exception:
            pass

    # -- Chrome beside the window (Find examples)

    def open_examples(self):
        """Dock this window on the left of the screen and a Chrome window on the right."""
        me = self.hwnd()
        wp, wo = screen_of(me, True)[0], screen_of(me, False)[0]
        # this window takes the left of the work area, in its own pixels; Chrome the rest, in physical ones
        place_window(me, wo[0], wo[1], int(round(wo[2] * EXAMPLES_SPLIT)), wo[3], own=True)
        split = int(round(wp[2] * EXAMPLES_SPLIT))
        self.dock = (wp[0] + split, wp[1], wp[2] - split, wp[3])
        if window_alive(self.chrome_hwnd):
            place_window(self.chrome_hwnd, *self.dock)
            ctypes, u, RECT = _win32()
            u.SetForegroundWindow(ctypes.c_void_p(self.chrome_hwnd))
            return
        chrome = find_chrome()
        if chrome is None:
            os.startfile(EXAMPLES_URL)
            self.emit("toast", {"text": "Chrome isn't installed, so your default browser opened instead", "icon": "warn"})
            return
        self.chrome_before = set(chrome_windows())
        System.Diagnostics.Process.Start(chrome, '--new-window "%s"' % EXAMPLES_URL)
        self.chrome_wait = time.time() + 10
        self.chrome_timer.Start()
        self.emit("toast", {"text": "Opening Chrome...", "icon": "search"})

    def on_chrome_timer(self, sender, e):
        try:
            new = [h for h in chrome_windows() if h not in self.chrome_before]
            if new:
                self.chrome_timer.Stop()
                self.chrome_hwnd = new[0]
                place_window(self.chrome_hwnd, *self.dock)
                ctypes, u, RECT = _win32()
                u.SetForegroundWindow(ctypes.c_void_p(self.chrome_hwnd))
                self.emit("toast", {"text": "Find an image in Chrome, then drag it onto the spot it's for", "icon": "search", "ms": 5000})
            elif time.time() > self.chrome_wait:
                self.chrome_timer.Stop()
                self.emit("toast", {"text": "Chrome opened but couldn't be docked; drag images in from wherever it is", "icon": "warn"})
        except Exception:
            self.chrome_timer.Stop()


# ---------------------------------------------------------------- building a session

def page_categories():
    return [{"id": c, "label": l, "icon": i, "example": TASK_TEXT[c][1], "hint": TASK_TEXT[c][0],
             "drop": "Drop " + DROP_TEXT.get(c, "images or preset files") + " here, or click to choose"}
            for c, l, i in CATEGORIES]


def boot_payload(ctx):
    settings = ctx["settings"]
    terms = terminal_windows()
    queue = [{"stamp": os.path.basename(f), "marks": len(m.get("strokes") or []), "note": (m.get("note") or "")[:80]}
             for f, m in queued()]
    pend = pending_reply() if ctx["mode"] == "capture" else None
    pending = None
    if pend:
        try:
            with open(os.path.join(pend[0], "reply.json")) as f:
                title = json.load(f).get("title", "")
        except Exception:
            title = ""
        pending = {"id": os.path.basename(pend[0]), "reply_to": pend[1], "title": title}
    page_settings = dict((k, v) for k, v in settings.items() if k not in ("hidden_presets", "window"))
    boot = {
        "version": VERSION,
        "mode": ctx["mode"],
        "image": {"url": url_for(os.path.join(ctx["work"], "clean.png")), "w": ctx["w"], "h": ctx["h"]},
        "info": ctx["info"],
        "settings": page_settings,
        "categories": page_categories(),
        "presets": presets_for_page(settings),
        "env": env_info(sc.doc),
        "notes": {"render_note": RENDER_NOTE},
        "text": {"reply": list(REPLY_TEXT), "options": list(OPTIONS_TEXT), "style": list(STYLE_TEXT)},
        "terminals": terms,
        "target": choose_terminal(terms, settings),
        "queue": queue,
        "pending_reply": pending,
        "drafts": [dict((k, v) for k, v in d.items() if k != "folder") for d in drafts(exclude=ctx["work"])] if ctx["mode"] == "capture" else [],
        "reply": None,
    }
    reply = ctx.get("reply")
    if reply:
        r = {"title": reply.get("title", ""), "reply_to": reply.get("reply_to", ""), "summary": reply.get("summary", ""),
             "strokes": reply.get("strokes") or []}
        if reply.get("kind") == "options":
            r["options"] = [dict(o, url=url_for(os.path.join(reply["_folder"], o["image"]))) for o in reply.get("options") or []]
            r["mode"] = reply.get("mode")
        boot["reply"] = r
    if ctx.get("restore"):
        boot["restore"] = ctx["restore"]
        boot["draft_when"] = ctx.get("draft_when", "")
    return boot


def run_dialog(ctx):
    """Show the window; returns its result dict. Falls back to the classic window if WebView2 fails."""
    ctx["boot"] = boot_payload(ctx)
    dlg = GlassDialog(ctx)
    dlg.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)
    return dlg.result


def run_classic():
    if not os.path.isfile(CLASSIC):
        print("ClaudeMark: the classic window isn't there either (%s)." % CLASSIC)
        return
    ns = {"__name__": "__main__", "__file__": CLASSIC}
    exec(compile(open(CLASSIC).read(), CLASSIC, "exec"), ns)


def finish_session(ctx, res):
    """Act on what the window returned: send, queue, cancel, or switch to a reply or a draft."""
    doc = sc.doc
    work, action = ctx["work"], res.get("action", "cancel")
    settings = load_settings()
    if action == "fallback":
        shutil.rmtree(work, True)
        if not ctx.get("tests"):   # an automated run must never leave a window waiting for a person
            run_classic()
        return
    if action in ("cancel",):
        shutil.rmtree(work, True)
        if ctx["mode"] in ("reply", "options", "style"):
            print("ClaudeMark: reply left for later; ClaudeReply opens it again.")
        else:
            print("ClaudeMark: cancelled.")
        return
    if action in ("open_reply", "open_draft"):
        keep = os.path.isfile(os.path.join(work, "state.json")) and ctx["mode"] in ("capture", "draft")
        if not keep:
            shutil.rmtree(work, True)
        if action == "open_reply":
            main_reply()
        else:
            main_draft(res.get("stamp"))
        return
    # send or queue
    strokes = res.get("strokes") or []
    note = res.get("note") or ""
    task = res.get("task") or "general"
    presets = [r for r in (find_preset(pid, settings) for pid in res.get("presets") or []) if r]
    meta = ctx["meta"]
    brief = (res.get("task_brief") or "").strip()
    if res.get("task_options") is not None:
        meta["task_options"] = res.get("task_options")
    if brief:   # the sidebar's options, as Claude reads them: first among the presets, so the hook shows it too
        meta["task_brief"] = brief
        presets.insert(0, {"category": task, "name": "%s options" % (res.get("task_label") or task.title()),
                           "instructions": brief, "kind": "options"})
    under = []
    if ctx["mode"] in ("reply", "options", "style"):
        reply = ctx["reply"]
        meta.update({"reply_to": reply.get("reply_to"), "reply_id": os.path.basename(reply["_folder"]),
                     "reply_title": reply.get("title", ""), "claude_strokes": res.get("claude_strokes") or reply.get("strokes") or []})
        under = meta["claude_strokes"] if ctx["mode"] == "reply" else []
        if ctx["mode"] in ("options", "style"):
            chosen = res.get("option")
            opt = next((o for o in reply.get("options") or [] if o.get("label") == chosen), (reply.get("options") or [{}])[0])
            meta["chosen_option"] = dict((k, opt.get(k)) for k in ("label", "title", "idea", "settings"))
            meta["options"] = [dict((k, o.get(k)) for k in ("label", "title", "idea")) for o in reply.get("options") or []]
            if ctx["mode"] == "style":
                meta["options_mode"] = "style"
                meta["styles_file"] = reply.get("styles")
                meta["styles_previews"] = reply.get("previews")
            try:
                shutil.copyfile(os.path.join(reply["_folder"], opt["image"]), os.path.join(work, "clean.png"))
            except Exception:
                pass
    camera = ctx.get("camera")
    if camera is not None and camera_matches(camera, doc.Views.ActiveView.ActiveViewport):
        camera = None   # the view never moved: live picks are better (selected objects count too)
    queue_it = action == "queue"
    final = send(doc, doc.Views.ActiveView, work, meta, strokes, note, ctx["w"], ctx["h"], res.get("png"),
                 task, presets, under=under, camera=camera, dest=QUEUE if queue_it else INBOX)
    if ctx["mode"] in ("reply", "options", "style"):
        try:
            archive_reply(ctx["reply"]["_folder"])
        except Exception:
            pass
    if queue_it:
        n = len(queued())
        print("ClaudeMark: added %s to the batch (%d waiting). Mark the next view; Send pastes them all." % (
            os.path.basename(final), n))
        return
    batch = release_queue()
    images = [os.path.join(f, "marked.png") for f in batch] + [os.path.join(final, "marked.png")]
    status = "type your request in Claude Code; the next prompt picks it up"
    jump = (res.get("settings") or {}).get("jump", settings.get("jump", True))
    if jump and ctx.get("no_paste"):
        status = "test run: nothing pasted"
    elif jump:
        if True:
            try:
                if ctx["mode"] in ("options", "style"):
                    word = "Style" if ctx["mode"] == "style" else "Option"
                    tag = "[%s %s]" % (word, res.get("option") or "?")
                elif ctx["mode"] == "reply":
                    tag = "[Reply to Claude]"
                else:
                    tag = ("[%s]" % res["task_tag"]) if res.get("task_tag") else preset_tag(task, [x for x in presets if x.get("kind") != "options"])
                    if batch:
                        tag = (tag + " " if tag else "") + "[Batch of %d captures]" % (len(batch) + 1)
                typed = (tag + " " + note).strip()
                status = paste_into_claude(typed, images, res.get("target"), settings)
            except Exception as ex:
                status = "paste failed (%s); the next prompt still picks it up" % ex
    print("ClaudeMark: sent %s (%d marks%s): %s." % (os.path.basename(final), len(strokes),
                                                     ", with %d from the batch" % len(batch) if batch else "", status))


def capture_info(doc, vp, w, h, extra=None):
    info = {"doc": doc.Name or "Untitled", "path": doc.Path or "", "view": vp.Name,
            "display": vp.DisplayMode.EnglishName, "units": str(doc.ModelUnitSystem), "size": [w, h],
            "selected": len(list(doc.Objects.GetSelectedObjects(False, False)))}
    if extra:
        info.update(extra)
    return info


def new_work():
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    work = os.path.join(INBOX, "." + stamp)
    k = 1
    while os.path.exists(work) or os.path.exists(os.path.join(INBOX, stamp)):
        work = os.path.join(INBOX, ".%s-%d" % (stamp, k))
        k += 1
    os.makedirs(work)
    return work


def main():
    doc = sc.doc
    view = doc.Views.ActiveView
    if view is None:
        print("ClaudeMark: no active view.")
        return
    vp = view.ActiveViewport
    w, h = vp.Size.Width, vp.Size.Height
    work = new_work()
    bmp = view.CaptureToBitmap(System.Drawing.Size(w, h))
    if bmp is None:
        print("ClaudeMark: the view capture failed.")
        shutil.rmtree(work, True)
        return
    bmp.Save(os.path.join(work, "clean.png"), System.Drawing.Imaging.ImageFormat.Png)
    meta = gather_meta(doc, view, w, h)
    with open(os.path.join(work, "capture.json"), "w") as f:
        json.dump(meta, f)
    settings = load_settings()
    try:
        install_presets(settings)
    except Exception:
        pass
    ctx = {"mode": "capture", "work": work, "w": w, "h": h, "meta": meta, "settings": settings,
           "info": capture_info(doc, vp, w, h), "camera": None,
           "selected": list(doc.Objects.GetSelectedObjects(False, False))[:SELECTION_LIMIT]}
    ctx.update(globals().get("CLAUDEMARK_TEST") or {})
    finish_session(ctx, run_dialog(ctx))


def main_reply():
    """ClaudeReply: open Claude's newest reply (its crit drawn on the user’s capture) so the user can answer on top."""
    pend = pending_reply()
    if pend is None:
        print("ClaudeMark: no reply from Claude is waiting.")
        return
    folder = pend[0]
    with open(os.path.join(folder, "reply.json")) as f:
        reply = json.load(f)
    reply["_folder"] = folder
    base = reply.get("meta") or {}
    if not base.get("view", {}).get("size"):
        print("ClaudeMark: that reply has no capture record, so it can't be opened (%s)." % folder)
        return
    w, h = base["view"]["size"]
    work = new_work()
    shutil.copyfile(os.path.join(folder, "clean.png"), os.path.join(work, "clean.png"))
    doc = sc.doc
    is_options = reply.get("kind") == "options"
    mode = ("style" if reply.get("mode") == "style" else "options") if is_options else "reply"
    meta = copy.deepcopy(base)
    meta.update({"tool": "ClaudeMark", "version": 4, "captured": datetime.datetime.now().isoformat(),
                 "captured_unix": time.time(), "rhino_pid": System.Diagnostics.Process.GetCurrentProcess().Id,
                 "selection": [], "subobject_selection": [], "history_tail": history_tail()})
    meta["doc"] = dict(meta.get("doc") or {}, path=doc.Path or "", name=doc.Name or "Untitled",
                       modified=doc.Modified, object_count=live_object_count(doc))
    info = {"doc": "%s %s" % ("Options for" if is_options else "Reply to", reply.get("reply_to", "?")),
            "path": doc.Path or "", "view": (base.get("view") or {}).get("name", ""),
            "display": reply.get("title") or ("Options" if is_options else "Claude's crit"), "size": [w, h]}
    ctx = {"mode": mode, "work": work, "w": w, "h": h, "meta": meta, "settings": load_settings(), "info": info,
           "camera": base.get("camera"), "reply": reply, "selected": []}
    ctx.update(globals().get("CLAUDEMARK_TEST") or {})
    finish_session(ctx, run_dialog(ctx))


def main_draft(stamp):
    """Reopen an unsent markup on its own capture and camera."""
    folder = os.path.join(INBOX, "." + (stamp or ""))
    if not os.path.isdir(folder):
        print("ClaudeMark: that draft is gone.")
        return
    with open(os.path.join(folder, "capture.json")) as f:
        meta = json.load(f)
    with open(os.path.join(folder, "state.json")) as f:
        state = json.load(f)
    w, h = meta["view"]["size"]
    doc = sc.doc
    refs = dict((st["label"], url_for(os.path.join(folder, st["file"]))) for st in state.get("strokes") or []
                if st.get("kind") == "ref" and st.get("file"))
    restore = {"strokes": state.get("strokes") or [], "note": state.get("note") or "", "task": state.get("task"),
               "presets": state.get("presets") or [], "picks": state.get("picks") or None,
               "ref_count": state.get("ref_count") or 0, "ref_urls": refs}
    when = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(folder, "state.json"))).strftime("%H:%M")
    info = {"doc": (meta.get("doc") or {}).get("name", "Untitled"), "path": (meta.get("doc") or {}).get("path", ""),
            "view": meta["view"].get("name", ""), "display": meta["view"].get("display_mode", ""), "size": [w, h]}
    meta["captured_unix"] = time.time()
    ctx = {"mode": "draft", "work": folder, "w": w, "h": h, "meta": meta, "settings": load_settings(), "info": info,
           "camera": meta.get("camera"), "restore": restore, "draft_when": when, "selected": []}
    ctx.update(globals().get("CLAUDEMARK_TEST") or {})
    finish_session(ctx, run_dialog(ctx))


if not globals().get("CLAUDEMARK_NO_UI"):
    if globals().get("CLAUDEMARK_MODE") == "reply":
        main_reply()
    else:
        main()
