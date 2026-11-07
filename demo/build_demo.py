# -*- coding: utf-8 -*-
# ClaudeMark demo: the Lakeside Pavilion, the model in the launch film and the screenshots.
#
# Use: in Rhino 8, File > New (any template), then run
#     _-RunPythonScript "<this folder>\build_demo.py"
# It builds the pavilion in millimetres in a few seconds, with materials, a sun and four named views
# (Approach, Aerial, Deck, Lake), and opens the Approach view in Rendered mode. Then type ClaudeMark.
#
# Two faults are left in on purpose, so there is something to mark up and ask Claude to fix:
#   1. the oak service core stops 600 mm short of the roof soffit
#   2. pane S5 on the south face of the glass room is missing (its mullions stay)
# It only runs in an empty model, so it can never touch your own work.
import math
import System
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
from System.Drawing import Color

doc = sc.doc
doc.ModelUnitSystem = Rhino.UnitSystem.Millimeters

# ---------------------------------------------------------------- only into an empty model
if doc.Objects.Count > 0:
    raise Exception("The ClaudeMark demo builds into an empty model. File > New, then run this script again.")

def layer(path, col):
    idx = doc.Layers.FindByFullPath(path, -1)
    if idx >= 0:
        return idx
    parent = None
    if "::" in path:
        ppath, name = path.rsplit("::", 1)
        parent = doc.Layers[layer(ppath, Color.Gray)]
    else:
        name = path
    L = Rhino.DocObjects.Layer()
    L.Name = name
    L.Color = col
    if parent is not None:
        L.ParentLayerId = parent.Id
    return doc.Layers.Add(L)

def mat(name, rgb, refl=0.0, transp=0.0, gloss=0.0):
    m = Rhino.DocObjects.Material()
    m.Name = name
    m.DiffuseColor = Color.FromArgb(rgb[0], rgb[1], rgb[2])
    m.Reflectivity = refl
    m.Transparency = transp
    m.Shine = gloss * Rhino.DocObjects.Material.MaxShine
    if transp > 0:
        m.TransparentColor = Color.FromArgb(255, 255, 255)
        m.IndexOfRefraction = 1.52
    rm = Rhino.Render.RenderMaterial.CreateBasicMaterial(m, doc)
    rm.Name = name
    doc.RenderMaterials.Add(rm)
    return rm

LAY = {}
spec = [
    ("SITE::GROUND", Color.FromArgb(120, 140, 110), mat("Meadow", (150, 165, 128))),
    ("SITE::WATER", Color.FromArgb(70, 110, 140), mat("Lake", (64, 98, 120), refl=0.0, gloss=0.0)),
    ("SITE::PATH", Color.FromArgb(200, 195, 185), mat("Gravel path", (196, 190, 178))),
    ("SITE::TREES", Color.FromArgb(80, 120, 70), mat("Foliage", (92, 124, 78))),
    ("SITE::TRUNKS", Color.FromArgb(90, 70, 55), mat("Bark", (96, 78, 62))),
    ("PAVILION::DECK", Color.FromArgb(180, 178, 172), mat("Board-formed concrete", (186, 183, 176))),
    ("PAVILION::STAIR", Color.FromArgb(180, 178, 172), mat("Concrete stair", (178, 175, 168))),
    ("PAVILION::COLUMNS", Color.FromArgb(40, 40, 42), mat("Blackened steel", (46, 46, 48), refl=0.15, gloss=0.5)),
    ("PAVILION::ROOF", Color.FromArgb(235, 233, 228), mat("White render", (236, 234, 229))),
    ("PAVILION::CORE", Color.FromArgb(160, 110, 70), mat("Oak cladding", (168, 120, 78))),
    ("PAVILION::FINS", Color.FromArgb(160, 110, 70), mat("Oak fins", (176, 128, 84))),
    ("PAVILION::GLAZING", Color.FromArgb(150, 190, 210), mat("Tinted glass", (96, 138, 150), refl=0.35, transp=0.32, gloss=1.0)),
    ("PAVILION::MULLIONS", Color.FromArgb(40, 40, 42), mat("Black aluminium", (40, 40, 42), refl=0.1, gloss=0.4)),
    ("PAVILION::FURNITURE", Color.FromArgb(120, 90, 60), mat("Walnut", (110, 80, 56))),
]
for path, col, rm in spec:
    i = layer(path, col)
    L = doc.Layers[i]
    L.RenderMaterial = rm
    LAY[path] = i

def attrs(path, name=None):
    a = Rhino.DocObjects.ObjectAttributes()
    a.LayerIndex = LAY[path]
    if name:
        a.Name = name
    return a

def box(path, x0, y0, z0, x1, y1, z1, name=None):
    b = rg.BoundingBox(rg.Point3d(min(x0, x1), min(y0, y1), min(z0, z1)), rg.Point3d(max(x0, x1), max(y0, y1), max(z0, z1)))
    br = rg.Brep.CreateFromBox(b)
    return doc.Objects.AddBrep(br, attrs(path, name))

# ---------------------------------------------------------------- site
ground = rg.Brep.CreateFromCornerPoints(rg.Point3d(-60000, -45000, 0), rg.Point3d(60000, -45000, 0),
                                        rg.Point3d(60000, 9000, 0), rg.Point3d(-60000, 9000, 0), 1)
doc.Objects.AddBrep(ground, attrs("SITE::GROUND", "Meadow"))
# the lake: north of the pavilion, a little lower, with a concrete edge
lake = rg.Brep.CreateFromCornerPoints(rg.Point3d(-60000, 9000, -450), rg.Point3d(60000, 9000, -450),
                                      rg.Point3d(60000, 60000, -450), rg.Point3d(-60000, 60000, -450), 1)
doc.Objects.AddBrep(lake, attrs("SITE::WATER", "Lake"))
box("PAVILION::DECK", -60000, 8800, -450, 60000, 9200, 0, "Lake edge")
# gravel path from the south
box("SITE::PATH", -1400, -45000, 0, 1400, -6400, 30, "Approach path")

# ---------------------------------------------------------------- pavilion (deck 20 x 11 m, roof 24 x 14.4 m)
DX, DY, DH = 20000.0, 11000.0, 450.0
box("PAVILION::DECK", -DX / 2, -DY / 2 + 600, 0, DX / 2, DY / 2 + 3300, DH, "Deck slab")   # runs to the lake edge
for k in range(2):   # three risers of 150 down to the path on the south: treads at 300 and 150
    box("PAVILION::STAIR", -1800, -DY / 2 + 600 - 380 * (k + 1), 0, 1800, -DY / 2 + 600 - 380 * k, DH - 150 * (k + 1), "Step %d" % (k + 1))
ROOF_Z = DH + 3400.0
RT = 280.0
# columns on a 3.6 m grid, two rows
xs = [-7200, -3600, 0, 3600, 7200]
ys = [-3800, 3800]
for x in xs:
    for y in ys:
        box("PAVILION::COLUMNS", x - 80, y - 80, DH, x + 80, y + 80, ROOF_Z, "Column %s%d" % ("AB"[ys.index(y)], xs.index(x) + 1))
# roof slab with a deep cantilever and a long skylight slot
RX0, RX1, RY0, RY1 = -12000.0, 12000.0, -7200.0, 7200.0
roof = rg.Brep.CreateFromBox(rg.BoundingBox(rg.Point3d(RX0, RY0, ROOF_Z), rg.Point3d(RX1, RY1, ROOF_Z + RT)))
slot = rg.Brep.CreateFromBox(rg.BoundingBox(rg.Point3d(-5400, -600, ROOF_Z - 50), rg.Point3d(5400, 600, ROOF_Z + RT + 50)))
cut = rg.Brep.CreateBooleanDifference(roof, slot, doc.ModelAbsoluteTolerance)
doc.Objects.AddBrep(cut[0] if cut else roof, attrs("PAVILION::ROOF", "Roof slab"))
# a thin fascia upstand
box("PAVILION::ROOF", RX0, RY0, ROOF_Z + RT, RX1, RY0 + 120, ROOF_Z + RT + 160, "Fascia S")
box("PAVILION::ROOF", RX0, RY1 - 120, ROOF_Z + RT, RX1, RY1, ROOF_Z + RT + 160, "Fascia N")

# service core, oak clad. FAULT 1: stops 600 mm short of the soffit
box("PAVILION::CORE", 3900, -2600, DH, 7000, 1400, ROOF_Z - 600, "Service core")

# glass room between columns 1 and 3, mullions every 1.2 m
GX0, GX1, GY0, GY1 = -7000.0, 1500.0, -3000.0, 3000.0
MW = 60.0
def mullion(x, y, along_x):
    if along_x:
        box("PAVILION::MULLIONS", x - MW / 2, y - MW / 2, DH, x + MW / 2, y + MW / 2, ROOF_Z, None)
    else:
        box("PAVILION::MULLIONS", x - MW / 2, y - MW / 2, DH, x + MW / 2, y + MW / 2, ROOF_Z, None)
def pane(x0, y0, x1, y1, name):
    p = rg.Brep.CreateFromCornerPoints(rg.Point3d(x0, y0, DH), rg.Point3d(x1, y1, DH), rg.Point3d(x1, y1, ROOF_Z),
                                       rg.Point3d(x0, y0, ROOF_Z), 1)
    doc.Objects.AddBrep(p, attrs("PAVILION::GLAZING", name))
n = int(round((GX1 - GX0) / 1200.0))
step = (GX1 - GX0) / n
for i in range(n + 1):
    x = GX0 + i * step
    mullion(x, GY0, True)
    mullion(x, GY1, True)
for i in range(n):
    x0, x1 = GX0 + i * step, GX0 + (i + 1) * step
    if i != 4:   # FAULT 2: the fifth pane on the south face is missing
        pane(x0, GY0, x1, GY0, "Glass S%d" % (i + 1))
    pane(x0, GY1, x1, GY1, "Glass N%d" % (i + 1))
m = int(round((GY1 - GY0) / 1200.0))
stp = (GY1 - GY0) / m
for j in range(m + 1):
    y = GY0 + j * stp
    mullion(GX0, y, False)
    mullion(GX1, y, False)
for j in range(m):
    y0, y1 = GY0 + j * stp, GY0 + (j + 1) * stp
    pane(GX0, y0, GX0, y1, "Glass W%d" % (j + 1))
    pane(GX1, y0, GX1, y1, "Glass E%d" % (j + 1))
# head and sill rails
box("PAVILION::MULLIONS", GX0, GY0 - MW / 2, ROOF_Z - 90, GX1, GY0 + MW / 2, ROOF_Z, "Head rail S")
box("PAVILION::MULLIONS", GX0, GY1 - MW / 2, ROOF_Z - 90, GX1, GY1 + MW / 2, ROOF_Z, "Head rail N")

# oak fin screen on the east, 40 x 240 fins at 300 centres
for i in range(22):
    y = -5400 + i * 300
    box("PAVILION::FINS", 9000 - 120, y - 20, DH, 9000 + 120, y + 20, ROOF_Z, "Fin %d" % (i + 1))

# furniture: a long walnut table and two benches inside the glass room
box("PAVILION::FURNITURE", -5800, -500, DH + 720, -600, 500, DH + 760, "Table top")
for x in (-5600, -800):
    box("PAVILION::FURNITURE", x - 40, -420, DH, x + 40, 420, DH + 720, "Table leg")
for y in (-950, 950):
    box("PAVILION::FURNITURE", -5800, y - 180, DH + 420, -600, y + 180, DH + 460, "Bench")
    for x in (-5600, -800):
        box("PAVILION::FURNITURE", x - 30, y - 150, DH, x + 30, y + 150, DH + 420, "Bench leg")

# ---------------------------------------------------------------- trees
import random
random.seed(7)
spots = [(-19000, -9000), (-24000, -2000), (-16000, 4200), (17500, -10500), (22000, -3000), (26500, 3500),
         (-30000, -16000), (31000, -15000), (-27000, -23000), (21000, -25000), (-38000, -6000), (38000, -4000)]
for (x, y) in spots:
    h = random.uniform(5200, 8200)
    r = random.uniform(2200, 3400)
    trunk = rg.Mesh.CreateFromCylinder(rg.Cylinder(rg.Circle(rg.Plane(rg.Point3d(x, y, 0), rg.Vector3d.ZAxis), 170), h * 0.55), 1, 24)
    doc.Objects.AddMesh(trunk, attrs("SITE::TRUNKS", "Tree trunk"))
    for k in range(3):
        c = rg.Point3d(x + random.uniform(-700, 700), y + random.uniform(-700, 700), h * 0.62 + k * r * 0.35)
        s = rg.Mesh.CreateFromSphere(rg.Sphere(c, r * (1.0 - 0.18 * k)), 48, 32)
        xf = rg.Transform.Scale(rg.Plane(c, rg.Vector3d.ZAxis), 1.0, 1.0, 0.8)
        s.Transform(xf)
        s.Normals.ComputeNormals()
        doc.Objects.AddMesh(s, attrs("SITE::TREES", "Tree canopy"))

# ---------------------------------------------------------------- sun, views
sun = doc.Lights.Sun
sun.Enabled = True
sun.TimeZone = 10.0
sun.Latitude = -33.87
sun.Longitude = 151.21
sun.SetDateTime(System.DateTime(2026, 3, 21, 15, 40, 0), System.DateTimeKind.Local)
doc.Lights.Sun.North = 90.0
doc.Lights.Skylight.Enabled = True
rs = doc.RenderSettings
rs.BackgroundStyle = Rhino.Display.BackgroundStyle.Gradient
rs.BackgroundColorTop = Color.FromArgb(118, 156, 204)
rs.BackgroundColorBottom = Color.FromArgb(222, 230, 238)
doc.RenderSettings = rs

def named_view(name, cam, tgt, lens):
    i = doc.NamedViews.FindByName(name)
    if i >= 0:
        doc.NamedViews.Delete(i)
    v = doc.Views.Find("Perspective", False) or doc.Views.ActiveView
    vp = v.ActiveViewport
    vp.ChangeToPerspectiveProjection(True, lens)
    vp.SetCameraLocations(tgt, cam)
    vp.Camera35mmLensLength = lens
    doc.NamedViews.Add(name, vp.Id)

named_view("Approach", rg.Point3d(-10800, -15200, 2350), rg.Point3d(-200, -300, 2050), 26)
named_view("Aerial", rg.Point3d(-26000, -30000, 21000), rg.Point3d(0, 0, 800), 32)
named_view("Deck", rg.Point3d(-13500, -9000, 2100), rg.Point3d(1500, 1800, 2200), 22)
named_view("Lake", rg.Point3d(13000, 27000, 4400), rg.Point3d(0, 0, 2000), 30)
doc.NamedViews.Restore(doc.NamedViews.FindByName("Approach"), (doc.Views.Find("Perspective", False) or doc.Views.ActiveView).ActiveViewport)
doc.Views.Redraw()
cnt = Rhino.DocObjects.ObjectEnumeratorSettings()
cnt.DeletedObjects = False

# ---------------------------------------------------------------- the glass in the film: tinted, so the missing pane reads
gm = Rhino.DocObjects.Material()
gm.Name = "Tinted glass"
gm.DiffuseColor = Color.FromArgb(96, 138, 150)
gm.Reflectivity = 0.35
gm.Transparency = 0.32
gm.Shine = Rhino.DocObjects.Material.MaxShine
gm.TransparentColor = Color.FromArgb(120, 170, 182)
gm.IndexOfRefraction = 1.52
try:
    gm.FresnelReflections = True
except Exception:
    pass
for old in [x for x in doc.RenderMaterials if x.Name == "Tinted glass"]:
    doc.RenderMaterials.Remove(old)
grm = Rhino.Render.RenderMaterial.CreateBasicMaterial(gm, doc)
grm.Name = "Tinted glass"
doc.RenderMaterials.Add(grm)
doc.Layers[LAY["PAVILION::GLAZING"]].RenderMaterial = grm

# ---------------------------------------------------------------- open on the Approach view, Rendered
pv = doc.Views.Find("Perspective", False) or doc.Views.ActiveView
doc.NamedViews.Restore(doc.NamedViews.FindByName("Approach"), pv.ActiveViewport)
pv.ActiveViewport.DisplayMode = Rhino.Display.DisplayModeDescription.GetDisplayMode(Rhino.Display.DisplayModeDescription.RenderedId)
doc.Views.ActiveView = pv
doc.Views.Redraw()
print("ClaudeMark demo built. Two faults to find: the core stops short of the roof, and pane S5 is missing. Type ClaudeMark.")
