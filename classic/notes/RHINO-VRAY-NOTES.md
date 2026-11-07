# Rhino + V-Ray scripting notes (for Claude)

ClaudeMark's presets and `tools/TOOLS.md` point here by section name. Everything below was learnt the hard way
driving Rhino 8 and V-Ray 7.1 for Rhino through the Rhino MCP. Paths are the default install locations.

## General rules for the Rhino MCP

- **The MCP's Python host is IronPython 2.7**, not CPython: no f-strings, `print` shows tuples as tuples.
- **Keep every MCP call short.** A call that runs too long times out the MCP, and can take Rhino down with it. Slow
  work becomes a job you start and then step (`job_step` in `tools/cm_analyse.py`).
- **A script that throws may undo everything it did, or keep the changes it made before the throw.** Count and diff
  the objects before re-running.
- **Check which Rhino you're driving.** With two Rhinos open, the MCP can be talking to the empty one. Compare
  `doc.Path` with the capture's `meta.json` (`doc.path`, `rhino_pid`) before writing anything.
- **IronPython's json chokes on non-ASCII layer names** ("Unable to translate bytes"). claude_mark.py and cm_core.py
  set `json.encoder.HAS_UTF8 = re.compile(r"(?!)")` at import; do the same in any MCP script that dumps layer names.
- **The default object enumerator skips hidden objects and objects on hidden layers.** A layer that reports zero
  objects may not be empty: set `ObjectEnumeratorSettings.HiddenObjects = True`.
- **Wrap bulk edits in `doc.Views.EnableRedraw(False)` / `True`.**

## rhino-viewport-camera-traps

- **`SetCameraLocations(target, camera)` takes the TARGET first.** Read `vp.CameraLocation` back after setting it.
- **`vp.GetFrustumLine(x, y)` runs FAR to NEAR:** `line.From` is on the far plane.
- **Never set `vp.Size` to get a capture resolution.** On a docked layout it resizes the user's viewports.
- **Save and restore a camera with `ViewportInfo`:** `vi = ViewportInfo(vp)`, later `vp.SetViewProjection(vi, True)`.
  `RhinoViewport` has no `SetFrustum`; build a `ViewportInfo`, set its frustum, and apply that.
- **The user orbits between messages.** Read the camera in the SAME call as any capture you'll measure on, or
  (better) use the camera ClaudeMark saved in meta.json.
- **Pass the display mode to the capture:** `view.CaptureToBitmap(size, DisplayModeDescription)`. Setting the
  viewport's mode first often doesn't reach the capture. Pen/Technical captures still come out shaded: for linework
  use `HiddenLineDrawing.Compute`.
- **Two viewports can share a name** ("Perspective" twice). Name lookups then hit the first; capture through the view
  object you actually changed.
- **A floating temp view un-maximises the user's Perspective.** Re-maximise it and check `vp.Size` afterwards.
- **A hidden Top view draws Breps as wireframe** in captures; meshes shade. Capture through a real perspective view.
- **A display clipping plane KEEPS the side its normal points to.** A hidden clipping plane still clips. An active
  clip plane in the user's view shows up in captures as a flat grey section fill over everything.
- **Named views are copies:** editing `doc.NamedViews[i].Viewport` doesn't stick. Building one in a view of a
  different aspect changes the lens; use a temp view at the original aspect (`aspect_view` in cm_core).
- **`ZoomBoundingBox` then a capture at another aspect leaves the model off-centre.** Aim at the bbox centre and set
  the distance from the camera angles instead.

## vray-render-over-mcp

V-Ray renders CAN be started from the MCP (V-Ray 7.1 for Rhino):

```python
import sys; sys.path.append(r"C:\Program Files\Chaos\V-Ray\V-Ray for Rhinoceros")
import rhVRay as vray
o = vray.Scene.Plugin("/SettingsOutput")
with vray.Scene.Transaction():
    o.Param("img_width").Value = 1280; o.Param("img_height").Value = 720
    o.Param("img_dir").Value = "C:\\...\\Renders\\"      # trailing backslash
    o.Param("img_file").Value = "name.png"
vray.Scene.Refresh()
vray.Render(vray.RenderModes.RM_PRODUCTION, vray.RenderEngines.RE_CPU, 0)   # returns at once
```

- The call returns immediately; the render runs in the VFB. Poll for the file from the shell.
- It writes `name.png`, `name.denoiser.png` and `name.effectsResult.png`.
- **It renders the ACTIVE viewport as it is right now.** Restore the named view first.
- **The UI's `save_render` / `save_render_path` do nothing for scripted renders.** Only the core `img_dir` +
  `img_file` save a file.
- **`name.png` has the VFB's colour corrections baked in.** If the user has a VFB filter preset on, the
  `.denoiser.png` is the faithful image. Say so; don't change their VFB without asking.
- **`img_dir`/`img_file` persist in the document.** Clear both once the file exists, or the user's own renders
  overwrite it. If a render was interrupted, check `/SettingsOutput` first thing next turn.
- **On a big model `rhVRay.Render` blocks during scene export** and times the MCP out. Start it from a one-shot
  WinForms timer after the MCP call returns (`_render_later` in cm_render.py).
- **The saved PNG has real alpha** (background transparent). Resizing RGBA with PIL premultiplies, so the sky turns
  black in a resized crop: `convert("RGB")` before resizing.
- **Clear glass:** a Rhino PBR material with partial opacity renders milky. Use a Custom material: transparency ~0.97,
  Fresnel reflections on, IOR 1.5.
- Routes that DON'T work: `RhinoApp.RunScript("_vrayRender")` (blocks), `run_command "_-vrayRender"` (does nothing).

## vray-lights-by-script

- `rhVRay.py` can set light params but can't create a light.
- **Mesh lights:** select the objects, then `run_command("_-vrayLightMesh _Enter")`. Every selected object joins ONE
  shared LightMesh plugin. Without the trailing `_Enter` the command sits at a prompt and the MCP times out.
  Check `RhinoApp.CommandPrompt == "Command"` afterwards.
- **Params:** `pl = rhVRay.Scene.PluginFromObject("<guid string>")` (a string, not a System.Guid), then inside
  `with rhVRay.Scene.Transaction():` set `units` (2 = cd/m²), `intensity`, `color` [r,g,b,1], `invisible`,
  `doubleSided`, `enabled`. Then `Scene.Refresh()`.
- **Deleting the Rhino objects leaves the plugin.** Disable it (`enabled = 0`); there's no delete.
- **Rectangle and spot lights:** create a Rhino light (`WorldRectangular` / `WorldSpot`) with RhinoCommon, select it,
  `run_command("_-vrayLight _Convert")`. V-Ray ignores a plain Rhino light until converted. Spots: `units` 1 (lm),
  `coneAngle` and `penumbraAngle` in radians.
- **V-Ray switches OFF lights on hidden layers** when it re-syncs. Converted Rhino lights also flood the Rendered
  viewport (huge intensity values). For many emitters, use mesh lights on a visible layer.
- To split one mesh light into zones: copy the emitters with fresh attributes, delete the originals, and run
  `_-vrayLightMesh _Enter` once per zone.

## vray-cosmos-proxies-scripting

- **Library on disk:** `Documents\Chaos Cosmos\Packages\<Category>\<Name>_<hash>\` (or under `OneDrive\Documents`
  when Documents is redirected). Each package has its `.vrmesh`/`.vrmat`/`.exr` and a `Previews\` folder.
- **Placing an asset:** `run_command("-_vrayCosmos 1 <full package UUID>\nwX,Y,Z\nwX,Y,Z...")`. The `w` prefix makes
  the points world coordinates whatever the CPlane. Assets arrive at true size. The command keeps prompting for
  more points, so the MCP call times out; that's harmless, but Esc must be sent to Rhino's main window afterwards
  (`PostMessage` WM_KEYDOWN VK_ESCAPE from PowerShell), then check `RhinoApp.CommandPrompt == "Command"`.
- **Get the full UUID** from the local Cosmos browser (`http://127.0.0.1:30305/`, address in
  `%LOCALAPPDATA%\Chaos\Cosmos\runtime.json`). A short folder hash fails ("Unable to read the import details").
- **Downloading:** use the Download button in the Cosmos browser. Ask the user before downloading anything (name and
  size first): some assets cost credits or need a paid plan.
- **Re-importing an asset that's already in the model re-creates every existing copy** with new serial numbers. Never
  clean up "strays" by serial number or name after an import: take only the object at your own insertion point.
- **A Cosmos proxy** is a MeshObject; after a transform its `Attributes.UserData.Count == 2`.
- `-_vrayImportMaterial` loads single-material `.vrmat` files but silently skips multi-materials (plants).
- Fresh imports face −Y.

## vray-settings-by-vropt

- **A `.vropt` is plain XML.** `rhVRay.Scene.SaveSettings(path)` writes the full settings; edit a copy and
  `Scene.LoadSettings(path)` to change many settings at once (`Vropt` in `tools/cm_style.py`).
- **LoadSettings MERGES:** plugins the file doesn't name stay in the scene.
- **The VFB layer stack (curves, LUTs, filmic) can't be set by script.** A look goes out as a `.cube` LUT instead.
- **The doc sun's V-Ray params are writable** via `Scene.Plugin("/Rhino Document Sun")` in a Transaction:
  `sky_model` (0 Preetham, 1 CIE Clear, 2 Overcast, 3 Hosek, 4 PRG Clear, 5 PRG Clear New), `turbidity`,
  `intensity_multiplier`, `size_multiplier`, clouds. Values come back as .NET Single: `float()` them before json.
- **A sun change reaches V-Ray on Rhino's next idle:** set it in one MCP call, render in the next.
- **Haze (`VolumeAerialPerspective.distance`) is in model units:** 300 in a mm model is a 0.3 m white-out.
- **CameraPhysical:** `shutter_speed` is the 1/s value (lower = brighter); EV100 at ISO 100 gives
  `shutter = 2^EV / f^2`. `white_balance` is the colour that becomes white.
- **NEVER assign a plugin LIST through rhVRay** (e.g. the fog's `lights`): it crashed Rhino with an access
  violation. Put lists in a .vropt. SaveSettings writes the fog's light list back empty, so re-add it.
- **V-Ray only honours a shifted frustum on a view flagged two-point.**
- `use_dof` set by script did nothing in the render.
- Rough EV100 with V-Ray's physical sky: clear noon 15, golden hour 12-13, blue hour 8.
- V-Ray already has **Light Gen** (`vrayShowLightGen`) and **batch render** (`vrayBatchRender`).

## section-perspective-pipeline

How the "Section along the line" analysis and section drawings are made:

- **Camera:** a one-point perspective square to the cut plane, set back on its normal at eye height between floors.
  At the cut, pixels per metre S = F / D with F = (H/2) · lens / 12; for 1:100 at 300 dpi, S = 118.1 px/m.
- **Clip without touching the user's plane:** add a temporary hidden clipping plane that clips only the spare
  viewport, and hide their own plane, curves, dots and annotations for the capture; show them again after.
- **The cut as vectors:** `Intersection.BrepPlane` / `MeshPlane` on the objects whose bounding box crosses the plane.
  Style by layer: concrete solid poché, steel black, masonry hatched, glass broken line.
- **Linework beyond:** `HiddenLineDrawing` (the Make2D engine) through the plane, graded by depth (darker near the
  cut). **The HLD keeps the side away from the clip plane's normal:** pass `Plane(origin, -normal)`. Run big HLDs on
  a background thread and write the segments to a file, or the MCP times out.
- **XML-escape every label** in the SVG (a bare `&` breaks it).
- **PDF/preview without pandoc:** print HTML with headless Edge
  (`msedge --headless=new --print-to-pdf=... file.html` or `--screenshot`).
